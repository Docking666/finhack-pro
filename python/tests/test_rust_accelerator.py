"""
Rust PyO3 加速层测试

验证 finhack_pyo3 模块的可用性和计算正确性（与纯 Python/NumPy 实现对照）。
这些测试在 finhack_pyo3 未编译时自动跳过，不影响 CI 主流程。
"""

import numpy as np
import pandas as pd
import pytest

pyo3 = pytest.importorskip("finhack_pyo3", reason="finhack_pyo3 未编译，跳过 Rust 测试")


def _make_ohlc(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """生成标准 OHLCV 测试数据"""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="5min")
    closes = np.random.uniform(90, 110, n).cumsum() % 120 + 80
    return pd.DataFrame({
        "date": dates,
        "open": closes * 0.99,
        "high": closes * 1.02,
        "low": closes * 0.98,
        "close": closes,
        "volume": np.random.uniform(1000, 10000, n),
    })


class TestRustModuleBasics:
    """Rust 模块基础"""

    def test_get_version(self):
        assert isinstance(pyo3.get_version(), str)
        assert pyo3.get_version()

    def test_get_rayon_threads(self):
        assert pyo3.get_rayon_threads() >= 1

    def test_calculate_max_drawdown(self):
        """最大回撤与 NumPy 实现一致"""
        equity = np.array([100, 110, 95, 120, 90, 130], dtype=np.float64)
        rust_dd = pyo3.calculate_max_drawdown(equity)

        # NumPy 对照
        peak = np.maximum.accumulate(equity)
        py_dd = float(((peak - equity) / peak).max())

        assert abs(rust_dd - py_dd) < 1e-9

    def test_calculate_sharpe_ratio(self):
        """夏普比率与 NumPy 实现一致"""
        returns = np.random.normal(0.001, 0.02, 100).astype(np.float64)
        rust_sharpe = pyo3.calculate_sharpe_ratio(returns, None)

        mean = returns.mean()
        std = returns.std(ddof=1)
        py_sharpe = (mean / std) * np.sqrt(252) if std > 0 else 0.0

        assert abs(rust_sharpe - py_sharpe) < 1e-6


class TestRustIndicators:
    """Rust 指标计算"""

    def test_calculate_indicators_lengths(self):
        df = _make_ohlc(200)
        closes = df["close"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()

        result = pyo3.calculate_indicators(
            closes, highs, lows, ["rsi", "macd", "bollinger", "atr"]
        )

        assert len(result["rsi"]) == 200
        assert len(result["macd"]) == 200
        assert len(result["bb_upper"]) == 200
        assert len(result["bb_middle"]) == 200
        assert len(result["bb_lower"]) == 200
        assert len(result["atr"]) == 200

    def test_rsi_reasonable_range(self):
        """RSI 应在 0~100 范围"""
        df = _make_ohlc(300)
        result = pyo3.calculate_indicators(
            df["close"].to_numpy(), None, None, ["rsi"]
        )
        rsi = [v for v in result["rsi"] if v is not None]
        assert rsi, "RSI 不应全为空"
        assert all(0.0 <= v <= 100.0 for v in rsi)

    def test_macd_after_warmup(self):
        """MACD 在足够预热后应有值"""
        df = _make_ohlc(300)
        result = pyo3.calculate_indicators(
            df["close"].to_numpy(), None, None, ["macd"]
        )
        macd = [v for v in result["macd"] if v is not None]
        assert len(macd) > 100  # 至少 100 个有效值


class TestRustBacktest:
    """Rust 批量回测"""

    def test_batch_backtest_basic(self):
        closes = np.random.uniform(90, 110, 500).cumsum() % 200 + 50
        closes = closes.astype(np.float64)
        configs = [
            {"fast_period": 5, "slow_period": 20},
            {"fast_period": 10, "slow_period": 30},
        ]
        result = pyo3.batch_backtest(closes, configs, 1_000_000.0)

        assert len(result["results"]) == 2
        for r in result["results"]:
            assert "total_return" in r
            assert "max_drawdown" in r
            assert "sharpe_ratio" in r
            assert r["total_return"] >= -1.0  # 收益下限 -100%

    def test_batch_backtest_more_configs_than_threads(self):
        """策略数多于线程数时仍全部返回"""
        closes = np.linspace(100, 150, 300, dtype=np.float64)
        configs = [{"fast_period": f, "slow_period": 20} for f in range(3, 12)]
        result = pyo3.batch_backtest(closes, configs, 100_000.0)
        assert len(result["results"]) == len(configs)


class TestPyO3IsolatedIntegration:
    """PyO3Isolated 子进程隔离链路"""

    def test_get_pyo3_isolated_available(self):
        from finhack_pro.backtest import get_pyo3_isolated

        rust = get_pyo3_isolated()
        assert rust.is_available

    def test_call_indicators_through_worker(self):
        from finhack_pro.backtest import get_pyo3_isolated

        rust = get_pyo3_isolated()
        if not rust.start():
            pytest.skip("PyO3 子进程启动失败")

        try:
            closes = np.random.uniform(90, 110, 100).astype(np.float64)
            status, result = rust.calculate_indicators(closes, None, None, ["rsi"])
            assert status == "ok"
            assert len(result["rsi"]) == 100
        finally:
            rust.stop()

    def test_max_drawdown_through_worker(self):
        from finhack_pro.backtest import get_pyo3_isolated

        rust = get_pyo3_isolated()
        if not rust.start():
            pytest.skip("PyO3 子进程启动失败")

        try:
            equity = np.array([100, 120, 90, 130, 80], dtype=np.float64)
            status, dd = rust.calculate_max_drawdown(equity)
            assert status == "ok"
            expected = float(((np.maximum.accumulate(equity) - equity) / np.maximum.accumulate(equity)).max())
            assert abs(dd - expected) < 1e-9
        finally:
            rust.stop()


class TestRustCoreBridgePyO3Path:
    """RustCoreBridge 的 PyO3 优先路径"""

    def test_preferred_mode_is_pyo3(self):
        from finhack_pro.backtest import get_rust_bridge

        bridge = get_rust_bridge()
        if not bridge.is_rust_available:
            pytest.skip("Rust 路径不可用")

        assert bridge.preferred_mode == "pyo3"

    def test_batch_calculate_indicators(self):
        from finhack_pro.backtest import get_rust_bridge

        bridge = get_rust_bridge()
        if not bridge.is_rust_available:
            pytest.skip("Rust 路径不可用")

        df = _make_ohlc(150)
        result = bridge.batch_calculate_indicators(df, ["rsi", "macd"])
        assert "rsi" in result.columns
        assert "macd" in result.columns
        assert len(result) == 150
