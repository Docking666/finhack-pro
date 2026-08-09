"""
向量化回测引擎 - Vectorized Backtest Engine

高性能回测模式，在现有逐K线循环基础上增加轻量级时间切片保护。

特点:
- 性能接近原始回测（额外开销 < 5%）
- 通过 TimeSliceContext 提供安全数据访问
- 可选的未来函数检测（strict模式）
- 兼容现有 BaseStrategy 接口
- 与异步事件驱动引擎共享统一接口
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from finhack_pro.backtest.time_slice import (
    BacktestMode,
    DataBarrier,
    EngineResult,
    EngineSnapshot,
    LatencyConfig,
    PortfolioSnapshot,
    TimeSliceContext,
)
from finhack_pro.strategies.base import (
    BarData,
    BaseStrategy,
    Context,
    Portfolio,
    Signal,
    SignalDirection,
)
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class VectorizedEngineConfig:
    """向量化引擎配置"""
    commission_rate: float = 0.0003     # 佣金万三
    stamp_tax_rate: float = 0.001       # 印花税千一（仅卖出）
    slippage: float = 0.001             # 滑点
    initial_capital: float = 1_000_000.0
    time_column: str = "date"           # 时间列名
    strict_mode: bool = True            # 严格模式：检测到未来函数时抛异常
    enable_time_slice: bool = True      # 启用时间切片保护
    enable_data_barrier: bool = False   # 启用数据屏障（额外开销）

    # 精度约束（A股交易规则，默认关闭保持向后兼容）
    enable_limit_up_down: bool = False  # 涨跌停约束：涨停拒买/跌停拒卖
    enable_t1: bool = False             # T+1 规则：当日买入次日才可卖
    enable_suspension: bool = False     # 停牌处理：停牌 bar 跳过成交
    enable_price_tick: bool = False     # 最小变动价位 0.01
    slippage_model: str = "fixed"       # 滑点模型: fixed / volume_proportional / impact_cost


class VectorizedEngine:
    """向量化回测引擎
    
    高性能模式，在现有回测逻辑基础上增加时间切片保护。
    
    Usage:
        engine = VectorizedEngine(config)
        result = engine.run(strategy, symbol, data)
    """
    
    def __init__(self, config: Optional[VectorizedEngineConfig] = None):
        self.config = config or VectorizedEngineConfig()
        # 撮合精度约束闸门
        from finhack_pro.backtest.execution import ExecutionConfig, ExecutionGate
        self._gate = ExecutionGate(ExecutionConfig(
            enable_limit_up_down=self.config.enable_limit_up_down,
            enable_t1=self.config.enable_t1,
            enable_suspension=self.config.enable_suspension,
            enable_price_tick=self.config.enable_price_tick,
            slippage_model=self.config.slippage_model,
            slippage=self.config.slippage,
        ))
    
    def run(
        self,
        strategy: BaseStrategy,
        symbol: str,
        data: pd.DataFrame,
        params: Optional[Dict[str, Any]] = None,
    ) -> EngineResult:
        """运行回测
        
        Args:
            strategy: 策略实例
            symbol: 标的代码
            data: OHLCV DataFrame
            params: 策略参数
            
        Returns:
            EngineResult 回测结果
        """
        start_time = time.time()
        look_ahead_warnings = 0
        
        logger.info(
            f"[VectorizedEngine] 开始回测: {strategy.strategy_name} | "
            f"{symbol} | {len(data)} bars | "
            f"strict={self.config.strict_mode} | "
            f"time_slice={self.config.enable_time_slice}"
        )
        
        # 初始化
        portfolio = Portfolio(cash=self.config.initial_capital)
        context = Context(
            portfolio=portfolio,
            config=params or {},
        )
        
        # 设置策略参数
        if params:
            strategy.set_parameters(params)
        
        # 初始化策略
        strategy.on_init(context)
        
        # 预计算时间序列（O(N) 一次），供每根 bar 的 DataBarrier 复用，
        # 避免逐 bar to_datetime 造成 O(N²) 总开销
        if self.config.time_column in data.columns:
            time_array = pd.to_datetime(data[self.config.time_column]).to_numpy()
        else:
            time_array = pd.to_datetime(data.index).to_numpy()
        
        # 运行回测循环
        trades: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        daily_returns: List[float] = []
        position_volume = 0
        position_cost = 0.0
        peak_value = self.config.initial_capital
        max_drawdown = 0.0
        
        prev_date = None
        
        for idx, row in data.iterrows():
            # 解析时间
            if self.config.time_column in row.index:
                bar_date = pd.to_datetime(row[self.config.time_column])
            elif isinstance(idx, pd.Timestamp):
                bar_date = idx
            else:
                bar_date = pd.to_datetime(idx)
            
            context.current_time = bar_date.to_pydatetime()
            
            # 构造 BarData（extra 携带涨跌停/停牌/ST 标记，供撮合约束使用）
            bar_extra: Dict[str, Any] = {}
            for key in ("pre_close", "limit_pct", "is_st", "trading_status"):
                if key in row.index and not pd.isna(row[key]):
                    bar_extra[key] = row[key]
            bar = BarData(
                symbol=symbol,
                datetime=bar_date.to_pydatetime(),
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=float(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
                extra=bar_extra,
            )
            
            # === 时间切片保护 ===
            if self.config.enable_time_slice:
                # 创建时间切片上下文。
                # DataBarrier 使用 lazy 二分定位（O(log N)），
                # 避免逐 bar 对 DataFrame 做 O(N) 物理切片导致的 O(N²) 总复杂度。
                barrier = DataBarrier(
                    data=data,
                    cutoff_time=bar_date,
                    time_column=self.config.time_column,
                    strict=self.config.strict_mode,
                    lazy=True,
                    time_array=time_array,
                )
                
                ts_context = TimeSliceContext(
                    data_barrier=barrier,
                    current_time=bar_date.to_pydatetime(),
                    portfolio_snapshot=PortfolioSnapshot(
                        cash=portfolio.cash,
                        positions=copy.deepcopy(portfolio.positions) if portfolio.positions else {},
                        total_value=portfolio.total_value,
                        daily_pnl=portfolio.daily_pnl,
                        total_pnl=portfolio.total_pnl,
                        timestamp=bar_date.to_pydatetime(),
                    ),
                )
                
                # 将时间切片上下文注入到 context.data_feed
                context.data_feed = ts_context
            
            # 调用策略
            try:
                signals = strategy.on_bar(context, bar)
                if signals is None:
                    signals = []
            except Exception as e:
                from finhack_pro.backtest.time_slice import LookAheadError
                if isinstance(e, LookAheadError):
                    look_ahead_warnings += 1
                    logger.error(f"[VectorizedEngine] 未来函数检测: {e}")
                    if self.config.strict_mode:
                        raise
                    signals = []
                else:
                    logger.error(f"[VectorizedEngine] 策略异常: {e}")
                    signals = []
            
            # 处理信号
            for signal in signals:
                if not isinstance(signal, Signal):
                    continue
                
                trade = self._execute_signal(
                    signal=signal,
                    bar=bar,
                    portfolio=portfolio,
                    position_volume=position_volume,
                    position_cost=position_cost,
                    symbol=symbol,
                )
                
                if trade:
                    trades.append(trade)
                    position_volume = trade.get("position_volume", position_volume)
                    position_cost = trade.get("position_cost", position_cost)
            
            # 更新组合价值
            current_value = portfolio.cash + position_volume * bar.close
            portfolio.total_value = current_value
            portfolio.daily_pnl = current_value - self.config.initial_capital
            portfolio.total_pnl = current_value - self.config.initial_capital
            
            # 计算回撤
            if current_value > peak_value:
                peak_value = current_value
            drawdown = (peak_value - current_value) / peak_value if peak_value > 0 else 0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            
            # 记录权益曲线
            equity_curve.append({
                "datetime": bar_date.isoformat(),
                "equity": current_value,
                "cash": portfolio.cash,
                "position_value": position_volume * bar.close,
                "drawdown": drawdown,
            })
            
            # 记录日收益率
            if prev_date and bar_date.date() != prev_date.date():
                if len(equity_curve) >= 2:
                    prev_equity = equity_curve[-2]["equity"]
                    daily_return = (current_value - prev_equity) / prev_equity if prev_equity > 0 else 0
                    daily_returns.append(daily_return)
            prev_date = bar_date
        
        # 策略结束
        strategy.on_finish(context)
        
        # 计算统计指标
        execution_time = time.time() - start_time
        result = self._calculate_result(
            strategy=strategy,
            symbol=symbol,
            data=data,
            portfolio=portfolio,
            trades=trades,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            max_drawdown=max_drawdown,
            execution_time=execution_time,
            look_ahead_warnings=look_ahead_warnings,
        )
        
        logger.info(
            f"[VectorizedEngine] 回测完成: "
            f"收益={result.total_return:.2%} | "
            f"夏普={result.sharpe_ratio:.2f} | "
            f"最大回撤={result.max_drawdown:.2%} | "
            f"交易={result.total_trades}次 | "
            f"耗时={execution_time:.2f}s | "
            f"未来函数警告={look_ahead_warnings}"
        )
        
        return result
    
    def _execute_signal(
        self,
        signal: Signal,
        bar: BarData,
        portfolio: Portfolio,
        position_volume: int,
        position_cost: float,
        symbol: str,
    ) -> Optional[Dict[str, Any]]:
        """执行交易信号（带撮合精度约束）"""
        trade = None

        position = portfolio.positions.get(symbol, {"volume": position_volume, "cost": position_cost, "pnl": 0.0})

        if signal.direction == SignalDirection.BUY:
            fill = self._gate.check_and_fill(
                bar=bar,
                signal=signal,
                position=position,
                cash=portfolio.cash,
                direction=SignalDirection.BUY,
            )
            if not fill.executed:
                logger.debug(f"[VectorizedEngine] 买入被拒({fill.reject_reason}): {symbol} {bar.datetime}")
                return None

            volume = fill.fill_volume
            price = fill.fill_price
            cost = volume * price
            commission = cost * self.config.commission_rate
            portfolio.cash -= (cost + commission)
            new_volume = position_volume + volume
            position_cost = (position_cost * position_volume + cost) / new_volume
            position_volume = new_volume

            # 更新持仓（T+1: 记录可卖日期 = 次日）
            position_entry = {
                "volume": position_volume,
                "cost": position_cost,
                "pnl": 0.0,
            }
            if self.config.enable_t1:
                next_day = bar.datetime.date() + timedelta(days=1)
                position_entry["available_date"] = next_day
            portfolio.positions[symbol] = position_entry

            trade = {
                "datetime": bar.datetime.isoformat(),
                "symbol": symbol,
                "direction": "buy",
                "price": price,
                "volume": volume,
                "commission": commission,
                "position_volume": position_volume,
                "position_cost": position_cost,
                "cash_after": portfolio.cash,
                "reject_reason": None,
            }

        elif signal.direction == SignalDirection.SELL:
            if position_volume > 0:
                fill = self._gate.check_and_fill(
                    bar=bar,
                    signal=signal,
                    position=position,
                    cash=portfolio.cash,
                    direction=SignalDirection.SELL,
                )
                if not fill.executed:
                    logger.debug(f"[VectorizedEngine] 卖出被拒({fill.reject_reason}): {symbol} {bar.datetime}")
                    return None

                price = fill.fill_price
                volume = fill.fill_volume
                revenue = volume * price
                commission = revenue * self.config.commission_rate
                stamp_tax = revenue * self.config.stamp_tax_rate
                pnl = revenue - volume * position_cost - commission - stamp_tax

                portfolio.cash += (revenue - commission - stamp_tax)

                trade = {
                    "datetime": bar.datetime.isoformat(),
                    "symbol": symbol,
                    "direction": "sell",
                    "price": price,
                    "volume": volume,
                    "commission": commission + stamp_tax,
                    "pnl": pnl,
                    "position_volume": position_volume - volume,
                    "position_cost": position_cost,
                    "cash_after": portfolio.cash,
                    "reject_reason": None,
                }

                position_volume -= volume
                if position_volume <= 0:
                    position_volume = 0
                    position_cost = 0.0
                    portfolio.positions[symbol] = {"volume": 0, "cost": 0.0, "pnl": pnl}
                else:
                    portfolio.positions[symbol] = {"volume": position_volume, "cost": position_cost, "pnl": 0.0}

        return trade
    
    def _calculate_result(
        self,
        strategy: BaseStrategy,
        symbol: str,
        data: pd.DataFrame,
        portfolio: Portfolio,
        trades: List[Dict[str, Any]],
        equity_curve: List[Dict[str, Any]],
        daily_returns: List[float],
        max_drawdown: float,
        execution_time: float,
        look_ahead_warnings: int,
    ) -> EngineResult:
        """计算回测结果"""
        final_value = portfolio.total_value
        total_return = (final_value - self.config.initial_capital) / self.config.initial_capital
        
        # 年化收益
        if len(data) > 1:
            time_column = self.config.time_column
            if time_column in data.columns:
                dates = pd.to_datetime(data[time_column])
            else:
                dates = pd.to_datetime(data.index)
            trading_days = (dates.max() - dates.min()).days
            annual_return = (1 + total_return) ** (365 / max(trading_days, 1)) - 1
        else:
            annual_return = total_return
        
        # 夏普比率（Rust 加速优先，NumPy 回退）
        sharpe = 0.0
        if len(daily_returns) > 1:
            from finhack_pro.backtest.rust_accelerator import sharpe_ratio
            sharpe = sharpe_ratio(np.array(daily_returns, dtype=np.float64))
        
        # 胜率、盈亏比
        sell_trades = [t for t in trades if t.get("direction") == "sell"]
        winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
        losing = [t for t in sell_trades if t.get("pnl", 0) <= 0]
        win_rate = len(winning) / len(sell_trades) if sell_trades else 0
        
        total_winning_pnl = sum(t.get("pnl", 0) for t in winning)
        total_losing_pnl = abs(sum(t.get("pnl", 0) for t in losing))
        profit_loss_ratio = total_winning_pnl / total_losing_pnl if total_losing_pnl > 0 else 0
        
        # 日期范围
        if self.config.time_column in data.columns:
            start_date = str(pd.to_datetime(data[self.config.time_column].iloc[0]).date())
            end_date = str(pd.to_datetime(data[self.config.time_column].iloc[-1]).date())
        else:
            start_date = str(data.index[0].date()) if hasattr(data.index[0], 'date') else ""
            end_date = str(data.index[-1].date()) if hasattr(data.index[-1], 'date') else ""
        
        return EngineResult(
            mode=BacktestMode.VECTORIZED,
            strategy_name=strategy.strategy_name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self.config.initial_capital,
            final_capital=final_value,
            total_return=total_return,
            annual_return=annual_return,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            profit_loss_ratio=profit_loss_ratio,
            total_trades=len(sell_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            trades=trades,
            daily_returns=daily_returns,
            equity_curve=equity_curve,
            execution_time_seconds=execution_time,
            look_ahead_warnings=look_ahead_warnings,
            metadata={
                "engine": "vectorized",
                "strict_mode": self.config.strict_mode,
                "time_slice_enabled": self.config.enable_time_slice,
            },
        )


# 需要导入 copy
import copy
