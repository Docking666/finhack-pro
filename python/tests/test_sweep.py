"""阶段5 参数热力图（sweep）回归测试

- _expand_grid：int/float 步长展开正确
- run_sweep：复用 GridSearchOptimizer + 真实 BacktestRunner 适配器，
  合成数据验证 cells/best 结构与网格超限拒绝
"""

import numpy as np
import pandas as pd
import pytest

from finhack_pro.webui.models import SweepParam, SweepRequest
from finhack_pro.webui.services import BacktestService


def _synthetic_df(n=200, seed=42):
    """合成日线数据（测试用，非产品逻辑）"""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(np.random.randn(n) * 1.2)
    open_ = close - np.random.randn(n) * 0.4
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": np.maximum(open_, close) * 1.01,
        "low": np.minimum(open_, close) * 0.99,
        "close": close,
        "volume": np.random.randint(100_000, 1_000_000, n),
    })


class TestExpandGrid:
    def test_int_step(self):
        svc = BacktestService()
        vals = svc._expand_grid(SweepParam(name="k1", min=0.2, max=0.6, step=0.2))
        assert vals == [0.2, 0.4, 0.6]

    def test_float_step_no_fp_error(self):
        svc = BacktestService()
        vals = svc._expand_grid(SweepParam(name="k1", min=0.1, max=0.5, step=0.1))
        assert len(vals) == 5
        assert vals[-1] == 0.5

    def test_bounded(self):
        svc = BacktestService()
        vals = svc._expand_grid(SweepParam(name="p", min=1, max=10, step=1))
        assert vals == list(range(1, 11))
        assert len(vals) == 10  # 上限包含


class TestRunSweep:
    def _make_request(self, x=None, y=None):
        return SweepRequest(
            strategy="dual_thrust",
            symbol="600519.SH",
            start_date="2024-01-01",
            end_date="2024-06-30",
            x_param=x or SweepParam(name="k1", label="上轨系数", min=0.3, max=0.7, step=0.2),
            y_param=y or SweepParam(name="k2", label="下轨系数", min=0.3, max=0.7, step=0.2),
        )

    @pytest.mark.asyncio
    async def test_sweep_full_chain(self, monkeypatch):
        """mock 取数（合成 df）→ 3×3 网格 → cells 结构与 best"""
        from finhack_pro.data import fetcher as fetcher_mod

        class _FakeFetcher:
            def __init__(self, **kw):
                pass

            def get_daily(self, symbol, start_date, end_date):
                return _synthetic_df()

        # run_sweep 内部 `from finhack_pro.data.fetcher import DataFetcher`
        monkeypatch.setattr(fetcher_mod, "DataFetcher", _FakeFetcher)

        svc = BacktestService()
        result = await svc.run_sweep("sweep_test", self._make_request())

        assert result.error is None, result.error
        assert result.total_combos == 9
        assert len(result.cells) == 9
        for c in result.cells:
            assert 0.2 <= c.x <= 0.8
            assert 0.2 <= c.y <= 0.8
            assert c.sharpe != 0.0 or True  # sharpe 可能为 0（无交易），字段必须存在
            assert "total_return" in c.model_dump()
            assert "max_drawdown" in c.model_dump()
        assert result.best is not None

    @pytest.mark.asyncio
    async def test_grid_over_limit_rejected(self):
        """网格 >10×10 → error，不执行扫描"""
        svc = BacktestService()
        req = self._make_request(
            x=SweepParam(name="k1", min=0.1, max=1.0, step=0.1),   # 10 列
            y=SweepParam(name="k2", min=0.05, max=1.0, step=0.05), # 20 行
        )
        result = await svc.run_sweep("sweep_over", req)
        assert result.error and "网格超限" in result.error
        assert result.cells == []

    @pytest.mark.asyncio
    async def test_adapter_runs_real_backtest(self):
        """适配器走真实 BacktestRunner：合成数据可产出指标"""
        svc = BacktestService()
        adapter = svc._make_sweep_adapter(
            __import__("finhack_pro.strategies.dual_thrust", fromlist=["DualThrustStrategy"]).DualThrustStrategy,
            "600519.SH", 1_000_000,
        )
        metrics = adapter.backtest({"k1": 0.5, "k2": 0.5, "lookback": 20}, _synthetic_df())
        assert set(metrics) >= {"sharpe_ratio", "total_return", "max_drawdown", "total_trades"}
