"""差异化策略（niche）注册与实时链路测试

覆盖：5 种策略可加载、技术字段预计算、runner bar.extra 注入、微观事件喂入。
"""

import pandas as pd
import pytest

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.strategies.niche_strategy import create_niche_strategy
from finhack_pro.webui.services import _precompute_niche_fields

NICHE_TYPES = [
    "micro_cap", "event_driven", "sentiment_reversal",
    "dragon_tiger_follow", "alternative_cross",
]


def _ohlcv_data(n=80):
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    close = [10.0 + i * 0.03 + (0.02 if i % 7 < 3 else -0.01) for i in range(n)]
    df = pd.DataFrame({
        "date": dates,
        "open": close, "high": [c + 0.3 for c in close], "low": [c - 0.3 for c in close],
        "close": close, "volume": [100000 + (80000 if i % 9 < 2 else 0) for i in range(n)],
    })
    return df


class TestNicheRegistration:
    def test_all_five_niche_strategies_loadable(self):
        """5 种差异化策略均可通过 load_strategy 加载（注册进回测）"""
        for name in NICHE_TYPES:
            s = BacktestRunner.load_strategy(name)
            assert s is not None
            assert s.config.niche_type.value == name

    def test_niche_strategies_in_api_list(self):
        """策略列表包含差异化策略"""
        import asyncio
        from finhack_pro.webui.api_routes import list_backtest_strategies
        resp = asyncio.run(list_backtest_strategies())
        builtin = set(resp.data["builtin"])
        for name in NICHE_TYPES:
            assert name in builtin


class TestNicheFields:
    def test_precompute_niche_fields(self):
        """技术字段预计算：ma20/volume_ratio/rsi/macd_signal"""
        df = _precompute_niche_fields(_ohlcv_data())
        for col in ("ma20", "volume_ratio", "rsi", "macd_signal"):
            assert col in df.columns
        assert df["macd_signal"].isin(["golden_cross", "death_cross", "neutral"]).all()
        # 后期数据不应全空
        tail = df.iloc[-1]
        assert pd.notna(tail["ma20"])
        assert pd.notna(tail["volume_ratio"])

    def test_runner_injects_bar_extra(self):
        """runner 将预计算列注入 BarData.extra（micro_cap 读取不报错）"""
        df = _precompute_niche_fields(_ohlcv_data())
        strategy = create_niche_strategy("micro_cap")
        runner = BacktestRunner()
        result = runner.run(
            strategy=strategy, symbol="600519", data=df,
            initial_capital=1_000_000, commission_rate=0.0003,
            stamp_tax_rate=0.001, slippage=0.001,
        )
        assert result.equity_curve  # 跑通且产出权益曲线

    def test_feed_micro_events(self):
        """Agent 微观事件喂入策略（实时链路：事件驱动信号）"""
        strategy = create_niche_strategy("event_driven")
        events = [
            {"event_id": "e1", "symbol": "600519", "title": "重大资产重组获批",
             "impact_level": "high", "impact_direction": "positive", "time": "2026-08-28T10:00:00"},
        ]
        fed = strategy.feed_micro_events(events)
        assert fed == 1
        assert "600519" in strategy._event_history
        assert len(strategy._event_history["600519"]) == 1

        # build_bar_extra 应识别 7 天内的正面事件
        from datetime import datetime
        extra = strategy.build_bar_extra(symbol="600519", as_of=datetime(2026, 8, 29))
        assert extra.get("has_positive_event") is True
