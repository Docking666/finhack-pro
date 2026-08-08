"""
时间切片层 - Time Slice Layer

提供严格的时间隔离数据访问，从物理层面消除未来函数(Look-ahead Bias)。

核心设计:
- TimeSliceView: 只暴露截止到指定时间的数据，物理上不可能访问未来数据
- ImmutableStateSnapshot: 不可变状态快照，用于异步模式的状态传递
- DataBarrier: 数据屏障，拦截非法的未来数据访问

两种模式共享此层:
- 向量化模式: 轻量级包装，性能开销极小
- 异步事件驱动模式: 完整的时间隔离 + 延迟模拟
"""

from __future__ import annotations

import copy
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据屏障 - 拦截非法访问
# ============================================================

class LookAheadError(RuntimeError):
    """未来函数访问异常
    
    当检测到代码试图访问未来数据时抛出。
    """
    def __init__(self, message: str, access_time: datetime = None, current_time: datetime = None):
        self.access_time = access_time
        self.current_time = current_time
        detail = message
        if access_time and current_time:
            detail += f" (试图访问 {access_time} 的数据，当前时间 {current_time})"
        super().__init__(detail)


class DataBarrier:
    """数据屏障
    
    包装 DataFrame，拦截所有可能访问未来数据的操作。
    在异步事件驱动模式下强制启用，向量化模式下可选启用。

    两种隔离模式：
    - lazy=True（默认）：逻辑隔离。构造 O(1)，get/get_latest 通过
      二分定位（np.searchsorted）按截止时间截取，总回测复杂度 O(N log N)。
      要求时间序列按升序排列（回测数据默认满足），不满足时自动降级为物理切片。
    - lazy=False：物理隔离。构造时立即按截止时间切片并复制，
      返回的数据物理上不包含未来行（内存开销 O(N²)）。
    """
    
    def __init__(
        self,
        data: pd.DataFrame,
        cutoff_time: Union[datetime, pd.Timestamp, str],
        time_column: str = "date",
        strict: bool = True,
        lazy: bool = True,
        time_array: Optional[np.ndarray] = None,
    ):
        """
        Args:
            data: 原始数据
            cutoff_time: 截止时间
            time_column: 时间列名
            strict: 严格模式，访问未来数据时抛出异常
            lazy: 逻辑隔离模式（默认），false 时物理切片
            time_array: 预计算的 datetime64 数组（由引擎在循环外算一次），
                避免每个 barrier 重复 to_datetime，是 O(N²)→O(N log N) 的关键
        """
        self._original_data = data
        self._time_column = time_column
        self._strict = strict
        
        if isinstance(cutoff_time, str):
            cutoff_time = pd.to_datetime(cutoff_time)
        self._cutoff_time = pd.Timestamp(cutoff_time)
        
        # 预计算时间序列（供 lazy 定位 / 物理切片共用）
        if time_array is not None:
            self._time_series = time_array  # np.ndarray[datetime64]
        elif time_column in data.columns:
            self._time_series = pd.to_datetime(data[time_column]).to_numpy()
        else:
            self._time_series = pd.to_datetime(data.index).to_numpy()
        
        if lazy:
            # 逻辑隔离：保留引用，get 时二分定位。
            # 时间序列必须升序，否则 searchsorted 结果不可靠，自动降级。
            if self._time_series_is_sorted():
                self._lazy = True
                self._data = data
                self._available_count = int(
                    (self._time_series <= self._cutoff_time.to_datetime64()).sum()
                )
            else:
                logger.warning(
                    "[DataBarrier] 时间序列未排序，lazy 模式不可用，降级为物理切片"
                )
                self._lazy = False
                self._mask = self._time_series <= self._cutoff_time.to_datetime64()
                self._data = data.loc[np.asarray(self._mask)].copy()
                self._available_count = len(self._data)
        else:
            # 物理隔离：立即切片并复制，未来数据在物理上不可访问
            self._lazy = False
            self._mask = self._time_series <= self._cutoff_time.to_datetime64()
            self._data = data.loc[np.asarray(self._mask)].copy()
            self._available_count = len(self._data)

    def _time_series_is_sorted(self) -> bool:
        """判断时间序列是否严格升序（lazy 二分定位的前提）"""
        ts = self._time_series
        if len(ts) < 2:
            return True
        return bool(np.all(ts[1:] >= ts[:-1]))
    
    @property
    def cutoff_time(self) -> pd.Timestamp:
        """获取截止时间"""
        return self._cutoff_time
    
    @property
    def data(self) -> pd.DataFrame:
        """获取安全的数据副本"""
        return self.get()
    
    @property
    def available_count(self) -> int:
        """获取可用数据行数"""
        return self._available_count
    
    def get(self, symbol: Optional[str] = None, lookback: int = 0) -> pd.DataFrame:
        """获取截止到当前时间的数据
        
        Args:
            symbol: 标的代码（如果数据有多标的）
            lookback: 回看期数（0表示全部）
        """
        if self._lazy:
            # 二分定位：找到 <= cutoff 的最后一个位置（O(log N)）
            cutoff_np = np.datetime64(self._cutoff_time.to_datetime64())
            pos = int(np.searchsorted(self._time_series, cutoff_np, side="right"))
            if pos <= 0:
                return self._data.iloc[0:0].copy()
            start = max(0, pos - lookback) if lookback > 0 else 0
            result = self._data.iloc[start:pos]
        else:
            result = self._data
            if lookback > 0:
                result = result.tail(lookback)
        if symbol is not None and "symbol" in result.columns:
            result = result[result["symbol"] == symbol]
        return result.copy()
    
    def get_latest(self, symbol: Optional[str] = None) -> Optional[pd.Series]:
        """获取最新一条数据"""
        data = self.get(symbol, lookback=1)
        if len(data) == 0:
            return None
        return data.iloc[-1]
    
    def check_access(self, requested_time: Union[datetime, pd.Timestamp, str]) -> bool:
        """检查请求的时间是否在允许范围内
        
        Returns:
            True 如果安全，False 如果是未来数据
        """
        if isinstance(requested_time, str):
            requested_time = pd.to_datetime(requested_time)
        requested_time = pd.Timestamp(requested_time)
        return requested_time <= self._cutoff_time
    
    def assert_safe(self, requested_time: Union[datetime, pd.Timestamp, str]) -> None:
        """断言时间安全，不安全时抛出 LookAheadError"""
        if not self.check_access(requested_time):
            if self._strict:
                raise LookAheadError(
                    "检测到未来函数访问!",
                    access_time=requested_time,
                    current_time=self._cutoff_time,
                )
            else:
                logger.warning(
                    f"[DataBarrier] 检测到潜在未来函数访问: "
                    f"requested={requested_time}, current={self._cutoff_time}"
                )


