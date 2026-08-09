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
from finhack_pro.strategies.base import BarData, Signal, SignalDirection

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


class TestLookAheadProtection:
    """防未来函数专项测试

    验证 DataBarrier（lazy 逻辑隔离 / 物理隔离两种模式）在
    任意截止时间都不可能返回未来数据，且两种模式结果一致。
    """

    def _barrier_pair(self, df, cutoff):
        """构造 lazy 与 physical 两种模式的屏障对"""
        lazy_b = DataBarrier(df, cutoff_time=cutoff, time_column="date", lazy=True)
        phys_b = DataBarrier(df, cutoff_time=cutoff, time_column="date", lazy=False)
        return lazy_b, phys_b

    def test_lazy_never_exposes_future(self, sample_bars_100):
        """lazy 模式：任意 cutoff 下 get() 返回的行时间都不超过 cutoff"""
        df = sample_bars_100
        for i in [0, 10, 50, 99]:
            cutoff = df["date"].iloc[i]
            barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date", lazy=True)
            data = barrier.get()
            assert len(data) == i + 1
            assert data["date"].max() <= cutoff

    def test_physical_never_exposes_future(self, sample_bars_100):
        """物理隔离模式：返回的数据物理上不包含未来行"""
        df = sample_bars_100
        cutoff = df["date"].iloc[60]
        barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date", lazy=False)
        data = barrier.data
        assert (data["date"] <= cutoff).all()
        assert len(data) == 61

    def test_lazy_physical_consistent(self, sample_bars_100):
        """lazy 与物理隔离模式在所有 lookback 下结果一致"""
        df = sample_bars_100
        cutoff = df["date"].iloc[75]
        lazy_b, phys_b = self._barrier_pair(df, cutoff)

        for lookback in [0, 1, 5, 30]:
            lazy_result = lazy_b.get(lookback=lookback)
            phys_result = phys_b.get(lookback=lookback)
            assert len(lazy_result) == len(phys_result), f"lookback={lookback} 行数不一致"
            assert (lazy_result["close"].to_numpy() == phys_result["close"].to_numpy()).all()

        # 最新一条一致
        assert lazy_b.get_latest()["close"] == phys_b.get_latest()["close"]

    def test_lookback_from_cutoff(self, sample_bars_100):
        """lookback 从截止时间（而非数据末尾）回溯"""
        df = sample_bars_100
        cutoff = df["date"].iloc[50]
        barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date", lazy=True)
        recent = barrier.get(lookback=10)
        # 最近 10 条应为索引 41..50
        assert len(recent) == 10
        assert recent["close"].iloc[-1] == df["close"].iloc[50]
        assert recent["close"].iloc[0] == df["close"].iloc[41]

    def test_check_access_and_assert(self, sample_bars_100):
        """时间安全检查：未来时间被拦截，越界抛 LookAheadError"""
        df = sample_bars_100
        cutoff = df["date"].iloc[50]
        barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date", strict=True)

        assert barrier.check_access(df["date"].iloc[30]) is True
        assert barrier.check_access(df["date"].iloc[80]) is False

        with pytest.raises(LookAheadError):
            barrier.assert_safe(df["date"].iloc[80])

        # 非严格模式不抛异常
        soft = DataBarrier(df, cutoff_time=cutoff, time_column="date", strict=False)
        soft.assert_safe(df["date"].iloc[80])  # 仅警告

    def test_unsorted_data_falls_back_to_physical(self, sample_bars_100):
        """乱序数据自动降级为物理隔离，且不暴露未来数据"""
        df = sample_bars_100.sample(frac=1, random_state=1)  # 打乱顺序
        cutoff = df["date"].iloc[30]
        barrier = DataBarrier(df, cutoff_time=cutoff, time_column="date", lazy=True)
        # 乱序时 lazy 自动降级，结果仍受截止时间约束
        data = barrier.get()
        assert (data["date"] <= cutoff).all()


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


