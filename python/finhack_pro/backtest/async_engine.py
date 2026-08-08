"""
异步事件驱动回测引擎 - Async Event-Driven Backtest Engine

严格时间隔离的回测模式，通过事件驱动+不可变消息传递彻底消除未来函数。

特点:
- 完整延迟模拟（数据延迟+计算延迟+下单延迟+撮合延迟）
- 不可变状态快照，每个时刻独立
- 事件溯源，完整决策记录
- 信号时刻与成交时刻分离（消除未来函数关键）
- 因果顺序保证
- 依赖 Rust 核心弥补性能开销

架构:
  行情回放 → 数据切片 → 策略计算 → 延迟模拟 → 撮合执行 → 状态快照
"""

from __future__ import annotations

import asyncio
import copy
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
    LatencySimulator,
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
class AsyncEngineConfig:
    """异步引擎配置"""
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage: float = 0.001
    initial_capital: float = 1_000_000.0
    time_column: str = "date"
    
    # 延迟配置
    latency: LatencyConfig = field(default_factory=LatencyConfig)
    
    # 性能配置
    batch_size: int = 1              # 批量处理大小（1=逐bar，>1=批量）
    max_concurrent_symbols: int = 1  # 最大并发标的数
    
    # 状态管理
    save_snapshots: bool = True       # 是否保存完整状态快照
    snapshot_interval: int = 1        # 快照保存间隔（每N个bar保存一次）


# ============================================================
# 事件消息（不可变）
# ============================================================

@dataclass(frozen=True)
class BarEvent:
    """行情事件"""
    timestamp: datetime
    bar: BarData
    bar_index: int
    data_barrier: DataBarrier  # 包含截止到当前bar的数据切片


@dataclass(frozen=True)
class SignalEvent:
    """信号事件"""
    timestamp: datetime
    signals: tuple  # tuple of Signal (不可变)


@dataclass(frozen=True)
class FillEvent:
    """成交事件"""
    timestamp: datetime        # 成交时间（非信号时间）
    signal_time: datetime     # 信号时间
    symbol: str
    direction: str
    price: float
    volume: int
    commission: float
    stamp_tax: float
    pnl: float
    portfolio_snapshot_hash: str  # 下单时刻的组合状态哈希


# ============================================================
# 异步回测引擎
# ============================================================

