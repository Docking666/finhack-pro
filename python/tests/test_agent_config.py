"""
per-Agent 独立 LLM 配置、服务商预置、多空辩论测试

覆盖:
- config.py: agents 段解析、空串归一、PROVIDER_PRESETS
- services.py: update_config agents 合并、execution→backtest 映射、敏感字段掩码
- coordinator.py: per-Agent 配置分发（覆盖 + 跟随全局）
- strategy_generator.py: debate 三轮流程
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from finhack_pro.config import PROVIDER_PRESETS, FinhackProConfig
from finhack_pro.webui.services import ConfigService

# ============================================================
# config.py
# ============================================================

class TestAgentLLMConfig:
    def test_agents_section_parsing(self):
        """agents 段正确解析为 AgentLLMConfig"""
        cfg = FinhackProConfig(**{
            "llm": {"model": "gpt-4o"},
            "agents": {
                "market_analyzer": {"model": "deepseek-chat", "openai_api_key": "sk-test"},
                "strategy_generator": {"openai_base_url": "https://api.orcarouter.ai/v1"},
            },
        })
        assert cfg.agents["market_analyzer"].model == "deepseek-chat"
        assert cfg.agents["market_analyzer"].openai_api_key == "sk-test"
        assert cfg.agents["strategy_generator"].openai_base_url == "https://api.orcarouter.ai/v1"

    def test_empty_string_normalized_to_none(self):
        """空串字段归一为 None，序列化后不出现"""
        cfg = FinhackProConfig(**{
            "agents": {"risk_manager": {"model": "", "openai_api_key": ""}},
        })
        d = cfg.model_dump(exclude_none=True)
        assert d.get("agents", {}).get("risk_manager", {}) == {}

    def test_provider_presets(self):
        """服务商预置表完整且 base_url 正确"""
        assert set(PROVIDER_PRESETS.keys()) >= {"orca", "deepseek", "openai", "zhipu"}
        assert PROVIDER_PRESETS["orca"]["base_url"] == "https://api.orcarouter.ai/v1"
        assert PROVIDER_PRESETS["deepseek"]["base_url"] == "https://api.deepseek.com/v1"

    def test_default_agents_empty(self):
        """默认无 agents 段"""
        cfg = FinhackProConfig()
        assert cfg.agents == {}


# ============================================================
# services.py
# ============================================================

class TestConfigServiceUpdate:
    def setup_method(self):
        self.svc = ConfigService(config_path=None)

    def test_execution_maps_to_backtest(self):
        """前端 execution 段映射到后端 backtest 段"""
        self.svc.update_config({"execution": {"slippage": 0.002}})
        assert self.svc._config.backtest.slippage == 0.002

    def test_agents_merge(self):
        """agents 段合并到配置"""
        self.svc.update_config({"agents": {
            "market_analyzer": {"model": "deepseek-chat", "openai_api_key": "sk-agent"},
            "strategy_generator": {"model": "orcarouter/auto"},
        }})
        agents = self.svc._config.model_dump(exclude_none=True).get("agents", {})
        assert agents["market_analyzer"]["model"] == "deepseek-chat"
        assert agents["market_analyzer"]["openai_api_key"] == "sk-agent"
        assert agents["strategy_generator"]["model"] == "orcarouter/auto"

    def test_agents_empty_removes_override(self):
        """空配置清除该 Agent 覆盖（跟随全局）"""
        self.svc.update_config({"agents": {"strategy_generator": {"model": "x"}}})
        self.svc.update_config({"agents": {"strategy_generator": {}}})
        agents = self.svc._config.model_dump(exclude_none=True).get("agents", {})
        assert "strategy_generator" not in agents

    def test_agents_sensitive_masked(self):
        """per-Agent 的 API key 在 get_config 中掩码"""
        self.svc.update_config({"agents": {"market_analyzer": {"openai_api_key": "sk-secret-value"}}})
        masked = self.svc.get_config()
        ma = masked.get("agents", {}).get("market_analyzer", {})
        assert "sk-secret-value" not in str(ma)
        assert ma.get("openai_api_key", "").startswith("sk")

    def test_update_config_syncs_global(self):
        """update_config 后全局单例同步（strategy_routes 依赖）"""
        from finhack_pro.config import get_config
        self.svc.update_config({"llm": {"model": "deepseek-chat"}})
        assert get_config().llm.model == "deepseek-chat"


# ============================================================
# coordinator.py
# ============================================================

class TestCoordinatorAgentDistribution:
    def test_per_agent_config_distribution(self):
        """per-Agent 配置正确分发：覆盖 + 跟随全局"""
        from finhack_pro.agents.coordinator import AgentCoordinator

        config_data = {
            "llm": {
                "provider": "openai",
                "openai_api_key": "sk-global",
                "openai_base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-chat",
            },
            "agents": {
                "market_analyzer": {"model": "gpt-4o-mini"},
                "strategy_generator": {
                    "openai_api_key": "sk-orca",
                    "openai_base_url": "https://api.orcarouter.ai/v1",
                    "model": "orcarouter/auto",
                },
                "news_analyst": {},
            },
        }
        coord = AgentCoordinator(config_data)

        # 覆盖生效
        assert coord._agents["market_analyzer"].config["model"] == "gpt-4o-mini"
        assert coord._agents["market_analyzer"].config["api_key"] == "sk-global"
        assert coord._agents["strategy_generator"].config["api_key"] == "sk-orca"
        assert coord._agents["strategy_generator"].config["base_url"] == "https://api.orcarouter.ai/v1"
        assert coord._agents["strategy_generator"].config["model"] == "orcarouter/auto"
        # 空 dict 跟随全局
        assert coord._agents["news_analyst"].config["model"] == "deepseek-chat"
        assert coord._agents["news_analyst"].config["api_key"] == "sk-global"


# ============================================================
# strategy_generator.py
# ============================================================

class TestStrategyGeneratorDebate:
    @pytest.mark.asyncio
    async def test_debate_three_rounds(self):
        """debate 执行三轮 LLM 调用并返回 StrategySignal"""
        from finhack_pro.agents.market_analyzer import MarketAnalysisReport
        from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent

        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        llm = MagicMock()
        judge_json = json.dumps({
            "bull_arguments": ["业绩增长"],
            "bear_arguments": ["估值过高"],
            "bull_strength": 0.7,
            "bear_strength": 0.4,
            "consensus": "bullish",
            "confidence": 0.75,
            "key_debates": ["估值"],
            "conclusion": "综合看多",
        })
        llm.chat = AsyncMock(side_effect=["多头论点", "空头论点", judge_json])
        llm._extract_json = MagicMock(side_effect=lambda t: json.loads(t))
        agent._llm = llm

        report = MarketAnalysisReport(
            symbol="600519.SH",
            market_state="sideways",
            trend_direction="flat",
            confidence=0.6,
            risk_level="medium",
        )
        signal = await agent.debate(analysis_report=report, current_price=1500.0)
        assert llm.chat.await_count == 3
        assert signal.symbol == "600519.SH"
        assert signal.direction.value in ("buy", "sell", "hold")

    @pytest.mark.asyncio
    async def test_debate_structured_signal(self):
        """debate 后 chat_structured 正常生成信号"""
        from finhack_pro.agents.market_analyzer import MarketAnalysisReport
        from finhack_pro.agents.strategy_generator import (
            SignalDirection,
            StrategyGeneratorAgent,
            StrategySignal,
        )

        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        llm = MagicMock()
        judge_json = json.dumps({
            "bull_arguments": ["a"], "bear_arguments": ["b"],
            "bull_strength": 0.7, "bear_strength": 0.4,
            "consensus": "bearish", "confidence": 0.8,
            "key_debates": ["x"], "conclusion": "偏空",
        })
        llm.chat = AsyncMock(side_effect=["多头", "空头", judge_json])
        llm._extract_json = MagicMock(side_effect=lambda t: json.loads(t))
        llm.chat_structured = AsyncMock(return_value=StrategySignal(
            symbol="600519.SH", direction=SignalDirection.SELL, confidence=0.8,
            reasoning="辩论后卖出",
        ))
        agent._llm = llm

        report = MarketAnalysisReport(
            symbol="600519.SH", market_state="sideways",
            trend_direction="flat", confidence=0.6, risk_level="medium",
        )
        signal = await agent.debate(analysis_report=report)
        assert signal.direction.value == "sell"
        assert signal.reasoning == "辩论后卖出"

    @pytest.mark.asyncio
    async def test_debate_fallback_on_llm_error(self):
        """LLM 硬错误时抛异常（由 coordinator 回退 generate_strategy）"""
        from finhack_pro.agents.market_analyzer import MarketAnalysisReport
        from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent

        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("LLM 连接失败"))
        agent._llm = llm

        report = MarketAnalysisReport(
            symbol="600519.SH", market_state="sideways",
            trend_direction="flat", confidence=0.6, risk_level="medium",
        )
        with pytest.raises(RuntimeError):
            await agent.debate(analysis_report=report)
