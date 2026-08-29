"""阶段6 LLM 安全边界回归测试

- StrategyManifest status 默认 draft + 序列化保留
- run_overfit_check：样本内/外分治判定（通过/oos<0/衰减过快/数据不足）
"""

import numpy as np
import pandas as pd
import pytest

from finhack_pro.strategies.base import BaseStrategy, Context, Signal, SignalDirection
from finhack_pro.strategies.strategy_validator import run_overfit_check
from finhack_pro.workshop import StrategyManifest


def _price_df(segments):
    """按段拼接价格序列（每段 [起始, 终止, 天数]）"""
    prices = []
    for start, end, n in segments:
        prices.extend(np.linspace(start, end, n))
    dates = pd.date_range("2023-01-01", periods=len(prices), freq="B")
    close = np.array(prices)
    open_ = close - 0.3
    return pd.DataFrame({
        "date": dates, "open": open_,
        "high": np.maximum(open_, close) + 0.5,
        "low": np.minimum(open_, close) - 0.5,
        "close": close,
        "volume": np.full(len(close), 500_000),
    })


class _PeriodicStrategy(BaseStrategy):
    """周期信号探针：每 15 根买、每 15 根（+10）卖 → 夏普由价格方向决定"""

    def __init__(self):
        super().__init__()
        self.strategy_name = "Periodic"
        self._params = {}
        self.idx = 0

    def on_init(self, context):
        self._params.update(context.params)
        self.idx = 0

    def on_bar(self, context, bar):
        self.idx += 1
        if self.idx % 15 == 1:
            return [Signal(symbol=bar.symbol, direction=SignalDirection.BUY, price=bar.close, strategy_name=self.strategy_name)]
        if self.idx % 15 == 10:
            return [Signal(symbol=bar.symbol, direction=SignalDirection.SELL, price=bar.close, strategy_name=self.strategy_name)]
        return []


class TestManifestStatus:
    def test_default_status_draft(self):
        m = StrategyManifest.from_dict({"id": "x", "name": "n", "version": "1.0"})
        assert m.status == "draft"
        assert m.validation_report == {}

    def test_status_roundtrip(self, tmp_path):
        m = StrategyManifest.from_dict({
            "id": "x", "name": "n", "version": "1.0",
            "status": "enabled",
            "validation_report": {"passed": True, "overfit": {"oos_sharpe": 1.2}},
        })
        yaml_text = m.to_yaml()
        m2 = StrategyManifest.from_yaml(yaml_text)
        assert m2.status == "enabled"
        assert m2.validation_report["passed"] is True
        assert m2.validation_report["overfit"]["oos_sharpe"] == 1.2


class TestOverfitCheck:
    def test_pass_both_segments_positive(self):
        """样本内外都上涨 → 通过"""
        data = _price_df([(100, 140, 150), (140, 180, 60)])
        out = run_overfit_check(_PeriodicStrategy, data)
        assert out["passed"] is True, out["reason"]
        assert out["is_sharpe"] > 0 and out["oos_sharpe"] > 0

    def test_fail_oos_negative(self):
        """样本内上涨、样本外下跌 → oos 夏普 < 0 → 不通过"""
        data = _price_df([(100, 140, 150), (140, 90, 60)])
        out = run_overfit_check(_PeriodicStrategy, data)
        assert out["passed"] is False
        assert out["oos_sharpe"] < 0
        assert "样本外" in out["reason"]

    def test_fail_oos_decay(self):
        """样本外夏普 < 0.5×样本内 → 衰减过快不通过"""
        # 样本内强趋势、样本外几乎走平（夏普≈0）→ 衰减超过容忍
        data = _price_df([(100, 200, 200), (200, 205, 60)])
        out = run_overfit_check(_PeriodicStrategy, data)
        # 可能命中 oos<0 或衰减分支，总之不通过
        assert out["passed"] is False

    def test_insufficient_oos_data(self):
        """样本外不足 20 根 → 明确不通过（总 95 根，80% 切分后 oos=19）"""
        data = _price_df([(100, 140, 80), (140, 150, 15)])
        out = run_overfit_check(_PeriodicStrategy, data)
        assert out["passed"] is False
        assert "不足" in out["reason"]

    def test_empty_data_graceful(self):
        out = run_overfit_check(_PeriodicStrategy, pd.DataFrame())
        assert out["passed"] is False