class AsyncEventEngine:
    """异步事件驱动回测引擎
    
    严格时间隔离模式，通过事件驱动架构消除未来函数。
    
    Usage:
        engine = AsyncEventEngine(config)
        result = await engine.run(strategy, symbol, data)
    """
    
    def __init__(self, config: Optional[AsyncEngineConfig] = None):
        self.config = config or AsyncEngineConfig()
        self._latency_sim = LatencySimulator(self.config.latency)
    
    async def run(
        self,
        strategy: BaseStrategy,
        symbol: str,
        data: pd.DataFrame,
        params: Optional[Dict[str, Any]] = None,
    ) -> EngineResult:
        """运行异步回测
        
        Args:
            strategy: 策略实例
            symbol: 标的代码
            data: OHLCV DataFrame
            params: 策略参数
        """
        start_time = time.time()
        
        logger.info(
            f"[AsyncEventEngine] 开始回测: {strategy.strategy_name} | "
            f"{symbol} | {len(data)} bars | "
            f"延迟={self.config.latency.total_latency_ms:.0f}ms | "
            f"快照={'开启' if self.config.save_snapshots else '关闭'}"
        )
        
        # 初始化
        portfolio = Portfolio(cash=self.config.initial_capital)
        context = Context(portfolio=portfolio, config=params or {})
        
        if params:
            strategy.set_parameters(params)
        strategy.on_init(context)
        
        # 预计算时间序列（O(N) 一次），供每根 bar 的 DataBarrier 复用
        if self.config.time_column in data.columns:
            time_array = pd.to_datetime(data[self.config.time_column]).to_numpy()
        else:
            time_array = pd.to_datetime(data.index).to_numpy()
        
        # 状态追踪
        trades: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        daily_returns: List[float] = []
        snapshots: List[EngineSnapshot] = []
        position_volume = 0
        position_cost = 0.0
        peak_value = self.config.initial_capital
        max_drawdown = 0.0
        prev_date = None
        
        # === 事件驱动回测循环 ===
        bar_index = 0  # 单调递增的 bar 序号（不依赖 DataFrame 行索引类型）
        for idx, row in data.iterrows():
            # 解析时间
            if self.config.time_column in row.index:
                bar_date = pd.to_datetime(row[self.config.time_column])
            elif isinstance(idx, pd.Timestamp):
                bar_date = idx
            else:
                bar_date = pd.to_datetime(idx)
            
            context.current_time = bar_date.to_pydatetime()
            
            # --- Step 1: 创建数据屏障（时间隔离） ---
            # 使用 lazy 二分定位（O(log N)），避免逐 bar 对 DataFrame
            # 做 O(N) 物理切片导致的 O(N²) 总复杂度。
            barrier = DataBarrier(
                data=data,
                cutoff_time=bar_date,
                time_column=self.config.time_column,
                strict=True,
                lazy=True,
                time_array=time_array,
            )
            
            # --- Step 2: 构造行情事件 ---
            bar = BarData(
                symbol=symbol,
                datetime=bar_date.to_pydatetime(),
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=float(row.get("volume", 0)),
                amount=float(row.get("amount", 0)),
            )
            
            bar_event = BarEvent(
                timestamp=bar_date.to_pydatetime(),
                bar=bar,
                bar_index=bar_index,
                data_barrier=barrier,
            )
            
            # --- Step 3: 创建时间切片上下文 ---
            portfolio_snap = PortfolioSnapshot(
                cash=portfolio.cash,
                positions=copy.deepcopy(portfolio.positions) if portfolio.positions else {},
                total_value=portfolio.total_value,
                daily_pnl=portfolio.daily_pnl,
                total_pnl=portfolio.total_pnl,
                timestamp=bar_date.to_pydatetime(),
            )
            
            ts_context = TimeSliceContext(
                data_barrier=barrier,
                current_time=bar_date.to_pydatetime(),
                portfolio_snapshot=portfolio_snap,
            )
            context.data_feed = ts_context
            
            # --- Step 4: 策略计算（信号时刻 = 当前bar时刻） ---
            signal_time = bar_date.to_pydatetime()
            
            try:
                signals = strategy.on_bar(context, bar)
                if signals is None:
                    signals = []
            except Exception as e:
                from finhack_pro.backtest.time_slice import LookAheadError
                if isinstance(e, LookAheadError):
                    logger.error(f"[AsyncEventEngine] 未来函数检测: {e}")
                    raise  # 异步模式严格模式下直接终止
                logger.error(f"[AsyncEventEngine] 策略异常: {e}")
                signals = []
            
            # --- Step 5: 延迟模拟 ---
            # 关键：成交时刻 > 信号时刻
            fill_time = self._latency_sim.get_fill_time(signal_time)
            
            # 模拟异步延迟（可选，在批量模式下跳过实际等待）
            if self.config.batch_size == 1 and self.config.latency.total_latency_ms > 0:
                await self._latency_sim.simulate_delay()
            
            # --- Step 6: 撮合执行（使用成交时刻的行情） ---
            for signal in signals:
                if not isinstance(signal, Signal):
                    continue
                
                # 关键：使用成交时刻（非信号时刻）的价格
                fill_price = self._latency_sim.get_fill_price(
                    data=data,
                    fill_time=fill_time,
                    direction=signal.direction.value,
                    slippage=self.config.slippage,
                )
                
                trade = self._execute_signal(
                    signal=signal,
                    fill_price=fill_price,
                    fill_time=fill_time,
                    signal_time=signal_time,
                    portfolio=portfolio,
                    position_volume=position_volume,
                    position_cost=position_cost,
                    symbol=symbol,
                    portfolio_snapshot_hash=portfolio_snap.hash(),
                )
                
                if trade:
                    trades.append(trade)
                    position_volume = trade.get("position_volume", position_volume)
                    position_cost = trade.get("position_cost", position_cost)
            
            # --- Step 7: 更新组合价值 ---
            current_value = portfolio.cash + position_volume * bar.close
            portfolio.total_value = current_value
            portfolio.daily_pnl = current_value - self.config.initial_capital
            portfolio.total_pnl = current_value - self.config.initial_capital
            
            if current_value > peak_value:
                peak_value = current_value
            drawdown = (peak_value - current_value) / peak_value if peak_value > 0 else 0
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            
            equity_curve.append({
                "datetime": bar_date.isoformat(),
                "equity": current_value,
                "cash": portfolio.cash,
                "position_value": position_volume * bar.close,
                "drawdown": drawdown,
                "signal_time": signal_time.isoformat(),
                "fill_time": fill_time.isoformat(),
            })
            
            if prev_date and bar_date.date() != prev_date.date():
                if len(equity_curve) >= 2:
                    prev_equity = equity_curve[-2]["equity"]
                    daily_return = (current_value - prev_equity) / prev_equity if prev_equity > 0 else 0
                    daily_returns.append(daily_return)
            prev_date = bar_date
            
            # --- Step 8: 保存状态快照 ---
            if self.config.save_snapshots and (
                self.config.snapshot_interval <= 1 or 
                len(equity_curve) % self.config.snapshot_interval == 0
            ):
                snapshot = EngineSnapshot(
                    timestamp=bar_date.to_pydatetime(),
                    portfolio=PortfolioSnapshot(
                        cash=portfolio.cash,
                        positions=copy.deepcopy(portfolio.positions) if portfolio.positions else {},
                        total_value=portfolio.total_value,
                        daily_pnl=portfolio.daily_pnl,
                        total_pnl=portfolio.total_pnl,
                        timestamp=bar_date.to_pydatetime(),
                    ),
                    bar=bar,
                    signals=list(signals) if signals else [],
                    data_barrier=barrier,
                    metadata={
                        "signal_time": signal_time.isoformat(),
                        "fill_time": fill_time.isoformat(),
                        "position_volume": position_volume,
                    },
                )
                snapshots.append(snapshot)
            
            bar_index += 1
        
        # 策略结束
        strategy.on_finish(context)
        
        # 计算结果
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
            snapshots=snapshots,
        )
        
        logger.info(
            f"[AsyncEventEngine] 回测完成: "
            f"收益={result.total_return:.2%} | "
            f"夏普={result.sharpe_ratio:.2f} | "
            f"最大回撤={result.max_drawdown:.2%} | "
            f"交易={result.total_trades}次 | "
            f"耗时={execution_time:.2f}s | "
            f"快照={len(snapshots)}个"
        )
        
        return result
    
    def _execute_signal(
        self,
        signal: Signal,
        fill_price: float,
        fill_time: datetime,
        signal_time: datetime,
        portfolio: Portfolio,
        position_volume: int,
        position_cost: float,
        symbol: str,
        portfolio_snapshot_hash: str,
    ) -> Optional[Dict[str, Any]]:
        """执行交易信号（使用成交价格）"""
        trade = None
        
        if signal.direction == SignalDirection.BUY:
            available_cash = portfolio.cash * 0.9
            volume = int(available_cash / fill_price / 100) * 100
            
            if volume > 0:
                cost = volume * fill_price
                commission = cost * self.config.commission_rate
                portfolio.cash -= (cost + commission)
                position_volume += volume
                position_cost = (position_cost * (position_volume - volume) + cost) / position_volume
                
                portfolio.positions[symbol] = {
                    "volume": position_volume,
                    "cost": position_cost,
                    "pnl": 0.0,
                }
                
                trade = {
                    "datetime": fill_time.isoformat(),
                    "signal_time": signal_time.isoformat(),
                    "symbol": symbol,
                    "direction": "buy",
                    "price": fill_price,
                    "volume": volume,
                    "commission": commission,
                    "position_volume": position_volume,
                    "position_cost": position_cost,
                    "cash_after": portfolio.cash,
                    "portfolio_hash": portfolio_snapshot_hash,
                }
        
        elif signal.direction == SignalDirection.SELL:
            if position_volume > 0:
                revenue = position_volume * fill_price
                commission = revenue * self.config.commission_rate
                stamp_tax = revenue * self.config.stamp_tax_rate
                pnl = revenue - position_volume * position_cost - commission - stamp_tax
                
                portfolio.cash += (revenue - commission - stamp_tax)
                
                trade = {
                    "datetime": fill_time.isoformat(),
                    "signal_time": signal_time.isoformat(),
                    "symbol": symbol,
                    "direction": "sell",
                    "price": fill_price,
                    "volume": position_volume,
                    "commission": commission + stamp_tax,
                    "pnl": pnl,
                    "position_volume": 0,
                    "position_cost": 0.0,
                    "cash_after": portfolio.cash,
                    "portfolio_hash": portfolio_snapshot_hash,
                }
                
                position_volume = 0
                position_cost = 0.0
                
                if symbol in portfolio.positions:
                    portfolio.positions[symbol] = {
                        "volume": 0, "cost": 0.0, "pnl": pnl,
                    }
        
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
        snapshots: List[EngineSnapshot],
    ) -> EngineResult:
        """计算回测结果"""
        final_value = portfolio.total_value
        total_return = (final_value - self.config.initial_capital) / self.config.initial_capital
        
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
        
        sharpe = 0.0
        if len(daily_returns) > 1:
            returns_arr = np.array(daily_returns)
            mean_ret = np.mean(returns_arr)
            std_ret = np.std(returns_arr)
            if std_ret > 0:
                sharpe = (mean_ret / std_ret) * np.sqrt(252)
        
        sell_trades = [t for t in trades if t.get("direction") == "sell"]
        winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
        losing = [t for t in sell_trades if t.get("pnl", 0) <= 0]
        win_rate = len(winning) / len(sell_trades) if sell_trades else 0
        
        total_winning_pnl = sum(t.get("pnl", 0) for t in winning)
        total_losing_pnl = abs(sum(t.get("pnl", 0) for t in losing))
        profit_loss_ratio = total_winning_pnl / total_losing_pnl if total_losing_pnl > 0 else 0
        
        if self.config.time_column in data.columns:
            start_date = str(pd.to_datetime(data[self.config.time_column].iloc[0]).date())
            end_date = str(pd.to_datetime(data[self.config.time_column].iloc[-1]).date())
        else:
            start_date = ""
            end_date = ""
        
        return EngineResult(
            mode=BacktestMode.ASYNC_EVENT,
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
            snapshots=snapshots,
            execution_time_seconds=execution_time,
            look_ahead_warnings=0,  # 异步模式严格模式下不会产生警告，直接报错
            metadata={
                "engine": "async_event",
                "latency_ms": self.config.latency.total_latency_ms,
                "batch_size": self.config.batch_size,
                "snapshots_count": len(snapshots),
            },
        )
