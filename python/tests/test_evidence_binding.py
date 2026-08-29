"""阶段8 数据绑定试点（辩论环节）回归测试

- tool_registry：call_tool 分配 evidence_id
- debate：证据清单注入 prompt + [ev_N] 引用校验（有效/无效/未引用）
"""

import json

import pytest

from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent


class TestEvidenceId:
    def test_call_tool_assigns_evidence_id(self):
        """call_tool 成功时分配 ev_N 证据 id 并随返回"""
        from finhack_pro.agents.tool_registry import ToolRegistry

        registry = ToolRegistry()
        # 不依赖真实工具注册：直接验证计数器与 persist 输出
        registry._evidence_counter += 1
        registry._call_log.append({
            "tool_name": "fetch_market_data", "caller": "a1", "args": {},
            "success": True, "run_id": "r1",
            "return_summary": "data[10条]", "evidence_id": "ev_1", "timestamp": "t",
        })
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            path = registry.persist(d, run_id="r1")
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
            assert entries[0]["evidence_id"] == "ev_1"


class TestEvidenceRefs:
    def test_valid_refs_detected(self):
        valid, invalid = StrategyGeneratorAgent._check_evidence_refs(["结论基于 [ev_1] 和 [ev_3] 的数据"], ["ev_1", "ev_2", "ev_3"])
        assert valid == ["ev_1", "ev_3"]
        assert invalid == []

    def test_invalid_refs_detected(self):
        valid, invalid = StrategyGeneratorAgent._check_evidence_refs(["引用 [ev_9] 但不存在"], ["ev_1"])
        assert valid == []
        assert invalid == ["ev_9"]

    def test_no_refs(self):
        valid, invalid = StrategyGeneratorAgent._check_evidence_refs(["没有引用"], ["ev_1"])
        assert valid == [] and invalid == []

    def test_multi_field(self):
        valid, invalid = StrategyGeneratorAgent._check_evidence_refs(["a[ev_1]b", "c[ev_5]d", None], ["ev_1"])
        assert valid == ["ev_1"]
        assert invalid == ["ev_5"]


class TestEvidenceBlock:
    def test_block_generation(self, tmp_path):
        """run 目录有 tool_calls.json 时构造证据清单 prompt 块"""
        (tmp_path / "tool_calls.json").write_text(json.dumps([
            {"tool_name": "fetch_market_data", "caller": "market_analyzer",
             "success": True, "run_id": "r1", "return_summary": "data[250条]",
             "evidence_id": "ev_1"},
        ]), encoding="utf-8")
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        block = agent._load_evidence_block(str(tmp_path))
        assert "可用证据清单" in block
        assert "[ev_1]" in block
        assert "fetch_market_data" in block

    def test_no_run_dir_returns_empty(self):
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        assert agent._load_evidence_block(None) == ""
        assert agent._load_evidence_block("") == ""

    def test_no_evidence_ids_returns_empty(self, tmp_path):
        """tool_calls.json 存在但无 evidence_id → 空块（向后兼容）"""
        (tmp_path / "tool_calls.json").write_text(json.dumps([
            {"tool_name": "x", "caller": "a", "success": True, "run_id": "r1",
             "return_summary": "y"},  # 无 evidence_id
        ]), encoding="utf-8")
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        assert agent._load_evidence_block(str(tmp_path)) == ""


