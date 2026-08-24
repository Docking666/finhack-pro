"""
SDD 回归测试：消除流水线假数据 + 工坊生成失败显式化。

对应方案 swift-thunder-newton.md 的 11 项新增测试。核心原则：
- Agent / LLM / 数据源失败时**真实传播错误**，绝不返回固定默认值伪装成功。
- 测试用 mock LLM 返回**真实结构对象**，不依赖 fallback 兜底。
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from pydantic import BaseModel

from finhack_pro.agents.coordinator import AgentCoordinator
from finhack_pro.agents.fundamental_analyst import FundamentalAnalysisReport
from finhack_pro.agents.micro_event_agent import MicroEventReport
from finhack_pro.agents.news_analyst import NewsAnalysisReport
from finhack_pro.agents.risk_manager import RiskDecision, RiskManagerAgent
from finhack_pro.agents.strategy_generator import (
    SignalDirection,
    StrategyGeneratorAgent,
    StrategySignal,
)
from finhack_pro.agents.trade_executor import ExecutionReport, OrderSide, TradeExecutorAgent
from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.webui.models import (
    APIResponse,
    PipelineRunRequest,
    PipelineRunResult,
    PipelineStepResult,
)


# ---------------------------------------------------------------------------
# 1. 流水线：任一分析步骤失败 → 整体失败并终止（失败即终止）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pipeline_fails_on_step_error(tmp_path):
    config = {
        "llm": {"provider": "openai", "openai_api_key": "sk-test", "model": "test"},
        "pipeline": {"output_dir": str(tmp_path / "pipeline")},
    }
    coord = AgentCoordinator(config)

    # 4 个 Phase1 分析步骤：market 抛错（模拟 LLM/数据失败），其余返回合法报告
    coord.market_analyzer.analyze = AsyncMock(side_effect=RuntimeError("LLM down"))
    coord.news_analyst.analyze = AsyncMock(return_value=NewsAnalysisReport(symbol="600519.SH"))
    coord.fundamental_analyst.analyze = AsyncMock(
        return_value=FundamentalAnalysisReport(symbol="600519.SH")
    )
    coord.micro_event_agent.scan_events = AsyncMock(
        return_value=MicroEventReport(symbol="600519.SH")
    )

    result = await coord.run_analysis_pipeline("600519.SH", run_id="fail_step_1", resume=False)

    # 错误信息真实包含失败原因，且流水线状态落盘为 failed
    assert result.get("error"), "失败流水线必须返回 error"
    assert "LLM down" in result["error"]
    state = coord._load_pipeline_state("fail_step_1")
    assert state["status"] == "failed"


# ---------------------------------------------------------------------------
# 2. risk_manager fail-closed：LLM 失败 → 拒绝交易（approved=False，非假数据）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_risk_manager_fail_closed():
    rm = RiskManagerAgent(config={"model": "test", "api_key": "sk-test"})
    rm._llm = MagicMock()
    rm._llm.chat_structured = AsyncMock(side_effect=RuntimeError("LLM down"))

    signal = StrategySignal(
        symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.8
    )
    decision = await rm._llm_evaluate(
        signal, {"passed": False, "reasons": ["测试"]}
    )
    assert isinstance(decision, RiskDecision)
    assert decision.approved is False
    assert decision.risk_alerts  # 必须带拒绝原因，不能是空壳


# ---------------------------------------------------------------------------
# 3. 工坊策略生成：LLM 返回非 JSON → 显式 success=False（不伪造成功）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_workshop_strategy_parse_failure(monkeypatch):
    from finhack_pro.webui import strategy_routes

    async def _bad_llm(*a, **k):
        return "这不是合法JSON"

    monkeypatch.setattr(strategy_routes, "_call_llm", _bad_llm)

    req = strategy_routes.StrategyGenerateRequest(
        description="这是一个用于回归测试的策略描述文本", instruments="stock"
    )
    resp = await strategy_routes.generate_strategy(req)
    assert isinstance(resp, APIResponse)
    assert resp.success is False
    assert resp.data["validation"]["valid"] is False


# ---------------------------------------------------------------------------
# 4. 工坊因子生成：LLM 返回非 JSON → 显式 success=False
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_workshop_factor_parse_failure(monkeypatch):
    from finhack_pro.webui import strategy_routes

    async def _bad_llm(*a, **k):
        return "这不是合法JSON"

    monkeypatch.setattr(strategy_routes, "_call_llm", _bad_llm)

    req = strategy_routes.FactorGenerateRequest(description="这是一个用于回归测试的因子描述文本")
    resp = await strategy_routes.generate_factor(req)
    assert isinstance(resp, APIResponse)
    assert resp.success is False
    assert resp.data["validation"]["valid"] is False


# ---------------------------------------------------------------------------
# 5. 交易执行：实际执行路径下 LLM 失败 → 真实抛错（不伪造 pending 报告）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_trade_executor_llm_fail():
    ex = TradeExecutorAgent(config={"model": "test", "api_key": "sk-test", "dry_run": False})
    ex._llm = MagicMock()
    ex._llm.chat_structured = AsyncMock(side_effect=RuntimeError("LLM down"))

    signal = StrategySignal(
        symbol="600519.SH", direction=SignalDirection.BUY, confidence=0.8
    )
    with pytest.raises(RuntimeError):
        await ex._execute_with_llm(
            "ORD_TEST", signal, OrderSide.BUY, 1500.0, 100, "market"
        )


# ---------------------------------------------------------------------------
# 6. llm_client：缺必填字段不再填零值/空值（L2 去伪）
# ---------------------------------------------------------------------------
class _MinModel(BaseModel):
    symbol: str
    confidence: float


def test_llm_client_no_default_fill():
    from finhack_pro.agents.llm_client import LLMClient

    # 6a：缺必填字段且无同义 → 返回 None（不再补 0.0 / 空字符串伪装完整）
    res = LLMClient._try_fill_required_fields({}, _MinModel)
    assert res is None

    # 6b：缺 confidence（无同义）→ 仍返回 None（确认未做零值兜底）
    res = LLMClient._try_fill_required_fields({"symbol": "600519.SH"}, _MinModel)
    assert res is None

    # 6c：仅缺 symbol 但存在同义字段 code → 补全后成功（合法同义推断保留）
    res = LLMClient._try_fill_required_fields(
        {"code": "600519.SH", "confidence": 0.5}, _MinModel
    )
    assert res is not None
    assert res["symbol"] == "600519.SH"
    assert res["confidence"] == 0.5


# ---------------------------------------------------------------------------
# 7. services：失败流水线（coordinator 无 signal）→ final_signal=None（不伪造 hold/0.5）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_services_no_fake_signal():
    from finhack_pro.webui.services import AgentService

    svc = object.__new__(AgentService)
    svc._running_pipelines = {}
    svc._pipeline_history = []
    coordinator = MagicMock()
    coordinator._fetch_real_market_data = MagicMock(return_value={})
    coordinator.run_analysis_pipeline = AsyncMock(
        return_value={"symbol": "600519.SH", "error": "分析步骤失败: LLM down"}
    )
    svc._coordinator = coordinator

    req = PipelineRunRequest(symbol="600519.SH")
    result = await svc.run_pipeline(req, stream_callback=None)

    assert isinstance(result, PipelineRunResult)
    assert result.status == "failed"
    assert result.final_signal is None  # 严禁伪造 {hold, 0.5}


# ---------------------------------------------------------------------------
# 8. 数据源：双源均不可用 → get_daily 显式抛 ValueError（不静默返回空 DF）
# ---------------------------------------------------------------------------
def test_get_daily_raises_on_total_failure():
    fetcher = DataFetcher(source="tushare", tushare_token="")
    # 强制两源均不可用（确定性，不依赖真实网络）
    fetcher._tushare_available = False
    fetcher._akshare_available = False

    with pytest.raises(ValueError, match="数据源获取失败"):
        fetcher.get_daily(
            symbol="600519.SH",
            start_date="2024-01-01",
            end_date="2024-02-01",
        )


# ---------------------------------------------------------------------------
# 9. _standardize_columns：缺必需列 → 显式抛错（不填 0.0 伪造价格）
# ---------------------------------------------------------------------------
def test_standardize_columns_no_zero_fill():
    df = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "open": [1.0]})
    with pytest.raises(ValueError, match="数据缺失必要列"):
        DataFetcher._standardize_columns(df)


# ---------------------------------------------------------------------------
# 10. _df_to_market_data：空 DF / 缺列 → 显式抛错（不返回 {} 或零值）
# ---------------------------------------------------------------------------
def test_df_to_market_data_no_zero_fill():
    # 空 DF
    with pytest.raises(ValueError, match="市场数据为空"):
        AgentCoordinator._df_to_market_data(pd.DataFrame())

    # 缺必需列
    df = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "open": [1.0]})
    with pytest.raises(ValueError, match="缺失必要列"):
        AgentCoordinator._df_to_market_data(df)


# ---------------------------------------------------------------------------
# 11. 流水线数据取数失败（L5d）→ services 显式标为 failed，不盲跑
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pipeline_data_fetch_failure():
    from finhack_pro.webui.services import AgentService

    svc = object.__new__(AgentService)
    svc._running_pipelines = {}
    svc._pipeline_history = []
    coordinator = MagicMock()
    # 前端仅传 symbol（market_data=None）→ 触发真实取数；取数失败应显式传播
    coordinator._fetch_real_market_data = MagicMock(
        side_effect=ValueError("数据源获取失败：tushare 与 akshare 均未能返回 600519.SH 的有效行情数据")
    )
    coordinator.run_analysis_pipeline = AsyncMock(
        return_value={"symbol": "600519.SH"}
    )
    svc._coordinator = coordinator

    req = PipelineRunRequest(symbol="600519.SH")  # market_data 默认 None
    result = await svc.run_pipeline(req, stream_callback=None)

    assert isinstance(result, PipelineRunResult)
    assert result.status == "failed"
    assert "数据源获取失败" in (result.error or "")
