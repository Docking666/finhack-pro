"""
回测运行器

支持通过HTTP API调用Rust回测引擎，也支持纯Python回测(当Rust引擎不可用时)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger
from rich.console import Console
from rich.table import Table

from finhack_pro.strategies.base import (
    BarData,
    BaseStrategy,
    Context,
    OrderData,
    Portfolio,
    Signal,
    SignalDirection,
    TradeData,
)
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class BacktestResult:
    """回测结果

    Attributes:
        strategy_name: 策略名称
        symbol: 标的代码
        start_date: 开始日期
        end_date: 结束日期
        initial_capital: 初始资金
        final_capital: 最终资金
        total_return: 总收益率
        annual_return: 年化收益率
        max_drawdown: 最大回撤
        sharpe_ratio: 夏普比率
        win_rate: 胜率
        profit_loss_ratio: 盈亏比
        total_trades: 总交易次数
        winning_trades: 盈利交易次数
        losing_trades: 亏损交易次数
        trades: 交易记录列表
        daily_returns: 日收益率序列
        equity_curve: 权益曲线
    """
    strategy_name: str = ""
    symbol: str = ""
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 1_000_000.0
    final_capital: float = 1_000_000.0
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    trades: List[Dict[str, Any]] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)


class BacktestRunner:
    """回测运行器

    支持两种回测模式:
    1. Rust引擎模式: 通过HTTP API调用Rust高性能回测引擎
    2. Python模式: 纯Python回测(当Rust引擎不可用时自动切换)

    Usage:
        runner = BacktestRunner()
        result = runner.run(
            strategy=DualThrustStrategy(),
            symbol="600519.SH",
            data=df,
            initial_capital=1_000_000,
        )
        runner.print_report(result)
    """

    def __init__(
        self,
        rust_api_url: str = "http://localhost:8080",
        rust_api_key: str = "",
        timeout: int = 300,
    ) -> None:
        """初始化回测运行器

        Args:
            rust_api_url: Rust引擎API地址
            rust_api_key: API密钥
            timeout: 请求超时(秒)
        """
        self.rust_api_url = rust_api_url
        self.rust_api_key = rust_api_key
        self.timeout = timeout
        self._console = Console()
        self._rust_available = False

    async def check_rust_engine(self) -> bool:
        """检查Rust引擎是否可用

        Returns:
            Rust引擎是否可用
        """
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.rust_api_url}/health")
                self._rust_available = resp.status_code == 200
                logger.info(f"Rust引擎状态: {'可用' if self._rust_available else '不可用'}")
                return self._rust_available
        except Exception:
            self._rust_available = False
            logger.info("Rust引擎不可用，使用Python回测模式")
            return False

    # BarData.extra 可选列（差异化策略依赖；数据框含这些列时自动注入）
    _EXTRA_COLUMNS = ("volume_ratio", "ma20", "rsi", "macd_signal", "turnover", "market_cap", "net_inflow")

    @classmethod
    def _extract_bar_extra(cls, row: pd.Series) -> Dict[str, Any]:
        """从数据行提取可选扩展字段注入 BarData.extra（NaN/缺失列安全跳过）"""
        extra: Dict[str, Any] = {}
        for col in cls._EXTRA_COLUMNS:
            if col in row.index:
                val = row[col]
                if val is not None and not (isinstance(val, float) and val != val):  # 非 NaN
                    extra[col] = float(val) if isinstance(val, (int, float)) else val
        return extra

    def run(
        self,
        strategy: BaseStrategy,
        symbol: str,
        data: pd.DataFrame,
        initial_capital: float = 1_000_000.0,
        commission_rate: float = 0.0003,
        stamp_tax_rate: float = 0.001,
        slippage: float = 0.001,
        params: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> BacktestResult:
        """运行回测

        Args:
            strategy: 策略实例
            symbol: 标的代码
            data: OHLCV DataFrame
            initial_capital: 初始资金
            commission_rate: 佣金费率
            stamp_tax_rate: 印花税率
            slippage: 滑点
            params: 策略参数

        Returns:
            BacktestResult 回测结果
        """
        if data.empty:
            logger.error("回测数据为空")
            return BacktestResult()

        logger.info(
            f"开始回测: {strategy.strategy_name} @ {symbol}, "
            f"数据范围: {data['date'].iloc[0]} ~ {data['date'].iloc[-1]}, "
            f"初始资金: {initial_capital:,.0f}"
        )

        # 使用Python回测引擎
        return self._run_python_backtest(
            strategy=strategy,
            symbol=symbol,
            data=data,
            initial_capital=initial_capital,
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            slippage=slippage,
            params=params,
            progress_callback=progress_callback,
        )

    def _run_python_backtest(
        self,
        strategy: BaseStrategy,
        symbol: str,
        data: pd.DataFrame,
        initial_capital: float,
        commission_rate: float,
        stamp_tax_rate: float,
        slippage: float,
        params: Optional[Dict[str, Any]],
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> BacktestResult:
        """纯Python回测引擎

        Args:
            strategy: 策略实例
            symbol: 标的代码
            data: OHLCV DataFrame
            initial_capital: 初始资金
            commission_rate: 佣金费率
            stamp_tax_rate: 印花税率
            slippage: 滑点
            params: 策略参数

        Returns:
            BacktestResult
        """
        # 初始化组合
        portfolio = Portfolio(cash=initial_capital, total_value=initial_capital)
        position_volume = 0  # 持仓数量
        position_cost = 0.0  # 持仓成本
        peak_value = initial_capital
        max_drawdown = 0.0

        # 初始化策略
        context = Context(
            portfolio=portfolio,
            params=params or {},
            config={"symbol": symbol},
        )
        strategy.on_init(context)

        # 交易记录
        trades: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        daily_returns: List[float] = []

        prev_value = initial_capital

        # 逐K线回测
        total_bars = len(data)
        progress_step = max(total_bars // 100, 1)  # 每 1% 回调一次进度
        for idx, row in data.iterrows():
            bar_date = row["date"]
            if isinstance(bar_date, str):
                bar_date = pd.to_datetime(bar_date)

            if progress_callback and idx % progress_step == 0:
                progress_callback(int(idx / total_bars * 100), str(bar_date))

            bar = BarData(
                symbol=symbol,
                datetime=bar_date,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                extra=self._extract_bar_extra(row),
            )

            context.current_time = bar_date

            # 处理策略信号
            signals = strategy.on_bar(context, bar)

            for signal in signals:
                if signal.direction == SignalDirection.BUY and position_volume == 0:
                    # 买入
                    price = bar.close * (1 + slippage)
                    volume = int(portfolio.cash * 0.9 / price / 100) * 100
                    if volume >= 100:
                        cost = volume * price
                        commission = max(cost * commission_rate, 5.0)
                        total_cost = cost + commission

                        if total_cost <= portfolio.cash:
                            portfolio.cash -= total_cost
                            position_volume = volume
                            position_cost = price
                            trades.append({
                                "date": str(bar_date),
                                "action": "buy",
                                "price": round(price, 2),
                                "volume": volume,
                                "commission": round(commission, 2),
                            })

                elif signal.direction == SignalDirection.SELL and position_volume > 0:
                    # 卖出
                    price = bar.close * (1 - slippage)
                    revenue = position_volume * price
                    commission = max(revenue * commission_rate, 5.0)
                    stamp_tax = revenue * stamp_tax_rate
                    net_revenue = revenue - commission - stamp_tax

                    pnl = net_revenue - position_volume * position_cost
                    portfolio.cash += net_revenue
                    trades.append({
                        "date": str(bar_date),
                        "action": "sell",
                        "price": round(price, 2),
                        "volume": position_volume,
                        "commission": round(commission + stamp_tax, 2),
                        "pnl": round(pnl, 2),
                    })
                    position_volume = 0
                    position_cost = 0.0

            # 止损止盈检查
            if position_volume > 0:
                current_price = bar.close
                # 简单止损止盈(基于信号中的设置)
                # 这里不做额外处理，由策略自身管理

            # 更新组合价值
            current_value = portfolio.cash + position_volume * bar.close
            portfolio.total_value = current_value

            # 计算回撤
            if current_value > peak_value:
                peak_value = current_value
            drawdown = (peak_value - current_value) / peak_value
            if drawdown > max_drawdown:
                max_drawdown = drawdown

            # 记录权益曲线
            equity_curve.append({
                "date": str(bar_date),
                "equity": round(current_value, 2),
                "cash": round(portfolio.cash, 2),
                "position_value": round(position_volume * bar.close, 2),
            })

            # 计算日收益率
            daily_return = (current_value - prev_value) / prev_value if prev_value > 0 else 0
            daily_returns.append(daily_return)
            prev_value = current_value

        # 策略结束回调
        strategy.on_finish(context)

        # 计算回测统计
        final_value = portfolio.cash + position_volume * data.iloc[-1]["close"]
        total_return = (final_value - initial_capital) / initial_capital

        # 年化收益率
        if len(data) > 1:
            start_date = pd.to_datetime(data["date"].iloc[0])
            end_date = pd.to_datetime(data["date"].iloc[-1])
            years = (end_date - start_date).days / 365.25
            annual_return = (1 + total_return) ** (1 / max(years, 0.01)) - 1
        else:
            annual_return = total_return

        # 夏普比率
        if daily_returns:
            avg_return = np.mean(daily_returns)
            std_return = np.std(daily_returns)
            sharpe_ratio = (avg_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        # 胜率和盈亏比
        sell_trades = [t for t in trades if t["action"] == "sell" and "pnl" in t]
        winning = [t for t in sell_trades if t["pnl"] > 0]
        losing = [t for t in sell_trades if t["pnl"] <= 0]
        win_rate = len(winning) / len(sell_trades) if sell_trades else 0

        avg_win = np.mean([t["pnl"] for t in winning]) if winning else 0
        avg_loss = abs(np.mean([t["pnl"] for t in losing])) if losing else 1
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        result = BacktestResult(
            strategy_name=strategy.strategy_name,
            symbol=symbol,
            start_date=str(data["date"].iloc[0]),
            end_date=str(data["date"].iloc[-1]),
            initial_capital=initial_capital,
            final_capital=round(final_value, 2),
            total_return=round(total_return, 4),
            annual_return=round(annual_return, 4),
            max_drawdown=round(max_drawdown, 4),
            sharpe_ratio=round(sharpe_ratio, 4),
            win_rate=round(win_rate, 4),
            profit_loss_ratio=round(profit_loss_ratio, 4),
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            trades=trades,
            daily_returns=[round(r, 6) for r in daily_returns],
            equity_curve=equity_curve,
        )

        logger.info(
            f"回测完成: 总收益率={total_return:.2%}, "
            f"最大回撤={max_drawdown:.2%}, 夏普比率={sharpe_ratio:.2f}, "
            f"胜率={win_rate:.2%}, 交易次数={len(trades)}"
        )

        return result

    def print_report(self, result: BacktestResult) -> None:
        """打印回测报告

        Args:
            result: 回测结果
        """
        console = self._console

        # 基本信息表格
        table = Table(title="回测报告", show_lines=True)
        table.add_column("指标", style="cyan", width=20)
        table.add_column("值", style="green", width=20)

        table.add_row("策略名称", result.strategy_name)
        table.add_row("标的", result.symbol)
        table.add_row("回测区间", f"{result.start_date} ~ {result.end_date}")
        table.add_row("初始资金", f"{result.initial_capital:,.2f}")
        table.add_row("最终资金", f"{result.final_capital:,.2f}")
        table.add_row("总收益率", f"{result.total_return:.2%}")
        table.add_row("年化收益率", f"{result.annual_return:.2%}")
        table.add_row("最大回撤", f"{result.max_drawdown:.2%}")
        table.add_row("夏普比率", f"{result.sharpe_ratio:.2f}")
        table.add_row("胜率", f"{result.win_rate:.2%}")
        table.add_row("盈亏比", f"{result.profit_loss_ratio:.2f}")
        table.add_row("总交易次数", str(result.total_trades))
        table.add_row("盈利次数", str(result.winning_trades))
        table.add_row("亏损次数", str(result.losing_trades))

        console.print(table)

        # 交易记录(最近10笔)
        if result.trades:
            trade_table = Table(title="最近交易记录", show_lines=True)
            trade_table.add_column("日期", width=12)
            trade_table.add_column("方向", width=6)
            trade_table.add_column("价格", width=10)
            trade_table.add_column("数量", width=8)
            trade_table.add_column("盈亏", width=10)

            for trade in result.trades[-10:]:
                pnl_str = f"{trade.get('pnl', 0):,.2f}" if "pnl" in trade else "-"
                trade_table.add_row(
                    trade["date"],
                    trade["action"],
                    f"{trade['price']:.2f}",
                    str(trade["volume"]),
                    pnl_str,
                )

            console.print(trade_table)

    def save_report(
        self,
        result: BacktestResult,
        output_path: str = "backtest_report.json",
    ) -> None:
        """保存回测报告到JSON文件

        Args:
            result: 回测结果
            output_path: 输出文件路径
        """
        report = {
            "strategy_name": result.strategy_name,
            "symbol": result.symbol,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_return": result.total_return,
            "annual_return": result.annual_return,
            "max_drawdown": result.max_drawdown,
            "sharpe_ratio": result.sharpe_ratio,
            "win_rate": result.win_rate,
            "profit_loss_ratio": result.profit_loss_ratio,
            "total_trades": result.total_trades,
            "winning_trades": result.winning_trades,
            "losing_trades": result.losing_trades,
            "trades": result.trades,
            "equity_curve": result.equity_curve,
        }

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"回测报告已保存: {output_path}")

    @staticmethod
    def load_strategy(name: str) -> BaseStrategy:
        """加载策略实例（内置或工坊保存的自有策略）

        Args:
            name: 策略名称。内置（dual_thrust/momentum/mean_reversion/ml_strategy）
                  或 data/generated_strategies/{name}/ 下工坊保存的策略（strategy.py）

        Returns:
            策略实例

        Raises:
            ValueError: 未知策略或自定义策略加载失败
        """
        strategies: Dict[str, type] = {
            "dual_thrust": __import__(
                "finhack_pro.strategies.dual_thrust", fromlist=["DualThrustStrategy"]
            ).DualThrustStrategy,
            "momentum": __import__(
                "finhack_pro.strategies.momentum", fromlist=["MomentumStrategy"]
            ).MomentumStrategy,
            "mean_reversion": __import__(
                "finhack_pro.strategies.mean_reversion", fromlist=["MeanReversionStrategy"]
            ).MeanReversionStrategy,
            "ml_strategy": __import__(
                "finhack_pro.strategies.ml_strategy", fromlist=["MLStrategy"]
            ).MLStrategy,
        }

        strategy_cls = strategies.get(name.lower())
        if strategy_cls:
            return strategy_cls()

        # 差异化策略（README Niche Strategy Framework："机构做广度，个人做深度"）
        niche_types = {
            "micro_cap", "event_driven", "sentiment_reversal",
            "dragon_tiger_follow", "alternative_cross",
        }
        if name.lower() in niche_types:
            from finhack_pro.strategies.niche_strategy import create_niche_strategy
            strategy = create_niche_strategy(name.lower())
            logger.info(f"加载差异化策略: {name} -> {strategy.config.niche_type.value}")
            return strategy

        # 自定义策略：工坊保存到 data/generated_strategies/{name}/strategy.py
        # （生成代码继承 BaseStrategy 并实现 on_bar，与内置策略接口一致）
        gen_dir = Path("data/generated_strategies") / name
        strategy_file = gen_dir / "strategy.py"
        if strategy_file.exists():
            namespace: Dict[str, Any] = {}
            try:
                code = strategy_file.read_text(encoding="utf-8")
                exec(compile(code, str(strategy_file), "exec"), namespace)
            except Exception as e:
                raise ValueError(f"自定义策略 {name} 加载失败: {e}")
            cls = None
            for value in namespace.values():
                if (
                    isinstance(value, type)
                    and issubclass(value, BaseStrategy)
                    and value is not BaseStrategy
                ):
                    cls = value
                    break
            if cls is None:
                raise ValueError(f"自定义策略 {name} 未定义 BaseStrategy 子类")
            logger.info(f"加载自定义策略: {name} -> {cls.__name__}")
            return cls()

        raise ValueError(
            f"未知策略: {name}, 可用策略: {list(strategies.keys())}, "
            f"或 data/generated_strategies/ 下已保存的自有策略"
        )