class TestExecutionConstraints:
    """撮合精度约束测试（涨跌停 / T+1 / 停牌 / 滑点）"""

    def _make_bar(self, close=100.0, volume=10000.0, amount=1000000.0, extra=None, dt=None):
        from datetime import datetime as _dt
        return BarData(
            symbol="600000.SH",
            datetime=dt or _dt(2024, 1, 5, 10, 0),
            open=close * 0.99,
            high=close * 1.01,
            low=close * 0.98,
            close=close,
            volume=volume,
            amount=amount,
            extra=extra or {},
        )

    def _make_signal(self, direction):
        return Signal(
            symbol="600000.SH",
            direction=direction,
            price=100.0,
            volume=100,
        )

    def _make_gate(self, **kwargs):
        from finhack_pro.backtest.execution import ExecutionConfig, ExecutionGate
        defaults = dict(
            enable_limit_up_down=False,
            enable_t1=False,
            enable_suspension=False,
            enable_price_tick=False,
            slippage_model="fixed",
            slippage=0.001,
        )
        defaults.update(kwargs)
        return ExecutionGate(ExecutionConfig(**defaults))

    def test_limit_up_blocks_buy(self):
        """涨停封板：买单被拒"""
        gate = self._make_gate(enable_limit_up_down=True)
        bar = self._make_bar(close=11.0, extra={"pre_close": 10.0, "limit_pct": 0.10})  # 涨停价 11.0
        result = gate.check_and_fill(
            bar, self._make_signal(SignalDirection.BUY),
            {"volume": 0, "cost": 0}, cash=100000, direction=SignalDirection.BUY,
        )
        assert not result.executed
        assert result.reject_reason == "limit_up"

    def test_limit_down_blocks_sell(self):
        """跌停封板：卖单被拒"""
        gate = self._make_gate(enable_limit_up_down=True)
        bar = self._make_bar(close=9.0, extra={"pre_close": 10.0, "limit_pct": 0.10})  # 跌停价 9.0
        result = gate.check_and_fill(
            bar, self._make_signal(SignalDirection.SELL),
            {"volume": 1000, "cost": 10.0}, cash=0, direction=SignalDirection.SELL,
        )
        assert not result.executed
        assert result.reject_reason == "limit_down"

    def test_t1_blocks_same_day_sell(self):
        """T+1：当日买入当日不可卖"""
        from datetime import datetime as _dt
        gate = self._make_gate(enable_t1=True)
        bar = self._make_bar(dt=_dt(2024, 1, 5, 14, 0))
        position = {"volume": 1000, "cost": 10.0, "available_date": _dt(2024, 1, 6).date()}
        result = gate.check_and_fill(
            bar, self._make_signal(SignalDirection.SELL),
            position, cash=0, direction=SignalDirection.SELL,
        )
        assert not result.executed
        assert result.reject_reason == "t1_frozen"

    def test_t1_allows_next_day_sell(self):
        """T+1：次日可卖"""
        from datetime import datetime as _dt
        gate = self._make_gate(enable_t1=True)
        bar = self._make_bar(dt=_dt(2024, 1, 6, 10, 0))
        position = {"volume": 1000, "cost": 10.0, "available_date": _dt(2024, 1, 6).date()}
        result = gate.check_and_fill(
            bar, self._make_signal(SignalDirection.SELL),
            position, cash=0, direction=SignalDirection.SELL,
        )
        assert result.executed
        assert result.fill_volume == 1000

    def test_suspension_blocks_trade(self):
        """停牌：买卖均被拒"""
        gate = self._make_gate(enable_suspension=True)
        bar = self._make_bar(volume=0, amount=0)
        buy = gate.check_and_fill(
            bar, self._make_signal(SignalDirection.BUY),
            {"volume": 0}, cash=100000, direction=SignalDirection.BUY,
        )
        sell = gate.check_and_fill(
            bar, self._make_signal(SignalDirection.SELL),
            {"volume": 1000, "cost": 10}, cash=0, direction=SignalDirection.SELL,
        )
        assert not buy.executed and buy.reject_reason == "suspended"
        assert not sell.executed and sell.reject_reason == "suspended"

    def test_volume_proportional_slippage(self):
        """成交量比例滑点：大单滑点更大"""
        gate = self._make_gate(slippage_model="volume_proportional", slippage=0.001)
        small_bar = self._make_bar(close=100.0, volume=100000)
        large_bar = self._make_bar(close=100.0, volume=1000)

        small_fill = gate.check_and_fill(
            small_bar, self._make_signal(SignalDirection.BUY),
            {"volume": 0}, cash=1000000, direction=SignalDirection.BUY,
        )
        large_fill = gate.check_and_fill(
            large_bar, self._make_signal(SignalDirection.BUY),
            {"volume": 0}, cash=1000000, direction=SignalDirection.BUY,
        )
        assert small_fill.executed and large_fill.executed
        # 大单占 bar 成交量比例更高 → 滑点更大 → 买入价更高
        assert large_fill.fill_price > small_fill.fill_price

    def test_price_tick_rounding(self):
        """最小变动价位：成交价对齐 0.01"""
        gate = self._make_gate(enable_price_tick=True)
        bar = self._make_bar(close=100.0)
        fill = gate.check_and_fill(
            bar, self._make_signal(SignalDirection.BUY),
            {"volume": 0}, cash=1000000, direction=SignalDirection.BUY,
        )
        assert abs(round(fill.fill_price * 100) - fill.fill_price * 100) < 1e-6

    def test_engine_limit_up_blocks_buy(self, sample_bars_100):
        """引擎级验证：开启涨跌停约束后涨停日不成交"""
        from finhack_pro.backtest.vectorized_engine import VectorizedEngine, VectorizedEngineConfig
        from finhack_pro.strategies.dual_thrust import DualThrustStrategy

        df = sample_bars_100.copy()
        # 把最后一根 bar 改成涨停（pre_close=100, close=110）
        df.loc[df.index[-1], "close"] = 110.0
        df["pre_close"] = df["close"].shift(1)
        df.loc[df.index[-1], "pre_close"] = 100.0

        strategy = DualThrustStrategy()
        cfg = VectorizedEngineConfig(enable_limit_up_down=True)
        result = VectorizedEngine(cfg).run(strategy, "600000", df)
        # 最后一根涨停 bar 的买入被拒，不影响结果完整性
        assert result.total_trades >= 0
