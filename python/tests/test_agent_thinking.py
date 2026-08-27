"""
思维链（CoT）跨 Agent 传递测试

覆盖:
- thinking 字段默认空（向后兼容）
- thinking 序列化往返
- debate context 渲染 thinking
- coordinator 透传 thinking
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from finhack_pro.agents.market_analyzer import (
    MarketAnalysisReport,
    MarketState,
    RiskLevel,
    TrendDirection,
)
from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent


def _make_market_report(thinking="均线纠缠，量能不足，判断震荡"):
    return MarketAnalysisReport(
        symbol="600519.SH",
        market_state=MarketState.SIDEWAYS,
        trend_direction=TrendDirection.FLAT,
        confidence=0.6,
        risk_level=RiskLevel.MEDIUM,
        thinking=thinking,
    )


class TestThinkingField:
    def test_thinking_default_empty(self):
        """不传 thinking → 默认空串（向后兼容）"""
        from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
        from finhack_pro.agents.micro_event_agent import MicroEventReport
        from finhack_pro.agents.news_analyst import NewsAnalysisReport

        r = MarketAnalysisReport(
            symbol="x", market_state=MarketState.SIDEWAYS,
            trend_direction=TrendDirection.FLAT, confidence=0.5, risk_level=RiskLevel.MEDIUM,
        )
        assert r.thinking == ""
        assert NewsAnalysisReport(symbol="x").thinking == ""
        assert FundamentalAnalysisReport(symbol="x").thinking == ""
        assert MicroEventReport(symbol="x").thinking == ""

    def test_thinking_roundtrip(self):
        """thinking 序列化/反序列化往返保留"""
        report = _make_market_report()
        data = report.model_dump()
        assert data["thinking"] == "均线纠缠，量能不足，判断震荡"
        restored = MarketAnalysisReport.model_validate(data)
        assert restored.thinking == report.thinking


class TestDebateContextThinking:
    def test_build_debate_context_includes_thinking(self):
        """_build_debate_context 渲染 analysis_report 的 thinking"""
        agent = StrategyGeneratorAgent(config={"model": "test"})
        report = _make_market_report()
        ctx = agent._build_debate_context(report)
        assert "分析推理" in ctx
        assert "均线纠缠" in ctx

    def test_build_debate_context_no_thinking_no_error(self):
        """无 thinking 的报告用 getattr 不抛错"""
        agent = StrategyGeneratorAgent(config={"model": "test"})
        report = _make_market_report(thinking="")
        ctx = agent._build_debate_context(report)
        assert "分析推理" not in ctx  # 空 thinking 不渲染

    @pytest.mark.asyncio
    async def test_debate_prompt_contains_thinking(self):
        """debate 三轮 prompt 都包含上游 thinking"""
        from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
        from finhack_pro.agents.micro_event_agent import MicroEventReport
        from finhack_pro.agents.news_analyst import NewsAnalysisReport

        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        llm = MagicMock()
        judge_json = json.dumps({
            "bull_arguments": ["a"], "bear_arguments": ["b"],
            "bull_strength": 0.6, "bear_strength": 0.4,
            "consensus": "bullish", "confidence": 0.7,
            "key_debates": ["x"], "conclusion": "看多",
        })
        # 用真实 async 函数记录调用（side_effect 列表模式下 call_args 不记录）
        captured = []

        async def _fake_chat(message, **kwargs):
            captured.append(message)
            cycle = (len(captured) - 1) % 3  # 每轮 3 次：多/空/裁
            if cycle == 0:
                return "多头论点"
            if cycle == 1:
                return "空头论点"
            return judge_json

        llm.chat = AsyncMock(side_effect=_fake_chat)
        llm._extract_json = MagicMock(side_effect=lambda t: json.loads(t))
        # mock LLM 返回真实结构对象（SDD：不依赖 fallback 兜底）
        from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal
        llm.chat_structured = AsyncMock(return_value=StrategySignal(
            symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.7,
        ))
        agent._llm = llm

        report = _make_market_report()
        news = NewsAnalysisReport(symbol="600519.SH", thinking="新闻情绪偏中性")
        fund = FundamentalAnalysisReport(symbol="600519.SH", thinking="估值合理")
        micro = MicroEventReport(symbol="600519.SH", thinking="有并购事件")

        await agent.debate(
            analysis_report=report,
            news_report=news,
            fundamental_report=fund,
            micro_event_report=micro,
        )
        # 多轮辩论（分歧 0.2 不收敛 → 2 轮共 6 次调用），所有 prompt 都应包含 thinking
        assert len(captured) == 6, f"chat 应调用6次，实际{len(captured)}"
        for prompt in captured:
            has_thinking = any(
                t in prompt for t in ("均线纠缠", "新闻情绪", "估值合理", "并购事件")
            )
            assert has_thinking, f"prompt 缺少 thinking: {prompt[:100]}"


class TestCoordinatorThinkingPassthrough:
    @pytest.mark.asyncio
    async def test_coordinator_passthrough_thinking(self, tmp_path):
        """coordinator 流水线中报告 thinking 传递给下游 debate"""
        from finhack_pro.agents.coordinator import AgentCoordinator
        from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
        from finhack_pro.agents.micro_event_agent import MicroEventReport
        from finhack_pro.agents.news_analyst import NewsAnalysisReport
        from finhack_pro.agents.risk_manager import RiskDecision
        from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal
        from finhack_pro.agents.trade_executor import ExecutionReport, OrderSide

        config = {
            "llm": {"provider": "openai", "openai_api_key": "sk-test", "model": "test"},
            "pipeline": {"output_dir": str(tmp_path / "pipeline")},
        }
        coord = AgentCoordinator(config)

        coord.market_analyzer.analyze = AsyncMock(return_value=_make_market_report("市场看多"))
        coord.news_analyst.analyze = AsyncMock(return_value=NewsAnalysisReport(symbol="600519.SH", thinking="新闻偏多"))
        coord.fundamental_analyst.analyze = AsyncMock(return_value=FundamentalAnalysisReport(symbol="600519.SH", thinking="基本面强"))
        coord.micro_event_agent.scan_events = AsyncMock(return_value=MicroEventReport(symbol="600519.SH", thinking="事件利好"))
        coord.strategy_generator.debate = AsyncMock(return_value=StrategySignal(
            symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.8,
        ))
        coord.risk_manager.evaluate_risk = AsyncMock(return_value=RiskDecision(symbol="600519.SH", approved=True))
        coord.trade_executor.execute = AsyncMock(return_value=ExecutionReport(
            order_id="test-order", symbol="600519.SH", side=OrderSide.BUY,
            price=1500.0, volume=100, status="filled", filled_volume=100,
        ))

        await coord.run_analysis_pipeline("600519.SH", run_id="thinking_test_1", resume=True)

        # 断言 debate 收到的报告对象带 thinking
        call_kwargs = coord.strategy_generator.debate.await_args.kwargs
        assert call_kwargs["analysis_report"].thinking == "市场看多"
        assert call_kwargs["news_report"].thinking == "新闻偏多"
        assert call_kwargs["fundamental_report"].thinking == "基本面强"
        assert call_kwargs["micro_event_report"].thinking == "事件利好"


class TestLLMReasoning:
    """LLM 推理文本（reasoning_content）提取与流回调透传"""

    def test_extract_reasoning_attr(self):
        from finhack_pro.agents.llm_client import LLMClient

        class _Msg:
            reasoning_content = "逐步推理：趋势向上"

        assert LLMClient._extract_reasoning(_Msg()) == "逐步推理：趋势向上"

    def test_extract_reasoning_model_extra(self):
        from finhack_pro.agents.llm_client import LLMClient

        class _Msg:
            model_extra = {"reasoning_content": "额外字段推理"}

        assert LLMClient._extract_reasoning(_Msg()) == "额外字段推理"

    def test_extract_reasoning_empty(self):
        from finhack_pro.agents.llm_client import LLMClient

        assert LLMClient._extract_reasoning(None) == ""
        assert LLMClient._extract_reasoning(object()) == ""

    def test_stream_callbacks_auto_stream(self):
        """注入实例级回调后 chat 自动启用流式并合并回调（agent 零改动透传）"""
        import asyncio
        from unittest.mock import AsyncMock

        from finhack_pro.agents.llm_client import LLMClient

        client = LLMClient(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-v4-flash",
        )
        # 屏蔽真实网络：替换 _chat_openai 记录调用参数
        captured = {}
        async def fake_chat(messages, system, temperature, max_tokens, tools,
                            tool_choice, stream=False, on_token=None,
                            on_reasoning=None, response_format=None,
                            timeout=None):
            captured["stream"] = stream
            captured["on_token"] = on_token
            captured["on_reasoning"] = on_reasoning
            return "final text"
        client._chat_openai = fake_chat

        tokens, reasons = [], []
        client.set_stream_callbacks(
            on_token=lambda p: tokens.append(p),
            on_reasoning=lambda p: reasons.append(p),
        )
        # 未显式传 stream → 自动流式
        asyncio.run(client.chat(message="你好", system="sys"))
        assert captured["stream"] is True
        assert captured["on_token"] is not None
        assert captured["on_reasoning"] is not None

        # 显式 on_reasoning 与实例级合并：两者都被调用
        explicit = []
        asyncio.run(client.chat(message="你好", on_reasoning=lambda p: explicit.append(p)))
        assert captured["on_reasoning"] is not None
        captured["on_reasoning"]("推理片段")
        assert reasons == ["推理片段"]
        assert explicit == ["推理片段"]

        # 清理后恢复非流式
        client.clear_stream_callbacks()
        asyncio.run(client.chat(message="你好"))
        assert captured["stream"] is False

    def test_base_agent_injects_callbacks(self):
        """BaseAgent.set_llm_stream_callbacks 注入到 _llm"""
        from unittest.mock import MagicMock

        from finhack_pro.agents.news_analyst import NewsAnalystAgent

        agent = NewsAnalystAgent.__new__(NewsAnalystAgent)
        mock_llm = MagicMock()
        agent._llm = mock_llm

        def _cb(p):  # noqa: ANN001 - 测试回调
            return None

        agent.set_llm_stream_callbacks(on_token=_cb, on_reasoning=_cb)
        mock_llm.set_stream_callbacks.assert_called_once_with(on_token=_cb, on_reasoning=_cb)

    def test_base_agent_no_llm_silent(self):
        """未创建 _llm 的 agent 静默跳过，不抛异常"""
        from finhack_pro.agents.news_analyst import NewsAnalystAgent

        agent = NewsAnalystAgent.__new__(NewsAnalystAgent)
        agent._llm = None
        agent.set_llm_stream_callbacks(on_token=lambda p: None)  # 不应抛错

    def test_extract_json_still_static(self):
        """回归：_extract_json 必须保持 @staticmethod（防止装饰器被吞）"""
        from finhack_pro.agents.llm_client import LLMClient

        assert LLMClient._extract_json('{"a": 1}') == {"a": 1}
        client = LLMClient(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-v4-flash",
        )
        assert client._extract_json('{"a": 2}') == {"a": 2}

    def test_extract_json_still_static(self):
        """回归：_extract_json 必须保持 @staticmethod（防止装饰器被吞）"""
        from finhack_pro.agents.llm_client import LLMClient

        assert LLMClient._extract_json('{"a": 1}') == {"a": 1}
        client = LLMClient(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-v4-flash",
        )
        assert client._extract_json('{"a": 2}') == {"a": 2}


class TestRiskPortfolioBaseline:
    """风控初始组合回归：总资产/可用资金必须是初始资金而非 0"""

    def test_default_portfolio_is_initial_capital(self):
        from finhack_pro.agents.risk_manager import RiskManagerAgent

        agent = RiskManagerAgent(config={})
        assert agent._portfolio.total_value == 1_000_000
        assert agent._portfolio.cash == 1_000_000
        assert agent._portfolio.positions == []

    def test_portfolio_custom_initial_capital(self):
        from finhack_pro.agents.risk_manager import RiskManagerAgent

        agent = RiskManagerAgent(config={"initial_capital": 500_000})
        assert agent._portfolio.total_value == 500_000
        assert agent._portfolio.cash == 500_000

    def test_risk_context_contains_real_assets(self):
        """LLM 风控上下文必须含初始资金，而非总资产为 0"""
        from finhack_pro.agents.risk_manager import RiskManagerAgent
        from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal

        agent = RiskManagerAgent(config={})
        signal = StrategySignal(
            symbol="600519.SH",
            direction=SignalDirection.BUY,
            confidence=0.7,
            position_size_pct=0.2,
            stop_loss=1200.0,
            take_profit=1400.0,
            strategy_type="debate",
            time_horizon="5d",
            reasoning="测试信号",
        )
        ctx = agent._build_risk_context(signal, {"reasons": []})
        assert "总资产: 1000000.00" in ctx
        assert "可用资金: 1000000.00" in ctx
        assert "总资产: 0.00" not in ctx


class TestResumeTransparency:
    """断点恢复透明化：已恢复步骤按顺序推送事件，前端步骤顺序完整"""

    @pytest.mark.asyncio
    async def test_resume_emits_recovered_steps(self, tmp_path):
        from finhack_pro.agents.coordinator import AgentCoordinator
        from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
        from finhack_pro.agents.micro_event_agent import MicroEventReport
        from finhack_pro.agents.news_analyst import NewsAnalysisReport
        from finhack_pro.agents.risk_manager import RiskDecision
        from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal
        from finhack_pro.agents.trade_executor import ExecutionReport, OrderSide

        config = {
            "llm": {"provider": "openai", "openai_api_key": "sk-test", "model": "test"},
            "pipeline": {"output_dir": str(tmp_path / "pipeline")},
        }
        coord = AgentCoordinator(config)
        events = []

        async def on_event(ev):
            events.append(ev)

        def _mock_all():
            coord.market_analyzer.analyze = AsyncMock(return_value=_make_market_report("市场看多"))
            coord.news_analyst.analyze = AsyncMock(return_value=NewsAnalysisReport(symbol="600519.SH", thinking="新闻偏多"))
            coord.fundamental_analyst.analyze = AsyncMock(return_value=FundamentalAnalysisReport(symbol="600519.SH", thinking="基本面强"))
            coord.micro_event_agent.scan_events = AsyncMock(return_value=MicroEventReport(symbol="600519.SH", thinking="事件利好"))
            coord.strategy_generator.debate = AsyncMock(return_value=StrategySignal(
                symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.8,
            ))
            coord.risk_manager.evaluate_risk = AsyncMock(return_value=RiskDecision(symbol="600519.SH", approved=True))
            coord.trade_executor.execute = AsyncMock(return_value=ExecutionReport(
                order_id="test-order", symbol="600519.SH", side=OrderSide.BUY,
                price=1500.0, volume=100, status="filled", filled_volume=100,
            ))

        _mock_all()
        # 构造"部分完成"：手动标记 Step1-4 已落盘 done（模拟上次中断在 Step5 前）
        for _s, _n in [(1, "market_analysis"), (2, "news_analysis"),
                       (3, "fundamental_analysis"), (4, "micro_event_analysis")]:
            coord._write_report_json("resume_t_1", _s, _n, {"symbol": "600519.SH", "thinking": f"历史结果{_s}"})
            coord._mark_step_done("resume_t_1", _s, _n)

        # resume：Step1-4 已 done → 推送"已从断点恢复"事件；Step5-7 继续执行
        await coord.run_analysis_pipeline(
            "600519.SH", run_id="resume_t_1", resume=True, event_callback=on_event)
        recovered = [e for e in events
                     if e.get("type") == "agent_thought" and "已从断点恢复" in (e.get("content") or "")]
        assert len(recovered) == 4, f"应推送 4 个已恢复事件(Step1-4)，实际 {len(recovered)}"
        steps = [e.get("step") for e in recovered]
        assert steps == [1, 2, 3, 4], f"已恢复事件应按 1-4 顺序，实际 {steps}"
        # Step5-7 继续真实执行（mock 返回）
        ran = [e for e in events
               if e.get("type") == "agent_thought" and "已从断点恢复" not in (e.get("content") or "")]
        assert {e.get("step") for e in ran} >= {5, 6, 7}, f"Step5-7 应继续执行，实际步骤 {sorted(e.get('step') for e in ran)}"


class TestPipelineIsolation:
    """流水线并发隔离：同一时间仅一个流水线，第二个被明确拒绝"""

    @pytest.mark.asyncio
    async def test_second_pipeline_rejected_then_released(self, tmp_path):
        import asyncio as _asyncio

        from finhack_pro.agents.coordinator import (
            AgentCoordinator,
            PipelineBusyError,
        )
        from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
        from finhack_pro.agents.micro_event_agent import MicroEventReport
        from finhack_pro.agents.news_analyst import NewsAnalysisReport
        from finhack_pro.agents.risk_manager import RiskDecision
        from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal
        from finhack_pro.agents.trade_executor import ExecutionReport, OrderSide

        config = {
            "llm": {"provider": "openai", "openai_api_key": "sk-test", "model": "test"},
            "pipeline": {"output_dir": str(tmp_path / "pipeline")},
        }
        coord = AgentCoordinator(config)

        async def _slow_report(*a, **k):
            await _asyncio.sleep(0.3)
            return _make_market_report("慢分析")

        coord.market_analyzer.analyze = AsyncMock(side_effect=_slow_report)
        coord.news_analyst.analyze = AsyncMock(return_value=NewsAnalysisReport(symbol="600519.SH", thinking="新闻偏多"))
        coord.fundamental_analyst.analyze = AsyncMock(return_value=FundamentalAnalysisReport(symbol="600519.SH", thinking="基本面强"))
        coord.micro_event_agent.scan_events = AsyncMock(return_value=MicroEventReport(symbol="600519.SH", thinking="事件利好"))
        coord.strategy_generator.debate = AsyncMock(return_value=StrategySignal(
            symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.8,
        ))
        coord.risk_manager.evaluate_risk = AsyncMock(return_value=RiskDecision(symbol="600519.SH", approved=True))
        coord.trade_executor.execute = AsyncMock(return_value=ExecutionReport(
            order_id="t", symbol="600519.SH", side=OrderSide.BUY,
            price=1500.0, volume=100, status="filled", filled_volume=100,
        ))

        # 第一个流水线（运行中）
        task1 = _asyncio.create_task(coord.run_analysis_pipeline("600519.SH"))
        await _asyncio.sleep(0.05)  # 确保 task1 已获取门禁

        # 第二个立即调用 → 抛 PipelineBusyError
        with pytest.raises(PipelineBusyError):
            await coord.run_analysis_pipeline("600519.SH")

        # 等第一个完成
        await task1

        # 门禁已释放 → 第三个可正常执行
        coord.market_analyzer.analyze = AsyncMock(return_value=_make_market_report("后续分析"))
        r = await coord.run_analysis_pipeline("600519.SH")
        assert r.get("run_id")

    @pytest.mark.asyncio
    async def test_stream_callbacks_restored_after_pipeline(self, tmp_path):
        """流水线结束后各 agent 流回调恢复为注入前状态（防泄漏/并发覆盖）"""
        from finhack_pro.agents.coordinator import AgentCoordinator
        from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
        from finhack_pro.agents.micro_event_agent import MicroEventReport
        from finhack_pro.agents.news_analyst import NewsAnalysisReport
        from finhack_pro.agents.risk_manager import RiskDecision
        from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal
        from finhack_pro.agents.trade_executor import ExecutionReport, OrderSide

        config = {
            "llm": {"provider": "openai", "openai_api_key": "sk-test", "model": "test"},
            "pipeline": {"output_dir": str(tmp_path / "pipeline")},
        }
        coord = AgentCoordinator(config)
        coord.market_analyzer.analyze = AsyncMock(return_value=_make_market_report("市场看多"))
        coord.news_analyst.analyze = AsyncMock(return_value=NewsAnalysisReport(symbol="600519.SH", thinking="新闻偏多"))
        coord.fundamental_analyst.analyze = AsyncMock(return_value=FundamentalAnalysisReport(symbol="600519.SH", thinking="基本面强"))
        coord.micro_event_agent.scan_events = AsyncMock(return_value=MicroEventReport(symbol="600519.SH", thinking="事件利好"))
        coord.strategy_generator.debate = AsyncMock(return_value=StrategySignal(
            symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.8,
        ))
        coord.risk_manager.evaluate_risk = AsyncMock(return_value=RiskDecision(symbol="600519.SH", approved=True))
        coord.trade_executor.execute = AsyncMock(return_value=ExecutionReport(
            order_id="t", symbol="600519.SH", side=OrderSide.BUY,
            price=1500.0, volume=100, status="filled", filled_volume=100,
        ))

        # 注入前：各 agent 流回调应为空
        before = {name: a.get_llm_stream_callbacks()
                  for name, a in coord._agents.items() if hasattr(a, "get_llm_stream_callbacks")}
        assert all(cb == (None, None) for cb in before.values()), "注入前应为空回调"

        await coord.run_analysis_pipeline("600519.SH")

        # 结束后：恢复为注入前状态（空回调）
        after = {name: a.get_llm_stream_callbacks()
                 for name, a in coord._agents.items() if hasattr(a, "get_llm_stream_callbacks")}
        assert after == before, "流水线结束后流回调应恢复原状"
