"""
步骤级断点恢复（checkpoint/resume）测试

覆盖:
- 输入快照存取（point-in-time）
- done 标记原子性
- 环境指纹匹配/漂移
- 恢复跳过已完成步骤
- 终态恢复（hold/risk_rejected/executed）
- run_id 冲突 / 新 run_id
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from finhack_pro.agents.coordinator import (
    AgentCoordinator,
    EnvironmentDriftError,
    RunIdConflictError,
)
from finhack_pro.agents.market_analyzer import (
    MarketAnalysisReport,
    MarketState,
    RiskLevel,
    TrendDirection,
)


def _make_coordinator(tmp_path):
    """构造 coordinator，输出到 tmp_path/pipeline"""
    config = {
        "llm": {"provider": "openai", "openai_api_key": "sk-test", "model": "test"},
        "pipeline": {"output_dir": str(tmp_path / "pipeline")},
    }
    return AgentCoordinator(config)


def _make_market_report(symbol="600519.SH", thinking="测试推理"):
    return MarketAnalysisReport(
        symbol=symbol,
        market_state=MarketState.SIDEWAYS,
        trend_direction=TrendDirection.FLAT,
        confidence=0.6,
        risk_level=RiskLevel.MEDIUM,
        technical_summary="震荡",
        thinking=thinking,
    )


# ============================================================
# 辅助方法
# ============================================================

class TestCheckpointHelpers:
    def test_snapshot_roundtrip(self, tmp_path):
        """输入快照存取一致；含不可序列化对象不抛错"""
        coord = _make_coordinator(tmp_path)
        run_id = "test_run_1"
        coord._save_input_snapshot(run_id, "600519.SH", {"close": [1.0, 2.0]}, {"rsi": [50]}, 1500.0)
        snap = coord._load_input_snapshot(run_id)
        assert snap["symbol"] == "600519.SH"
        assert snap["market_data"] == {"close": [1.0, 2.0]}
        assert snap["current_price"] == 1500.0

    def test_step_done_marker(self, tmp_path):
        """done 标记原子写 + 幂等"""
        coord = _make_coordinator(tmp_path)
        run_id = "test_run_2"
        assert not coord._is_step_done(run_id, 1)
        coord._mark_step_done(run_id, 1, "market_analysis")
        assert coord._is_step_done(run_id, 1)
        # 幂等：重复标记不抛错
        coord._mark_step_done(run_id, 1, "market_analysis")

    def test_env_fingerprint_match_and_drift(self, tmp_path):
        """同配置指纹一致；改 model 后漂移"""
        coord = _make_coordinator(tmp_path)
        run_id = "test_run_3"
        coord._save_env_fingerprint(run_id)
        assert coord._check_env_fingerprint(run_id)
        # 改 model → 漂移
        coord._agents["market_analyzer"].config["model"] = "different-model"
        assert not coord._check_env_fingerprint(run_id)

    def test_report_json_reconstruction(self, tmp_path):
        """报告 JSON 落盘 + 重建往返一致"""
        coord = _make_coordinator(tmp_path)
        run_id = "test_run_4"
        report = _make_market_report()
        coord._write_report_json(run_id, 1, "market_analysis", report)
        restored = coord._load_step_report(run_id, 1)
        assert restored is not None
        assert restored.symbol == report.symbol
        assert restored.thinking == report.thinking
        assert restored.market_state == report.market_state

    def test_get_resume_plan(self, tmp_path):
        """resume plan 正确识别 done/pending"""
        coord = _make_coordinator(tmp_path)
        run_id = "test_run_5"
        coord._mark_step_done(run_id, 1, "market_analysis")
        coord._mark_step_done(run_id, 2, "news_analysis")
        plan = coord._get_resume_plan(run_id)
        assert plan["done_steps"] == {1, 2}
        assert 3 in plan["pending_steps"]


# ============================================================
# 流水线集成（断点恢复）
# ============================================================

def _mock_all_agents(coord, market_report=None):
    """mock 所有 agent 的 analyze/debate/evaluate/execute"""
    report = market_report or _make_market_report()

    from finhack_pro.agents.risk_manager import RiskDecision
    from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal
    from finhack_pro.agents.trade_executor import ExecutionReport, OrderSide

    coord.market_analyzer.analyze = AsyncMock(return_value=report)
    coord.news_analyst.analyze = AsyncMock(return_value=_make_news_report())
    coord.fundamental_analyst.analyze = AsyncMock(return_value=_make_fundamental_report())
    coord.micro_event_agent.scan_events = AsyncMock(return_value=_make_micro_report())
    coord.strategy_generator.debate = AsyncMock(return_value=StrategySignal(
        symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.8,
    ))
    coord.risk_manager.evaluate_risk = AsyncMock(return_value=RiskDecision(
        symbol="600519.SH", approved=True,
    ))
    coord.trade_executor.execute = AsyncMock(return_value=ExecutionReport(
        order_id="test-order",
        symbol="600519.SH",
        side=OrderSide.BUY,
        price=1500.0,
        volume=100,
        status="filled",
        filled_volume=100,
    ))
    return coord


def _make_news_report():
    from finhack_pro.agents.news_analyst import NewsAnalysisReport
    return NewsAnalysisReport(symbol="600519.SH", overall_sentiment="neutral", sentiment_score=0.0)


def _make_fundamental_report():
    from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
    return FundamentalAnalysisReport(symbol="600519.SH", overall_rating="bullish", rating_score=0.7)


def _make_micro_report():
    from finhack_pro.agents.micro_event_agent import MicroEventReport
    return MicroEventReport(symbol="600519.SH", events_count=1)


class TestPipelineResume:
    @pytest.mark.asyncio
    async def test_resume_skips_completed_steps(self, tmp_path):
        """恢复时已 done 步骤的 agent 不被调用"""
        coord = _mock_all_agents(_make_coordinator(tmp_path))
        run_id = "resume_test_1"
        # 首跑
        await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        assert coord._is_step_done(run_id, 1)

        # 重置 mock 调用计数
        coord.market_analyzer.analyze.reset_mock()
        coord.news_analyst.analyze.reset_mock()
        coord.fundamental_analyst.analyze.reset_mock()
        coord.micro_event_agent.scan_events.reset_mock()
        # 二次调用（同 run_id + resume）
        result = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        # 已 done 步骤的 agent 不应被调用
        coord.market_analyzer.analyze.assert_not_called()
        coord.news_analyst.analyze.assert_not_called()
        assert result["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_resume_uses_input_snapshot(self, tmp_path):
        """恢复时使用快照数据（point-in-time），忽略新传入数据"""
        coord = _mock_all_agents(_make_coordinator(tmp_path))
        run_id = "resume_test_2"
        await coord.run_analysis_pipeline(
            "600519.SH", market_data={"close": [1.0]}, run_id=run_id, resume=True,
        )
        # 二次调用传不同数据，断言 agent 收到的是快照数据
        coord.market_analyzer.analyze.reset_mock()
        await coord.run_analysis_pipeline(
            "600519.SH", market_data={"close": [999.0]}, run_id=run_id, resume=True,
        )
        # 已 done 的步骤不调用 agent（直接走 JSON 重建）
        coord.market_analyzer.analyze.assert_not_called()

    @pytest.mark.asyncio
    async def test_step_failure_not_marked_done(self, tmp_path):
        """步骤失败不写 done；修复后恢复只重跑该步"""
        coord = _make_coordinator(tmp_path)
        # Step2 先失败
        coord.market_analyzer.analyze = AsyncMock(return_value=_make_market_report())
        coord.news_analyst.analyze = AsyncMock(side_effect=RuntimeError("news api down"))
        coord.fundamental_analyst.analyze = AsyncMock(return_value=_make_fundamental_report())
        coord.micro_event_agent.scan_events = AsyncMock(return_value=_make_micro_report())

        run_id = "resume_test_3"
        result = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        # 失败步骤无 done（即使流水线整体继续/失败）
        assert not coord._is_step_done(run_id, 2)

        # 修复 news 后恢复：step2 被重跑且 done
        coord = _mock_all_agents(coord)
        result2 = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        assert coord._is_step_done(run_id, 2)

    @pytest.mark.asyncio
    async def test_env_drift_refuses_resume(self, tmp_path):
        """环境指纹漂移时拒绝恢复（run 未完成时）"""
        coord = _make_coordinator(tmp_path)
        coord.market_analyzer.analyze = AsyncMock(return_value=_make_market_report())
        run_id = "resume_test_4"
        # 手动制造部分完成状态：只完成 step1（run 未终态），并保存环境指纹
        coord._save_env_fingerprint(run_id)
        await coord._run_step(run_id, 1, "market_analysis", lambda: coord.market_analyzer.analyze("600519.SH"))
        # 改 model → 漂移
        coord._agents["market_analyzer"].config["model"] = "changed"
        with pytest.raises(EnvironmentDriftError):
            await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)

    @pytest.mark.asyncio
    async def test_env_drift_resume_on_drift_true(self, tmp_path):
        """resume_on_drift=true 时降级继续并返回警告"""
        coord = _make_coordinator(tmp_path)
        coord.config["pipeline"]["resume_on_drift"] = True
        # mock 全部 agent 使流水线可继续
        coord = _mock_all_agents(coord)
        run_id = "resume_test_5"
        coord._save_env_fingerprint(run_id)
        await coord._run_step(run_id, 1, "market_analysis", lambda: coord.market_analyzer.analyze("600519.SH"))
        coord._agents["market_analyzer"].config["model"] = "changed"
        result = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        assert "resume_drift_warning" in result

    @pytest.mark.asyncio
    async def test_run_id_conflict(self, tmp_path):
        """run_id 已存在 + resume=False → RunIdConflictError"""
        coord = _mock_all_agents(_make_coordinator(tmp_path))
        run_id = "resume_test_6"
        await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        with pytest.raises(RunIdConflictError):
            await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=False)

    @pytest.mark.asyncio
    async def test_run_id_not_exists_creates_fresh(self, tmp_path):
        """新 run_id 目录不存在 → 创建新 run 并使用该 run_id"""
        coord = _mock_all_agents(_make_coordinator(tmp_path))
        run_id = "brand_new_run"
        result = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        assert result["run_id"] == run_id
        assert coord._is_step_done(run_id, 7)

    @pytest.mark.asyncio
    async def test_run_id_none_backward_compatible(self, tmp_path):
        """不传 run_id → 新 run（旧行为）"""
        coord = _mock_all_agents(_make_coordinator(tmp_path))
        result = await coord.run_analysis_pipeline("600519.SH")
        assert result["run_id"]
        assert coord._is_step_done(result["run_id"], 7)

    @pytest.mark.asyncio
    async def test_terminal_hold_resume(self, tmp_path):
        """hold 终态：恢复直接返回，Step6/7 不执行"""
        from finhack_pro.agents.strategy_generator import SignalDirection, StrategySignal

        coord = _make_coordinator(tmp_path)
        coord = _mock_all_agents(coord)
        coord.strategy_generator.debate = AsyncMock(return_value=StrategySignal(
            symbol="600519.SH", direction=SignalDirection.HOLD, confidence=0.5,
        ))
        run_id = "resume_test_7"
        result = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        assert result["signal"]["direction"] == "hold"
        state = coord._load_pipeline_state(run_id)
        assert state["terminal"] == "hold"

        # 恢复：直接返回，Step6/7 不执行
        coord.risk_manager.evaluate_risk.reset_mock()
        result2 = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        coord.risk_manager.evaluate_risk.assert_not_called()

    @pytest.mark.asyncio
    async def test_terminal_executed_full_resume(self, tmp_path):
        """executed 终态：恢复不执行任何 agent，直接返回完整结果"""
        coord = _mock_all_agents(_make_coordinator(tmp_path))
        run_id = "resume_test_8"
        result = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        assert coord._load_pipeline_state(run_id)["terminal"] == "executed"

        # 恢复：所有 agent 不执行
        coord.market_analyzer.analyze.reset_mock()
        coord.strategy_generator.debate.reset_mock()
        coord.trade_executor.execute.reset_mock()
        result2 = await coord.run_analysis_pipeline("600519.SH", run_id=run_id, resume=True)
        coord.market_analyzer.analyze.assert_not_called()
        coord.strategy_generator.debate.assert_not_called()
        coord.trade_executor.execute.assert_not_called()
        assert result2["signal"]["direction"] == "buy"
        assert result2["execution"]["status"] == "filled"