# ============================================================
# 不可变状态快照
# ============================================================

@dataclass
class PortfolioSnapshot:
    """组合状态快照（不可变）"""
    cash: float
    positions: Dict[str, Dict[str, Any]]  # symbol -> {volume, cost, pnl}
    total_value: float
    daily_pnl: float
    total_pnl: float
    timestamp: datetime
    
    def copy(self) -> "PortfolioSnapshot":
        """创建深拷贝"""
        return PortfolioSnapshot(
            cash=self.cash,
            positions=copy.deepcopy(self.positions),
            total_value=self.total_value,
            daily_pnl=self.daily_pnl,
            total_pnl=self.total_pnl,
            timestamp=self.timestamp,
        )
    
    def get_position(self, symbol: str) -> Dict[str, Any]:
        """获取持仓"""
        return self.positions.get(symbol, {"volume": 0, "cost": 0.0, "pnl": 0.0})
    
    def hash(self) -> str:
        """生成状态哈希（SHA256，用于验证不可变性）"""
        content = f"{self.cash:.2f}|{self.total_value:.2f}|{sorted(self.positions.items())}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


@dataclass
class EngineSnapshot:
    """引擎完整状态快照（不可变）
    
    在异步事件驱动模式中，每个时刻生成一个快照，
    通过不可变消息传递给下游环节。
    """
    timestamp: datetime
    portfolio: PortfolioSnapshot
    bar: Optional[Any] = None  # BarData
    signals: List[Any] = field(default_factory=list)  # List[Signal]
    orders: List[Any] = field(default_factory=list)
    fills: List[Any] = field(default_factory=list)
    data_barrier: Optional[DataBarrier] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def copy(self) -> "EngineSnapshot":
        """创建深拷贝"""
        return EngineSnapshot(
            timestamp=self.timestamp,
            portfolio=self.portfolio.copy(),
            bar=self.bar,
            signals=list(self.signals),
            orders=list(self.orders),
            fills=list(self.fills),
            data_barrier=self.data_barrier,
            metadata=copy.deepcopy(self.metadata),
        )


# ============================================================
# 延迟模拟器
# ============================================================

@dataclass
class LatencyConfig:
    """延迟配置
    
    模拟真实交易中的各种延迟。
    """
    data_latency_ms: float = 0.0       # 行情数据到达延迟（毫秒）
    compute_latency_ms: float = 1.0     # 策略计算延迟
    order_latency_ms: float = 5.0      # 订单到达交易所延迟
    fill_latency_ms: float = 10.0      # 撮合回报延迟
    total_latency_ms: float = 0.0      # 总延迟（自动计算）
    
    def __post_init__(self):
        if self.total_latency_ms == 0:
            self.total_latency_ms = (
                self.data_latency_ms + 
                self.compute_latency_ms + 
                self.order_latency_ms + 
                self.fill_latency_ms
            )


