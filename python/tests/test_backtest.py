"""
回测模块测试

覆盖:
- time_slice: DataBarrier, PortfolioSnapshot, LatencySimulator, TimeSliceContext
- vectorized_engine: VectorizedEngine
- async_engine: AsyncEventEngine
- engine_factory: create_engine, run_backtest, compare_modes
- accelerated: NumPyVectorizedEngine, Numba JIT, RustCoreBridge
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from finhack_pro.backtest.accelerated import (
    NumPyEngineConfig,
    NumPyVectorizedEngine,
    RustCoreBridge,
    _calculate_drawdown_numpy,
    _calculate_sharpe_numpy,
    numba_jit_available,
)
from finhack_pro.backtest.engine_factory import BacktestMode, create_engine
from finhack_pro.backtest.time_slice import (
    BacktestMode,
    DataBarrier,
    EngineResult,
    EngineSnapshot,
    LatencyConfig,
    LatencySimulator,
    LookAheadError,
    PortfolioSnapshot,
    TimeSliceContext,
)
from finhack_pro.backtest.vectorized_engine import VectorizedEngine, VectorizedEngineConfig

# ============================================================================
# time_slice 测试
# ============================================================================

class TestDataBarrier:
    """DataBarrier 数据屏障测试"""

    def test_basic_slicing(self, sample_bars_100):
        """基本切片功能"""
        df = sample_bars_100
        cutoff = df["date"].iloc[50]
        barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date")

        assert len(barrier.data) == 51  # 0..50
        assert barrier.data["date"].iloc[-1] == cutoff

    def test_get_latest(self, sample_bars_100):
        """获取最新数据"""
        df = sample_bars_100
        cutoff = df["date"].iloc[80]
        barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date")

        # get_latest() returns Optional[pd.Series], not a scalar
        latest = barrier.get_latest()
        assert latest is not None
        assert latest["close"] == df["close"].iloc[80]

    def test_get_history(self, sample_bars_100):
        """获取历史数据"""
        df = sample_bars_100
        cutoff = df["date"].iloc[50]
        barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date")

        # DataBarrier uses get(symbol, lookback) not get_history(column, periods)
        history = barrier.get(lookback=10)
        assert len(history) == 10
        assert history["close"].iloc[-1] == df["close"].iloc[50]

    def test_empty_barrier(self):
        """空数据屏障"""
        df = pd.DataFrame({"date": [], "close": []})
        barrier = DataBarrier(df, cutoff_time=datetime(2024, 1, 1), time_column="date")
        assert len(barrier.data) == 0


class TestPortfolioSnapshot:
    """PortfolioSnapshot 不可变快照测试"""

    def test_create_snapshot(self):
        snap = PortfolioSnapshot(
            cash=100000,
            positions={"TEST": {"volume": 100, "cost": 10.0, "pnl": 0.0}},
            total_value=200000,
            daily_pnl=5000,
            total_pnl=50000,
            timestamp=datetime.now(),
        )
        assert snap.cash == 100000
        assert snap.total_value == 200000

    def test_deep_copy(self):
        original = PortfolioSnapshot(
            cash=100000, positions={}, total_value=100000,
            daily_pnl=0, total_pnl=0, timestamp=datetime.now(),
        )
        # Source uses copy() not deep_copy()
        copied = original.copy()
        copied.cash = 0
        assert original.cash == 100000  # 原始不受影响

    def test_hash_verification(self):
        snap = PortfolioSnapshot(
            cash=100000, positions={}, total_value=100000,
            daily_pnl=0, total_pnl=0, timestamp=datetime.now(),
        )
        # Source uses hash() not verify_hash(); it returns a string hash
        h = snap.hash()
        assert isinstance(h, str)
        assert len(h) == 16


class TestLatencySimulator:
    """延迟模拟器测试"""

    def test_fill_delay(self):
        config = LatencyConfig(fill_latency_ms=100)
        sim = LatencySimulator(config)
        signal_time = datetime(2024, 1, 1, 9, 30, 0)
        fill_time = sim.get_fill_time(signal_time)
        assert fill_time > signal_time

    def test_total_latency(self):
        config = LatencyConfig(
            data_latency_ms=10,
            compute_latency_ms=5,
            order_latency_ms=50,
            fill_latency_ms=100,
        )
        assert config.total_latency_ms == 165


# ============================================================================
# VectorizedEngine 测试
# ============================================================================

class TestVectorizedEngine:
    """向量化引擎测试"""

    def _make_strategy(self):
        from finhack_pro.strategies.base import BarData, BaseStrategy, Context, Signal, SignalDirection

        class MAStrategy(BaseStrategy):
            strategy_name = "test_ma"
            def on_init(self, context):
                self.prices = []
            def on_bar(self, context, bar):
                self.prices.append(bar.close)
                if len(self.prices) >= 20:
                    fast = np.mean(self.prices[-5:])
                    slow = np.mean(self.prices[-20:])
                    if fast > slow:
                        return [Signal(direction=SignalDirection.BUY, strength=0.5)]
                return []

        return MAStrategy()

    def test_basic_run(self, sample_bars_100):
        config = VectorizedEngineConfig(enable_time_slice=False)
        engine = VectorizedEngine(config)
        strategy = self._make_strategy()
        result = engine.run(strategy, "TEST", sample_bars_100, {"fast": 5, "slow": 20})
        assert isinstance(result, EngineResult)
        assert len(result.equity_curve) == 100

    def test_with_time_slice(self, sample_bars_100):
        config = VectorizedEngineConfig(enable_time_slice=True, strict_mode=False)
        engine = VectorizedEngine(config)
        strategy = self._make_strategy()
        result = engine.run(strategy, "TEST", sample_bars_100, {"fast": 5, "slow": 20})
        assert isinstance(result, EngineResult)


# ============================================================================
# engine_factory 测试
# ============================================================================

class TestEngineFactory:
    """引擎工厂测试"""

    def test_create_vectorized(self):
        engine = create_engine(BacktestMode.VECTORIZED)
        assert engine is not None

    def test_create_async(self):
        engine = create_engine(BacktestMode.ASYNC_EVENT)
        assert engine is not None


# ============================================================================
# accelerated 测试
# ============================================================================

class TestNumPyAccelerated:
    """NumPy 加速模块测试"""

    def _make_strategy(self):
        from finhack_pro.strategies.base import BarData, BaseStrategy, Context, Signal, SignalDirection

        class MAStrategy(BaseStrategy):
            strategy_name = "test_ma"
            def on_init(self, context):
                self.prices = []
            def on_bar(self, context, bar):
                self.prices.append(bar.close)
                if len(self.prices) >= 20:
                    fast = np.mean(self.prices[-5:])
                    slow = np.mean(self.prices[-20:])
                    if fast > slow:
                        return [Signal(direction=SignalDirection.BUY, strength=0.5)]
                return []

        return MAStrategy()

    def test_numpy_engine_basic(self, sample_bars_100):
        config = NumPyEngineConfig(enable_time_slice=False)
        engine = NumPyVectorizedEngine(config)
        strategy = self._make_strategy()
        result = engine.run(strategy, "TEST", sample_bars_100, {"fast": 5, "slow": 20})
        assert isinstance(result, EngineResult)
        assert len(result.equity_curve) == 100

    def test_numba_drawdown(self, sample_equity_curve):
        """Numba 回撤计算（回退模式）"""
        max_dd, dd_arr = _calculate_drawdown_numpy(sample_equity_curve)
        assert isinstance(max_dd, float)
        assert 0 <= max_dd <= 1
        assert len(dd_arr) == len(sample_equity_curve)

    def test_numba_sharpe(self, sample_returns):
        """Numba 夏普比率计算（回退模式）"""
        sharpe = _calculate_sharpe_numpy(sample_returns)
        assert isinstance(sharpe, float)

    def test_numba_available(self):
        """Numba 可用性检查"""
        result = numba_jit_available()
        assert isinstance(result, bool)


class TestRustCoreBridge:
    """Rust 核心桥接测试"""

    def test_creation(self):
        bridge = RustCoreBridge()
        assert bridge is not None
        assert hasattr(bridge, "is_rust_available")
        assert hasattr(bridge, "preferred_mode")

    def test_preferred_mode(self):
        bridge = RustCoreBridge()
        assert bridge.preferred_mode in ("pyo3", "http", "python")

    def test_python_fallback_indicators(self, sample_bars_100):
        """Python 回退指标计算"""
        bridge = RustCoreBridge()
        bridge._pyo3_available = False
        bridge._http_available = False

        result = bridge.batch_calculate_indicators(sample_bars_100, ["rsi", "macd"])
        assert "rsi" in result.columns
        assert "macd" in result.columns

    def test_python_fallback_backtest(self, sample_bars_100):
        """Python 回退批量回测"""
        bridge = RustCoreBridge()
        bridge._pyo3_available = False
        bridge._http_available = False

        configs = [
            {"name": "MA_5_20", "fast_period": 5, "slow_period": 20},
        ]
        results = bridge.batch_backtest(configs, sample_bars_100)
        assert isinstance(results, list)