class TestDebateEvidenceIntegration:
    """端到端：带证据的 run 目录下，辩论 prompt 含证据清单、结果含引用校验"""

    @pytest.mark.asyncio
    async def test_debate_with_evidence_run_dir(self, tmp_path):
        """证据注入 + 裁判引用 [ev_1] → debate_result.evidence_ids 含 ev_1"""
        from finhack_pro.agents.market_analyzer import MarketAnalysisReport

        (tmp_path / "tool_calls.json").write_text(json.dumps([
            {"tool_name": "fetch_market_data", "caller": "market_analyzer",
             "success": True, "run_id": "r1", "return_summary": "data[250条]",
             "evidence_id": "ev_1"},
        ]), encoding="utf-8")

        class _FakeLLM:
            def __init__(self):
                self.calls = 0
                self.last_message = ""

            def _extract_json(self, response):
                return json.loads(response)

            async def chat_structured(self, message, response_model=None, system=None, **kw):
                return response_model(direction="hold", confidence=0.5, symbol="600519.SH", reasoning="测试")

            async def chat(self, message, system, temperature=0.3, **kw):
                self.calls += 1
                self.last_message = message
                if self.calls % 3 == 1:
                    return '{"arguments": [], "overall_strength": 0.6, "weaknesses": []}'
                if self.calls % 3 == 2:
                    return '{"arguments": [], "overall_strength": 0.5, "weaknesses": []}'
                return json.dumps({
                    "bull_arguments": ["基于 [ev_1] 数据看涨"], "bear_arguments": [],
                    "bull_strength": 0.55, "bear_strength": 0.5,
                    "consensus": "bullish", "confidence": 0.6,
                    "key_debates": [], "conclusion": "参考 [ev_1] 得出结论",
                })

        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        fake = _FakeLLM()
        agent._llm = fake
        agent.max_debate_rounds = 2

        report = MarketAnalysisReport(
            symbol="600519.SH", market_state="sideways", trend_direction="down",
            confidence=0.62, risk_level="low", technical_summary="测试",
        )
        await agent.debate(report, run_dir=str(tmp_path))

        # 多头 prompt 必须含证据清单
        assert "可用证据清单" in fake.last_message or any("可用证据清单" in m for m in [])
        # 辩论结果落盘且 evidence_ids 校验通过
        debate_path = tmp_path / "debate.json"
        assert debate_path.exists()
        data = json.loads(debate_path.read_text(encoding="utf-8"))
        assert data["evidence_ids"] == ["ev_1"]
        assert data["evidence_issues"] == []

    @pytest.mark.asyncio
    async def test_debate_invalid_ref_marked(self, tmp_path):
        """裁判引用不存在的 ev_99 → 标记为未验证来源"""
        from finhack_pro.agents.market_analyzer import MarketAnalysisReport

        (tmp_path / "tool_calls.json").write_text(json.dumps([
            {"tool_name": "x", "caller": "a", "success": True, "run_id": "r1",
             "return_summary": "y", "evidence_id": "ev_1"},
        ]), encoding="utf-8")

        class _FakeLLM:
            def __init__(self):
                self.calls = 0

            def _extract_json(self, response):
                return json.loads(response)

            async def chat_structured(self, message, response_model=None, system=None, **kw):
                return response_model(direction="hold", confidence=0.5, symbol="600519.SH", reasoning="测试")

            async def chat(self, message, system, temperature=0.3, **kw):
                self.calls += 1
                if self.calls % 3 == 1:
                    return '{"arguments": [], "overall_strength": 0.6, "weaknesses": []}'
                if self.calls % 3 == 2:
                    return '{"arguments": [], "overall_strength": 0.5, "weaknesses": []}'
                return json.dumps({
                    "bull_arguments": [], "bear_arguments": [],
                    "bull_strength": 0.55, "bear_strength": 0.5,
                    "consensus": "neutral", "confidence": 0.6,
                    "key_debates": [], "conclusion": "依据 [ev_99] 判断",
                })

        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        fake = _FakeLLM()
        agent._llm = fake
        agent.max_debate_rounds = 2

        report = MarketAnalysisReport(
            symbol="600519.SH", market_state="sideways", trend_direction="down",
            confidence=0.62, risk_level="low", technical_summary="测试",
        )
        await agent.debate(report, run_dir=str(tmp_path))

        data = json.loads((tmp_path / "debate.json").read_text(encoding="utf-8"))
        assert data["evidence_ids"] == []
        assert any("ev_99" in i for i in data["evidence_issues"])
