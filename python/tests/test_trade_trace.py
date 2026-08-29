"""阶段2 交易溯源回归测试

- runner 成交时快照 trade['context']（bar_extra/position_volume/signal）
- TradeRecord.context 默认 {}，存量数据零破坏
- services 透传 context
"""

import numpy as np
import pandas as pd
import pytest

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.strategies.mean_reversion import MeanReversionStrategy
from finhack_pro.webui.models import TradeRecord


def _make_oscillating_df(n=300, seed=23):
    """震荡数据：均值回归策略会反复触发超卖买入/超买卖出"""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 100 + np.cumsum(np.random.randn(n) * 1.5)
    # 均值回归回归（拉回 100 附近）
    close = 100 + (close - 100) * 0.7 + np.random.randn(n) * 1.0
    open_ = close - np.random.randn(n) * 0.5
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": np.maximum(open_, close) * 1.01,
        "low": np.minimum(open_, close) * 0.99,
        "close": close,
        "volume": np.random.randint(100_000, 1_000_000, n),
    })


class TestTradeTrace:
    def test_trade_context_snapshot(self):
        """成交的 trade 必须带 context：bar_extra/position_volume/signal"""
        from finhack_pro.webui.services import _precompute_niche_fields

        df = _precompute_niche_fields(_make_oscillating_df())
        strategy = MeanReversionStrategy()
        runner = BacktestRunner()
        result = runner.run(strategy=strategy, symbol="600519.SH", data=df, initial_capital=1_000_000.0)

        assert len(result.trades) > 0, "震荡数据应产生交易"
        for t in result.trades:
            ctx = t.get("context")
            assert ctx is not None, f"trade 缺 context: {t}"
            assert "bar_extra" in ctx, "context 缺 bar_extra"
            assert "position_volume" in ctx, "context 缺 position_volume"
            assert "signal" in ctx, "context 缺 signal"
            assert ctx["signal"]["direction"] in ("buy", "sell")
            assert ctx["signal"]["strategy_name"] == "MeanReversion"
            # 买入时 position_volume 应为 0，卖出时 > 0
            if t["action"] == "buy":
                assert ctx["position_volume"] == 0
            else:
                assert ctx["position_volume"] > 0

    def test_trade_record_context_default_empty(self):
        """TradeRecord.context 默认 {}——存量数据零破坏"""
        rec = TradeRecord(
            date="2024-01-01", symbol="600519.SH", direction="buy",
            price=100.0, volume=100, commission=5.0,
        )
        assert rec.context == {}

    def test_trade_record_context_passthrough(self):
        """services 构造 TradeRecord 时透传 context"""
        ctx = {"bar_extra": {"ma20": 101.5}, "position_volume": 0,
               "signal": {"direction": "buy", "strategy_name": "MeanReversion", "extra": {}}}
        rec = TradeRecord(
            date="2024-01-02", symbol="600519.SH", direction="buy",
            price=98.0, volume=100, commission=5.0,
            reason="MeanReversion", context=ctx,
        )
        assert rec.context == ctx
        assert rec.context["bar_extra"]["ma20"] == 101.5
