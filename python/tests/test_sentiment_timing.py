"""全市场情绪指数（P2② 情绪择时）测试：三场景温度判定 + risk_manager 接入"""

import sys
import types
from unittest.mock import patch

import pandas as pd
import pytest

from finhack_pro.agents.risk_manager import RiskManagerAgent
from finhack_pro.agents.sentiment_index import compute_sentiment_index
from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal


def _install_fake_akshare(module_dict):
    fake = types.ModuleType("akshare")
    for name, obj in module_dict.items():
        setattr(fake, name, obj)
    return patch.dict(sys.modules, {"akshare": fake})


def _df(mean_attention, advancers_ratio, n=100):
    """构造 stock_comment_em 返回的 DataFrame（涨跌幅正比≈advancers_ratio）"""
    up_count = int(n * advancers_ratio)
    pct = [2.0] * up_count + [-2.0] * (n - up_count)
    return pd.DataFrame({
        "序号": range(1, n + 1),
        "代码": [f"{600000 + i}" for i in range(n)],
        "名称": [f"股票{i}" for i in range(n)],
        "最新价": [10.0] * n, "涨跌幅": pct,
        "换手率": [1.0] * n, "市盈率": [20.0] * n,
        "主力成本": [10.0] * n, "机构参与度": [0.3] * n,
        "综合得分": [60.0] * n, "上升": [0] * n,
        "目前排名": list(range(1, n + 1)),
        "关注指数": [mean_attention] * n,
        "交易日": ["2026-08-27"] * n,
    })


class TestSentimentIndex:
    @pytest.mark.asyncio
    async def test_overheated(self):
        """高关注 + 普涨 → 过热"""
        with _install_fake_akshare({"stock_comment_em": lambda: _df(75, 0.8)}):
            idx = await compute_sentiment_index()
        assert idx["temperature"] == "overheated"
        assert idx["mean_attention"] == 75.0

    @pytest.mark.asyncio
    async def test_panicky_high_attention_crash(self):
        """高关注 + 普跌 → 恐慌"""
        with _install_fake_akshare({"stock_comment_em": lambda: _df(70, 0.2)}):
            idx = await compute_sentiment_index()
        assert idx["temperature"] == "panicky"

    @pytest.mark.asyncio
    async def test_normal(self):
        """中关注 + 涨跌均衡 → 正常"""
        with _install_fake_akshare({"stock_comment_em": lambda: _df(50, 0.5)}):
            idx = await compute_sentiment_index()
        assert idx["temperature"] == "normal"

    @pytest.mark.asyncio
    async def test_api_failure_degrades_honestly(self):
        """数据失败 → 诚实降级 normal + error（不伪造信号）"""
        def boom():
            raise RuntimeError("接口失败")
        with _install_fake_akshare({"stock_comment_em": boom}):
            idx = await compute_sentiment_index()
        assert idx["temperature"] == "normal"
        assert "error" in idx


class TestRiskManagerSentiment:
    def _signal(self, direction="buy"):
        return StrategySignal(
            symbol="600519.SH", direction=SignalDirection(direction),
            confidence=0.8, position_size_pct=0.1, reasoning="测试",
        )

    def _manager(self, sentiment):
        m = RiskManagerAgent(config={"model": "test", "api_key": "sk-test", "initial_capital": 1_000_000})
        m.set_sentiment_index(sentiment)
        return m

    def test_overheated_rejects(self):
        """情绪过热 → 规则引擎拒绝（降仓/观望）"""
        m = self._manager({"temperature": "overheated", "mean_attention": 75.0, "advancers_ratio": 0.8})
        result = m._rule_engine_check(self._signal())
        assert result["passed"] is False
        assert any("市场情绪" in r for r in result["reasons"])

    def test_panicky_warns_but_allows(self):
        """恐慌 → 不硬拒（预警），其余检查通过时仍通过"""
        m = self._manager({"temperature": "panicky", "mean_attention": 70.0, "advancers_ratio": 0.2})
        result = m._rule_engine_check(self._signal())
        assert any("市场恐慌" in w for w in result["warnings"])

    def test_normal_no_intervention(self):
        """正常温度 → 无情绪干预"""
        m = self._manager({"temperature": "normal", "mean_attention": 50.0, "advancers_ratio": 0.5})
        result = m._rule_engine_check(self._signal())
        assert not any("市场情绪" in r or "市场恐慌" in w for r in result["reasons"] for w in result["warnings"])
