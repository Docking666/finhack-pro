"""多轮辩论（P1①）测试：收敛早停/轮次上限/情绪上下文注入"""

import json

import pytest

from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent
from finhack_pro.agents.market_analyzer import MarketAnalysisReport


class _FakeLLM:
    """可编程 LLM：按调用轮次返回多头/空头/裁判响应"""

    def __init__(self, bull_strength, bear_strength, rounds_needed=1):
        self.calls = 0
        self.bull_strength = bull_strength
        self.bear_strength = bear_strength
        self.rounds_needed = rounds_needed
        self.last_message = ""
        self.bull_messages = []  # 记录每轮多头 prompt（断言反驳注入）

    def _extract_json(self, response):
        return json.loads(response)

    async def chat_structured(self, message, response_model=None, system=None, **kw):
        return response_model(direction="hold", confidence=0.5, symbol="600519.SH", reasoning="测试信号")

    async def chat(self, message, system, temperature=0.3, **kw):
        self.calls += 1
        self.last_message = message
        if self.calls % 3 == 1:      # 多头
            self.bull_messages.append(message)
            return '{"arguments": [], "overall_strength": 0.8, "weaknesses": []}'
        if self.calls % 3 == 2:      # 空头
            return '{"arguments": [], "overall_strength": 0.4, "weaknesses": []}'
        # 裁判：若已到所需轮次则收敛（分歧小），否则分歧持续
        round_i = (self.calls + 2) // 3
        if round_i >= self.rounds_needed:
            return json.dumps({
                "bull_arguments": [], "bear_arguments": [],
                "bull_strength": 0.55, "bear_strength": 0.5,
                "consensus": "neutral", "confidence": 0.6,
                "key_debates": [], "conclusion": "测试结论",
            })
        return json.dumps({
            "bull_arguments": [], "bear_arguments": [],
            "bull_strength": self.bull_strength, "bear_strength": self.bear_strength,
            "consensus": "bullish", "confidence": 0.5,
            "key_debates": [], "conclusion": "分歧未收敛",
        })


def _report():
    return MarketAnalysisReport(
        symbol="600519.SH",
        market_state="sideways",
        trend_direction="down",
        confidence=0.62,
        risk_level="low",
        technical_summary="测试技术面",
    )


class TestMultiRoundDebate:
    @pytest.mark.asyncio
    async def test_converges_early(self):
        """裁判第 1 轮即收敛（|bull-bear|<0.1）→ 只调用 3 次（1 轮）"""
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        fake = _FakeLLM(0.55, 0.5, rounds_needed=1)
        agent._llm = fake
        agent.max_debate_rounds = 2

        signal = await agent.debate(_report())
        assert fake.calls == 3  # 1 轮 3 次，收敛早停
        assert signal is not None

    @pytest.mark.asyncio
    async def test_continues_when_divergent(self):
        """第 1 轮分歧大 → 进入第 2 轮反驳 → 第 2 轮收敛 → 6 次调用"""
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        fake = _FakeLLM(0.9, 0.1, rounds_needed=2)
        agent._llm = fake
        agent.max_debate_rounds = 2

        await agent.debate(_report())
        assert fake.calls == 6  # 2 轮 × 3 次
        # 第 2 轮多头的 prompt 应包含"需反驳的空头论点"
        assert len(fake.bull_messages) == 2
        assert "需反驳的空头论点" in fake.bull_messages[1]

    @pytest.mark.asyncio
    async def test_stops_at_max_rounds(self):
        """分歧持续不收敛 → 达轮次上限 2 轮停止（不无限循环）"""
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        fake = _FakeLLM(0.9, 0.1, rounds_needed=99)  # 永不收敛
        agent._llm = fake
        agent.max_debate_rounds = 2

        await agent.debate(_report())
        assert fake.calls == 6  # 恰好 2 轮

    def test_sentiment_injected_into_context(self):
        """情绪/关注度分位注入辩论上下文"""
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        ctx = agent._build_debate_context(
            _report(), sentiment_data={
                "discussion_count": 94, "hot_rank": 217, "rank_change": -178, "spike_detected": False,
            },
        )
        assert "市场情绪与关注度" in ctx
        assert "94" in ctx
        assert "217" in ctx