class LatencySimulator:
    """延迟模拟器
    
    在异步事件驱动模式中模拟真实交易延迟。
    向量化模式不使用此组件。
    """
    
    def __init__(self, config: Optional[LatencyConfig] = None):
        self.config = config or LatencyConfig()
    
    def get_fill_time(self, signal_time: datetime) -> datetime:
        """计算实际成交时间
        
        Args:
            signal_time: 信号产生时间
            
        Returns:
            考虑延迟后的成交时间
        """
        delta = timedelta(milliseconds=self.config.total_latency_ms)
        return signal_time + delta
    
    def get_fill_price(self, data: pd.DataFrame, fill_time: datetime, 
                       direction: str, slippage: float = 0.001) -> float:
        """获取成交价格
        
        使用成交时刻（而非信号时刻）的行情，加上滑点。
        这是消除未来函数的关键：信号时刻和成交时刻不同。
        
        Args:
            data: 完整行情数据
            fill_time: 成交时间
            direction: "buy" 或 "sell"
            slippage: 滑点比例
        """
        # 找到 fill_time 之后最近的行情
        if isinstance(data.index, pd.DatetimeIndex):
            time_col = data.index
        elif "date" in data.columns:
            time_col = pd.to_datetime(data["date"])
        else:
            time_col = pd.to_datetime(data.index)
        
        fill_ts = pd.Timestamp(fill_time)
        
        # 找到 >= fill_time 的第一条记录
        mask = time_col >= fill_ts
        if mask.any():
            idx = mask.idxmax() if hasattr(mask, 'idxmax') else data.index[mask].tolist()[0]
            if hasattr(data.loc[idx], 'close'):
                base_price = float(data.loc[idx, 'close'])
            elif isinstance(idx, (int, np.integer)):
                base_price = float(data.iloc[idx]['close']) if hasattr(data.iloc[idx], 'close') else float(data.iloc[idx]['close'])
            else:
                base_price = float(data.loc[idx, 'close'])
        else:
            # 没有未来数据，使用最后已知价格
            base_price = float(data['close'].iloc[-1])
        
        # 应用滑点
        if direction == "buy":
            return base_price * (1 + slippage)
        else:
            return base_price * (1 - slippage)
    
    async def simulate_delay(self) -> None:
        """模拟异步延迟（在异步引擎中使用）"""
        import asyncio
        delay_seconds = self.config.total_latency_ms / 1000.0
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)


# ============================================================
# 时间切片上下文 - 替代 Context.data_feed
# ============================================================

class TimeSliceContext:
    """时间切片上下文
    
    提供给策略的安全数据访问接口，物理上不可能访问未来数据。
    替代现有 Context 中空壳的 data_feed。
    """
    
    def __init__(
        self,
        data_barrier: DataBarrier,
        current_time: datetime,
        portfolio_snapshot: PortfolioSnapshot,
    ):
        self._barrier = data_barrier
        self._current_time = current_time
        self._portfolio = portfolio_snapshot
    
    @property
    def current_time(self) -> datetime:
        return self._current_time
    
    @property
    def cutoff_time(self) -> pd.Timestamp:
        return self._barrier.cutoff_time
    
    def get_history(self, symbol: Optional[str] = None, lookback: int = 0) -> pd.DataFrame:
        """获取历史数据（安全，不可能包含未来数据）"""
        return self._barrier.get(symbol=symbol, lookback=lookback)
    
    def get_latest_bar(self, symbol: Optional[str] = None) -> Optional[pd.Series]:
        """获取最新K线"""
        return self._barrier.get_latest(symbol=symbol)
    
    def get_position(self, symbol: str) -> Dict[str, Any]:
        """获取当前持仓"""
        return self._portfolio.get_position(symbol)
    
    def get_cash(self) -> float:
        """获取当前现金"""
        return self._portfolio.cash
    
    def get_total_value(self) -> float:
        """获取总资产"""
        return self._portfolio.total_value
    
    def assert_time_safe(self, requested_time: Union[datetime, str]) -> None:
        """断言时间安全"""
        self._barrier.assert_safe(requested_time)


# ============================================================
# 回测模式枚举
# ============================================================

class BacktestMode(str, Enum):
    """回测模式"""
    VECTORIZED = "vectorized"       # 向量化模式（高性能，轻量级时间隔离）
    ASYNC_EVENT = "async_event"     # 异步事件驱动模式（严格时间隔离+延迟模拟）


# ============================================================
# 回测引擎结果
# ============================================================

@dataclass
class EngineResult:
    """回测引擎结果"""
    mode: BacktestMode
    strategy_name: str
    symbol: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    profit_loss_ratio: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    trades: List[Dict[str, Any]] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    snapshots: List[EngineSnapshot] = field(default_factory=list)  # 异步模式的状态快照
    execution_time_seconds: float = 0.0
    look_ahead_warnings: int = 0  # 未来函数警告次数
    metadata: Dict[str, Any] = field(default_factory=dict)
