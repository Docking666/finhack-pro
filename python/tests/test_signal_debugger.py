"""阶段1 信号调试器回归测试

- runner 逐 bar 产生 signal_log（date/extra/signals/持仓/总权益）
- services.get_signal_log 抽样逻辑：>max_rows 均匀抽样但信号行全保留
"""

import numpy as np
import pandas as pd
import pytest

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.strategies.base import BaseStrategy, Context, BarData
from finhack_pro.strategies.dual_thrust import DualThrustStrategy


def _make_df(n=120, seed=42, trend=0.5):
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(np.random.randn(n) * 1.5 + trend)
    open_ = close - np.random.randn(n) * 0.5
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": np.maximum(open_, close) * 1.01,
        "low": np.minimum(open_, close) * 0.99,
        "close": close,
        "volume": np.random.randint(100_000, 1_000_000, n),
    })


class _ProbeStrategy(BaseStrategy):
    """探针：固定在某天产出信号，其余天无信号"""

    def __init__(self, trigger_idx=50):
        super().__init__()
        self.strategy_name = "Probe"
        self._params = {}
        self.trigger_idx = trigger_idx
        self.idx = 0

    def on_init(self, context):
        self._params.update(context.params)
        self.idx = 0

    def on_bar(self, context, bar):
        from finhack_pro.strategies.base import Signal, SignalDirection
        self.idx += 1
        if self.idx == self.trigger_idx:
            return [Signal(
                symbol=bar.symbol,
                direction=SignalDirection.BUY,
                price=bar.close,
                strategy_name=self.strategy_name,
            )]
        return []


class TestSignalLog:
    def test_runner_records_per_bar_log(self):
        """signal_log 每 bar 一条，含 date/extra/signals/持仓/总权益"""
        from finhack_pro.webui.services import _precompute_niche_fields

        df = _precompute_niche_fields(_make_df(n=60, seed=5))
        probe = _ProbeStrategy(trigger_idx=30)
        runner = BacktestRunner()
        result = runner.run(strategy=probe, symbol="600519.SH", data=df, initial_capital=1_000_000.0)

        assert len(result.signal_log) == 60, f"应逐 bar 记录, 实际 {len(result.signal_log)} 行"

        row = result.signal_log[29]  # 触发日
        assert str(row["date"]) == str(df["date"].iloc[29])
        assert row["signals"] and row["signals"][0]["direction"] == "buy"
        assert row["signals"][0]["strategy_name"] == "Probe"
        assert "ma20" in row["extra"], "extra 应含预计算指标（B1 联动）"
        assert "total_value" in row and row["total_value"] > 0

        # 未触发行 signals 为空
        assert result.signal_log[10]["signals"] == []

    def test_signal_log_sampling_keeps_triggered(self):
        """>max_rows 时均匀抽样，但信号行全保留"""
        from finhack_pro.webui.services import BacktestService

        # 构造 3000 行日志：第 100/1500/2900 行为触发行
        log = [
            {"date": f"2024-01-{i % 28 + 1:02d}", "extra": {"ma20": 100 + i},
             "signals": [{"direction": "buy", "strategy_name": "Probe"}] if i in (100, 1500, 2900) else [],
             "position_volume": 100 if i > 100 else 0, "total_value": 1_000_000 + i}
            for i in range(3000)
        ]
        svc = BacktestService()
        svc._signal_logs["t1"] = log

        out = svc.get_signal_log("t1", max_rows=2000)
        assert out["sampled"] is True
        assert out["total"] == 3000
        assert len(out["rows"]) <= 2000

        # 所有触发行必须保留
        kept_dates = {r["date"] for r in out["rows"]}
        for i in (100, 1500, 2900):
            assert f"2024-01-{i % 28 + 1:02d}" in kept_dates, f"触发行 {i} 被抽样丢弃"

    def test_signal_log_under_limit_no_sampling(self):
        """行数 ≤ max_rows 时原样返回"""
        from finhack_pro.webui.services import BacktestService

        log = [{"date": f"d{i}", "extra": {}, "signals": [], "position_volume": 0, "total_value": 0}
               for i in range(100)]
        svc = BacktestService()
        svc._signal_logs["t2"] = log
        out = svc.get_signal_log("t2", max_rows=2000)
        assert out["sampled"] is False
        assert len(out["rows"]) == 100

    def test_signal_log_missing_task_returns_empty(self):
        """不存在的 task_id 返回空结构而非抛错"""
        from finhack_pro.webui.services import BacktestService

        out = BacktestService().get_signal_log("no_such_task")
        assert out["total"] == 0
        assert out["rows"] == []
