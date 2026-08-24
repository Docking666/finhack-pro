"""
WebUI 服务层和模型测试

测试 ConfigService、BacktestService、AgentService、MemoryService、StreamService
以及所有 Pydantic 数据模型。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from finhack_pro.webui.models import (
    AgentInfo,
    APIResponse,
    BacktestMetrics,
    BacktestRequest,
    BacktestResult,
    BacktestStatus,
    BacktestStrategy,
    ConfigUpdate,
    ConnectionTestRequest,
    ConnectionTestResult,
    DataConfigUpdate,
    DataSourceTestRequest,
    DataSourceTestResult,
    ExecutionConfigUpdate,
    HealthStatus,
    LLMConfigUpdate,
    MemoryEntryResponse,
    MemorySearchRequest,
    MemoryStats,
    PipelineRunRequest,
    PipelineRunResult,
    PipelineStepResult,
    RiskConfigUpdate,
    SystemInfo,
    ToolCallStats,
    ToolInfo,
    ToolParameterInfo,
    TradeRecord,
    WSMessage,
)
from finhack_pro.webui.services import (
    AgentService,
    BacktestService,
    ConfigService,
    MemoryService,
    StreamService,
)

# ============================================================================
# Pydantic 模型测试
# ============================================================================


class TestModels:
    """Pydantic 数据模型创建和验证测试"""

    # --- 通用模型 ---

    def test_ws_message_creation(self):
        """WSMessage 创建"""
        msg = WSMessage(type="ping", data={"key": "value"})
        assert msg.type == "ping"
        assert msg.data == {"key": "value"}
        assert msg.timestamp  # 自动生成

    def test_api_response_creation(self):
        """APIResponse 创建"""
        resp = APIResponse(success=True, message="ok", data={"id": 1})
        assert resp.success is True
        assert resp.message == "ok"

    def test_api_response_defaults(self):
        """APIResponse 默认值"""
        resp = APIResponse()
        assert resp.success is True
        assert resp.message == ""
        assert resp.data is None

    # --- 系统管理 ---

    def test_system_info_defaults(self):
        """SystemInfo 默认值"""
        info = SystemInfo()
        assert info.name == "FinHack Pro"
        assert info.version == "1.0.0"
        assert info.status == "running"

    def test_health_status_creation(self):
        """HealthStatus 创建"""
        hs = HealthStatus(status="healthy", components={"db": "ok"})
        assert hs.status == "healthy"
        assert hs.components["db"] == "ok"

    # --- 配置管理 ---

    def test_llm_config_update(self):
        """LLMConfigUpdate 创建"""
        cfg = LLMConfigUpdate(temperature=0.5, max_tokens=2048)
        assert cfg.temperature == 0.5
        assert cfg.max_tokens == 2048
        assert cfg.provider is None

    def test_llm_config_update_temperature_validation(self):
        """LLMConfigUpdate temperature 范围校验"""
        with pytest.raises(ValidationError):
            LLMConfigUpdate(temperature=3.0)  # > 2

    def test_data_config_update(self):
        """DataConfigUpdate 创建"""
        cfg = DataConfigUpdate(source="tushare", tushare_token="abc")
        assert cfg.source == "tushare"

    def test_risk_config_update(self):
        """RiskConfigUpdate 创建"""
        cfg = RiskConfigUpdate(max_position_pct=0.5, stop_loss_pct=0.03)
        assert cfg.max_position_pct == 0.5

    def test_risk_config_update_validation(self):
        """RiskConfigUpdate 范围校验"""
        with pytest.raises(ValidationError):
            RiskConfigUpdate(max_position_pct=1.5)  # > 1

    def test_execution_config_update(self):
        """ExecutionConfigUpdate 创建"""
        cfg = ExecutionConfigUpdate(slippage=0.001, commission_rate=0.0003)
        assert cfg.slippage == 0.001

    def test_config_update(self):
        """ConfigUpdate 创建"""
        cfg = ConfigUpdate(
            llm=LLMConfigUpdate(model="gpt-4o"),
            risk=RiskConfigUpdate(stop_loss_pct=0.05),
        )
        assert cfg.llm is not None
        assert cfg.risk is not None
        assert cfg.data is None

    def test_connection_test_request(self):
        """ConnectionTestRequest 创建"""
        req = ConnectionTestRequest(provider="openai", api_key="sk-test")
        assert req.provider == "openai"

    def test_connection_test_request_missing_provider(self):
        """ConnectionTestRequest 缺少 provider 应报错"""
        with pytest.raises(ValidationError):
            ConnectionTestRequest()

    def test_connection_test_result(self):
        """ConnectionTestResult 创建"""
        result = ConnectionTestResult(
            provider="openai", success=True, message="ok", latency_ms=50.0
        )
        assert result.success is True
        assert result.latency_ms == 50.0

    def test_connection_test_request_default_protocol(self):
        """ConnectionTestRequest 默认 protocol=openai"""
        req = ConnectionTestRequest(provider="deepseek")
        assert req.protocol == "openai"

    def test_connection_test_request_explicit_protocol(self):
        """ConnectionTestRequest 显式 protocol=anthropic"""
        req = ConnectionTestRequest(provider="anthropic", protocol="anthropic")
        assert req.protocol == "anthropic"

    def test_data_source_test_request(self):
        """DataSourceTestRequest 创建"""
        req = DataSourceTestRequest(source="tushare", tushare_token="x")
        assert req.source == "tushare"
        assert req.tushare_token == "x"

    def test_data_source_test_request_missing_source(self):
        """DataSourceTestRequest 缺 source 应报错"""
        with pytest.raises(ValidationError):
            DataSourceTestRequest()

    def test_data_source_test_result(self):
        """DataSourceTestResult 创建"""
        result = DataSourceTestResult(source="akshare", success=True, message="ok", latency_ms=10.0)
        assert result.source == "akshare"
        assert result.success is True

    # --- 回测管理 ---

    def test_backtest_strategy_enum(self):
        """BacktestStrategy 枚举"""
        assert BacktestStrategy.DUAL_THRUST == "dual_thrust"
        assert BacktestStrategy.MOMENTUM == "momentum"
        assert BacktestStrategy.MEAN_REVERSION == "mean_reversion"

    def test_backtest_request_creation(self):
        """BacktestRequest 创建"""
        req = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        assert req.strategy == BacktestStrategy.DUAL_THRUST
        assert req.symbols == ["000001.SZ"]
        assert req.initial_capital == 1_000_000.0

    def test_backtest_request_missing_symbols(self):
        """BacktestRequest 缺少 symbols 应报错"""
        with pytest.raises(ValidationError):
            BacktestRequest(start_date="2024-01-01", end_date="2024-12-31")

    def test_backtest_status_creation(self):
        """BacktestStatus 创建"""
        status = BacktestStatus(task_id="t-001", status="running", progress=50.0)
        assert status.task_id == "t-001"
        assert status.progress == 50.0

    def test_trade_record_creation(self):
        """TradeRecord 创建"""
        trade = TradeRecord(
            date="2024-01-15",
            symbol="000001.SZ",
            direction="buy",
            price=10.5,
            volume=100,
            commission=31.5,
            pnl=200.0,
            reason="signal",
        )
        assert trade.direction == "buy"
        assert trade.pnl == 200.0

    def test_backtest_metrics_defaults(self):
        """BacktestMetrics 默认值"""
        metrics = BacktestMetrics()
        assert metrics.total_return == 0.0
        assert metrics.total_trades == 0

    def test_backtest_metrics_creation(self):
        """BacktestMetrics 创建"""
        metrics = BacktestMetrics(
            total_return=15.5,
            sharpe_ratio=1.8,
            max_drawdown=8.2,
            total_trades=42,
        )
        assert metrics.total_return == 15.5
        assert metrics.sharpe_ratio == 1.8

    def test_backtest_result_creation(self):
        """BacktestResult 创建"""
        result = BacktestResult(
            task_id="t-001",
            status="completed",
            metrics=BacktestMetrics(total_return=10.0),
        )
        assert result.task_id == "t-001"
        assert result.metrics.total_return == 10.0
        assert result.equity_curve == []
        assert result.trades == []

    # --- Agent 管理 ---

    def test_agent_info_creation(self):
        """AgentInfo 创建"""
        agent = AgentInfo(
            agent_id="market_analyzer",
            name="市场分析Agent",
            role="market_analyzer",
            status="running",
            message_count=5,
        )
        assert agent.agent_id == "market_analyzer"
        assert agent.status == "running"

    def test_agent_info_defaults(self):
        """AgentInfo 默认值"""
        agent = AgentInfo(agent_id="test", name="Test", role="test")
        assert agent.status == "idle"
        assert agent.message_count == 0

    def test_pipeline_run_request(self):
        """PipelineRunRequest 创建"""
        req = PipelineRunRequest(symbol="000001.SZ")
        assert req.symbol == "000001.SZ"
        assert req.market_data is None

    def test_pipeline_step_result(self):
        """PipelineStepResult 创建"""
        step = PipelineStepResult(step=1, agent_name="市场分析", status="completed")
        assert step.step == 1

    def test_pipeline_run_result(self):
        """PipelineRunResult 创建"""
        result = PipelineRunResult(
            run_id="r-001",
            symbol="000001.SZ",
            status="completed",
        )
        assert result.steps == []
        assert result.final_signal is None

    # --- 共享记忆 ---

    def test_memory_search_request(self):
        """MemorySearchRequest 创建"""
        req = MemorySearchRequest(keywords=["market", "analysis"], limit=10)
        assert req.keywords == ["market", "analysis"]
        assert req.limit == 10

    def test_memory_search_request_limit_validation(self):
        """MemorySearchRequest limit 范围校验"""
        with pytest.raises(ValidationError):
            MemorySearchRequest(limit=300)  # > 200

    def test_memory_entry_response(self):
        """MemoryEntryResponse 创建"""
        entry = MemoryEntryResponse(
            id="mem-001",
            memory_type="analysis",
            agent_id="market_analyzer",
            content="市场分析结果...",
            importance="high",
            timestamp="2024-01-01T00:00:00",
        )
        assert entry.tags == []
        assert entry.decay_score == 1.0

    def test_memory_stats_defaults(self):
        """MemoryStats 默认值"""
        stats = MemoryStats()
        assert stats.total_memories == 0
        assert stats.by_type == {}

    # --- 工具集 ---

    def test_tool_parameter_info(self):
        """ToolParameterInfo 创建"""
        param = ToolParameterInfo(
            name="symbol", type="str", description="标的代码", required=True
        )
        assert param.required is True

    def test_tool_info(self):
        """ToolInfo 创建"""
        tool = ToolInfo(
            name="get_price",
            description="获取价格",
            category="data",
            parameters=[
                ToolParameterInfo(
                    name="symbol", type="str", description="标的", required=True
                )
            ],
        )
        assert len(tool.parameters) == 1

    def test_tool_call_stats(self):
        """ToolCallStats 创建"""
        stats = ToolCallStats(
            total_tools=5,
            total_calls=100,
            call_counts={"get_price": 30},
        )
        assert stats.total_tools == 5


# ============================================================================
# ConfigService 测试
# ============================================================================


class TestConfigService:
    """ConfigService 测试"""

    def setup_method(self):
        """每个测试前重置全局配置"""
        from finhack_pro.config import reset_config
        reset_config()

    def teardown_method(self):
        """每个测试后重置全局配置"""
        from finhack_pro.config import reset_config
        reset_config()

    def test_get_config_masks_sensitive_fields(self):
        """get_config 隐藏敏感字段"""
        svc = ConfigService()
        config = svc.get_config()
        # openai_api_key 应被隐藏
        llm_cfg = config.get("llm", {})
        key = llm_cfg.get("openai_api_key", "")
        # 默认为空字符串，应返回空
        assert key == "" or "****" in key

    def test_get_full_config_returns_raw(self):
        """get_full_config 返回完整配置"""
        svc = ConfigService()
        config = svc.get_full_config()
        assert "llm" in config
        assert "data" in config
        assert "risk" in config

    def test_get_full_config_no_masking(self):
        """get_full_config 不隐藏敏感字段"""
        svc = ConfigService()
        config = svc.get_full_config()
        llm_cfg = config.get("llm", {})
        # 完整配置应包含原始值（可能是空字符串）
        assert "openai_api_key" in llm_cfg

    def test_update_config(self):
        """update_config 更新配置"""
        svc = ConfigService()
        result = svc.update_config({
            "llm": {"model": "gpt-4o-mini", "temperature": 0.5}
        })
        assert result["llm"]["model"] == "gpt-4o-mini"

    def test_update_config_ignores_none_values(self):
        """update_config 忽略 None 值"""
        svc = ConfigService()
        original = svc.get_full_config()
        original_model = original["llm"]["model"]
        svc.update_config({"llm": {"model": None}})
        # model 不应被改变
        updated = svc.get_full_config()
        assert updated["llm"]["model"] == original_model

    def test_update_config_unknown_section_ignored(self):
        """update_config 忽略未知 section"""
        svc = ConfigService()
        result = svc.update_config({"nonexistent": {"key": "value"}})
        assert "nonexistent" not in result

    def test_save_config(self, tmp_path):
        """save_config 保存到文件"""
        save_path = str(tmp_path / "test_config.yaml")
        svc = ConfigService()
        result_path = svc.save_config(save_path)
        assert result_path == save_path
        import os
        assert os.path.exists(save_path)

    def test_mask_sensitive_short_value(self):
        """_mask_sensitive 短敏感值用 **** 替代"""
        svc = ConfigService()
        result = svc._mask_sensitive({"openai_api_key": "abc"})
        assert result["openai_api_key"] == "****"

    def test_mask_sensitive_long_value(self):
        """_mask_sensitive 长敏感值部分隐藏"""
        svc = ConfigService()
        result = svc._mask_sensitive({"openai_api_key": "sk-1234567890abcdef"})
        assert result["openai_api_key"].startswith("sk")
        assert "****" in result["openai_api_key"]
        assert result["openai_api_key"].endswith("ef")

    def test_mask_sensitive_empty_value(self):
        """_mask_sensitive 空敏感值返回空字符串"""
        svc = ConfigService()
        result = svc._mask_sensitive({"openai_api_key": ""})
        assert result["openai_api_key"] == ""

    def test_mask_sensitive_nested(self):
        """_mask_sensitive 递归处理嵌套字典"""
        svc = ConfigService()
        result = svc._mask_sensitive({
            "llm": {
                "openai_api_key": "sk-longsecretkey",
                "model": "gpt-4o",
            }
        })
        assert "****" in result["llm"]["openai_api_key"]
        assert result["llm"]["model"] == "gpt-4o"

    def test_mask_sensitive_non_sensitive(self):
        """_mask_sensitive 非敏感字段不变"""
        svc = ConfigService()
        result = svc._mask_sensitive({"model": "gpt-4o", "temperature": 0.5})
        assert result["model"] == "gpt-4o"
        assert result["temperature"] == 0.5


# ============================================================================
# BacktestService 测试
# ============================================================================


def _mock_ohlcv():
    """构造真实结构的 OHLCV DataFrame（缓慢上升保证 final_equity>0）"""
    import numpy as np
    import pandas as pd

    dates = pd.bdate_range("2024-01-01", "2024-12-31")
    n = len(dates)
    close = 100 * (1 + 0.001 * np.arange(n))
    return pd.DataFrame({
        "date": dates,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": 1_000_000,
    })


def _empty_df():
    """空 DataFrame（模拟数据获取失败）"""
    import pandas as pd

    return pd.DataFrame()


class TestBacktestService:
    """BacktestService 测试"""

    def test_create_task(self):
        """create_task 创建回测任务"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        status = svc.create_task(request)
        assert status.task_id
        assert status.status == "pending"
        assert status.start_time is not None

    def test_get_task_status_exists(self):
        """get_task_status 获取已存在任务的状态"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        created = svc.create_task(request)
        status = svc.get_task_status(created.task_id)
        assert status is not None
        assert status.task_id == created.task_id

    def test_get_task_status_not_exists(self):
        """get_task_status 不存在的任务返回 None"""
        svc = BacktestService()
        status = svc.get_task_status("nonexistent")
        assert status is None

    def test_get_task_result_not_exists(self):
        """get_task_result 不存在的结果返回 None"""
        svc = BacktestService()
        result = svc.get_task_result("nonexistent")
        assert result is None

    def test_get_history_empty(self):
        """get_history 初始为空"""
        svc = BacktestService()
        history = svc.get_history()
        assert history == []

    @pytest.mark.asyncio
    async def test_run_task_completes(self):
        """run_task 完成回测任务"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        status = svc.create_task(request)
        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher_cls.return_value.get_daily.return_value = _mock_ohlcv()
            await svc.run_task(status.task_id)

        result = svc.get_task_result(status.task_id)
        assert result is not None
        assert result.status == "completed"
        assert result.metrics is not None
        assert result.metrics.final_equity > 0

    @pytest.mark.asyncio
    async def test_run_task_updates_status(self):
        """run_task 更新任务状态"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        status = svc.create_task(request)
        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher_cls.return_value.get_daily.return_value = _mock_ohlcv()
            await svc.run_task(status.task_id)

        updated_status = svc.get_task_status(status.task_id)
        assert updated_status is not None
        assert updated_status.status == "completed"
        assert updated_status.progress == 100

    @pytest.mark.asyncio
    async def test_run_task_populates_history(self):
        """run_task 完成后历史记录非空"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        status = svc.create_task(request)
        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher_cls.return_value.get_daily.return_value = _mock_ohlcv()
            await svc.run_task(status.task_id)

        history = svc.get_history()
        assert len(history) == 1
        assert history[0]["task_id"] == status.task_id

    @pytest.mark.asyncio
    async def test_run_task_nonexistent_no_error(self):
        """run_task 对不存在的任务不崩溃"""
        svc = BacktestService()
        await svc.run_task("nonexistent")  # 不应抛异常

    @pytest.mark.asyncio
    async def test_run_task_with_stream_callback(self):
        """run_task 带流式回调"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        status = svc.create_task(request)

        messages = []
        async def callback(msg):
            messages.append(msg)

        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher_cls.return_value.get_daily.return_value = _mock_ohlcv()
            await svc.run_task(status.task_id, stream_callback=callback)
        # 应该有进度消息和完成消息
        assert len(messages) > 0
        types = [m["type"] for m in messages]
        assert "backtest_completed" in types

    @pytest.mark.asyncio
    async def test_run_task_failed_when_no_data(self):
        """数据获取失败 → 任务标记 failed，无结果"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        status = svc.create_task(request)
        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher_cls.return_value.get_daily.return_value = _empty_df()
            await svc.run_task(status.task_id)

        updated = svc.get_task_status(status.task_id)
        assert updated is not None
        assert updated.status == "failed"
        assert "无法获取" in updated.message
        assert svc.get_task_result(status.task_id) is None

    @pytest.mark.asyncio
    async def test_run_task_failed_streams_backtest_failed(self):
        """数据获取失败 → 流式回调收到 backtest_failed 而非 completed"""
        svc = BacktestService()
        request = BacktestRequest(
            symbols=["000001.SZ"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
        status = svc.create_task(request)

        messages = []
        async def callback(msg):
            messages.append(msg)

        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher_cls.return_value.get_daily.return_value = _empty_df()
            await svc.run_task(status.task_id, stream_callback=callback)

        types = [m["type"] for m in messages]
        assert "backtest_failed" in types
        assert "backtest_completed" not in types


# ============================================================================
# AgentService 测试
# ============================================================================


class TestAgentService:
    """AgentService 测试"""

    @pytest.mark.asyncio
    async def test_get_agent_list_without_coordinator(self):
        """无协调器时返回默认 Agent 列表"""
        svc = AgentService()
        agents = await svc.get_agent_list()
        assert len(agents) > 0
        # 所有 Agent 应为 idle 状态
        for agent in agents:
            assert agent.status == "idle"

    @pytest.mark.asyncio
    async def test_get_agent_list_with_coordinator(self):
        """有协调器时从协调器获取状态"""
        svc = AgentService()

        mock_coordinator = AsyncMock()
        mock_coordinator.get_agent_status = AsyncMock(return_value={
            "agents": {
                "market_analyzer": {
                    "agent_id": "market_analyzer",
                    "role": "market_analyzer",
                    "running": True,
                },
                "news_analyst": {
                    "agent_id": "news_analyst",
                    "role": "news_analyst",
                    "running": False,
                },
            }
        })

        svc.set_coordinator(mock_coordinator)
        agents = await svc.get_agent_list()

        assert len(agents) == 2
        running = [a for a in agents if a.status == "running"]
        assert len(running) == 1
        assert running[0].agent_id == "market_analyzer"

    @pytest.mark.asyncio
    async def test_get_agent_status_found(self):
        """获取存在的 Agent 状态"""
        svc = AgentService()
        agent = await svc.get_agent_status("market_analyzer")
        assert agent is not None
        assert agent.agent_id == "market_analyzer"

    @pytest.mark.asyncio
    async def test_get_agent_status_not_found(self):
        """获取不存在的 Agent 状态返回 None"""
        svc = AgentService()
        agent = await svc.get_agent_status("nonexistent_agent")
        assert agent is None

    @pytest.mark.asyncio
    async def test_get_agent_list_coordinator_error(self):
        """协调器报错时返回空列表"""
        svc = AgentService()

        mock_coordinator = AsyncMock()
        mock_coordinator.get_agent_status = AsyncMock(side_effect=Exception("timeout"))

        svc.set_coordinator(mock_coordinator)
        agents = await svc.get_agent_list()
        assert agents == []

    def test_set_coordinator(self):
        """set_coordinator 设置协调器"""
        svc = AgentService()
        mock_coord = MagicMock()
        svc.set_coordinator(mock_coord)
        assert svc._coordinator is mock_coord

    def test_get_pipeline_history_empty(self):
        """初始流水线历史为空"""
        svc = AgentService()
        assert svc.get_pipeline_history() == []


# ============================================================================
# MemoryService 测试
# ============================================================================


class TestMemoryService:
    """MemoryService 测试"""

    @pytest.mark.asyncio
    async def test_get_stats_without_shared_memory(self):
        """无共享记忆时返回空统计"""
        svc = MemoryService()
        stats = await svc.get_stats()
        assert isinstance(stats, MemoryStats)
        assert stats.total_memories == 0

    @pytest.mark.asyncio
    async def test_get_stats_with_shared_memory(self):
        """有共享记忆时返回实际统计"""
        svc = MemoryService()

        mock_memory = AsyncMock()
        mock_memory.get_stats = AsyncMock(return_value={
            "total_memories": 42,
            "total_entries_ever": 100,
            "by_type": {"analysis": 20, "trade": 22},
            "by_agent": {"market_analyzer": 30},
        })
        svc.set_shared_memory(mock_memory)

        stats = await svc.get_stats()
        assert stats.total_memories == 42
        assert stats.by_type["analysis"] == 20

    @pytest.mark.asyncio
    async def test_search_without_shared_memory(self):
        """无共享记忆时搜索返回空列表"""
        svc = MemoryService()
        request = MemorySearchRequest(keywords=["test"])
        results = await svc.search(request)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_shared_memory(self):
        """有共享记忆时搜索返回结果"""
        svc = MemoryService()

        # 模拟记忆条目
        mock_entry = MagicMock()
        mock_entry.id = "mem-001"
        mock_entry.memory_type.value = "analysis"
        mock_entry.agent_id = "market_analyzer"
        mock_entry.content = "分析结果"
        mock_entry.importance.value = "high"
        mock_entry.timestamp = "2024-01-01T00:00:00"
        mock_entry.tags = ["market"]
        mock_entry.decay_score = 0.9
        mock_entry.access_count = 3
        mock_entry.summary = "市场分析"

        mock_memory = AsyncMock()
        mock_memory.retrieve = AsyncMock(return_value=[mock_entry])
        svc.set_shared_memory(mock_memory)

        request = MemorySearchRequest(keywords=["分析"])
        results = await svc.search(request)
        assert len(results) == 1
        assert results[0].id == "mem-001"
        assert results[0].content == "分析结果"

    @pytest.mark.asyncio
    async def test_get_recent_without_shared_memory(self):
        """无共享记忆时获取最近记忆返回空"""
        svc = MemoryService()
        results = await svc.get_recent()
        assert results == []

    @pytest.mark.asyncio
    async def test_get_recent_with_shared_memory(self):
        """有共享记忆时获取最近记忆"""
        svc = MemoryService()

        mock_entry = MagicMock()
        mock_entry.id = "mem-002"
        mock_entry.memory_type.value = "trade"
        mock_entry.agent_id = "trade_executor"
        mock_entry.content = "交易记录"
        mock_entry.importance.value = "medium"
        mock_entry.timestamp = "2024-01-02T00:00:00"
        mock_entry.tags = []
        mock_entry.decay_score = 1.0
        mock_entry.access_count = 1
        mock_entry.summary = None

        mock_memory = AsyncMock()
        mock_memory.get_recent = AsyncMock(return_value=[mock_entry])
        svc.set_shared_memory(mock_memory)

        results = await svc.get_recent(limit=10)
        assert len(results) == 1
        assert results[0].id == "mem-002"

    @pytest.mark.asyncio
    async def test_delete_without_shared_memory(self):
        """无共享记忆时删除返回 False"""
        svc = MemoryService()
        result = await svc.delete("mem-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_with_shared_memory(self):
        """有共享记忆时删除"""
        svc = MemoryService()

        mock_memory = AsyncMock()
        mock_memory.delete = AsyncMock(return_value=True)
        svc.set_shared_memory(mock_memory)

        result = await svc.delete("mem-001")
        assert result is True
        mock_memory.delete.assert_called_once_with("mem-001")

    @pytest.mark.asyncio
    async def test_delete_error_returns_false(self):
        """删除失败返回 False"""
        svc = MemoryService()

        mock_memory = AsyncMock()
        mock_memory.delete = AsyncMock(side_effect=Exception("DB error"))
        svc.set_shared_memory(mock_memory)

        result = await svc.delete("mem-001")
        assert result is False

    @pytest.mark.asyncio
    async def test_get_stats_error_returns_empty(self):
        """统计查询失败返回空统计"""
        svc = MemoryService()

        mock_memory = AsyncMock()
        mock_memory.get_stats = AsyncMock(side_effect=Exception("error"))
        svc.set_shared_memory(mock_memory)

        stats = await svc.get_stats()
        assert stats.total_memories == 0


# ============================================================================
# StreamService 测试
# ============================================================================


class TestStreamService:
    """StreamService 测试"""

    def test_initial_channels(self):
        """初始频道包含 backtest、agents、system"""
        svc = StreamService()
        assert "backtest" in svc._channels
        assert "agents" in svc._channels
        assert "system" in svc._channels

    @pytest.mark.asyncio
    async def test_connect(self):
        """connect 注册连接到频道"""
        svc = StreamService()
        mock_ws = AsyncMock()
        await svc.connect("backtest", mock_ws)
        assert svc.get_connection_count("backtest") == 1
        assert mock_ws in svc._channels["backtest"]

    @pytest.mark.asyncio
    async def test_connect_new_channel(self):
        """connect 自动创建新频道"""
        svc = StreamService()
        mock_ws = AsyncMock()
        await svc.connect("custom_channel", mock_ws)
        assert "custom_channel" in svc._channels
        assert svc.get_connection_count("custom_channel") == 1

    @pytest.mark.asyncio
    async def test_disconnect(self):
        """disconnect 从频道移除连接"""
        svc = StreamService()
        mock_ws = AsyncMock()
        await svc.connect("backtest", mock_ws)
        assert svc.get_connection_count("backtest") == 1

        await svc.disconnect("backtest", mock_ws)
        assert svc.get_connection_count("backtest") == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self):
        """disconnect 不存在的连接不崩溃"""
        svc = StreamService()
        mock_ws = AsyncMock()
        await svc.disconnect("backtest", mock_ws)  # 不应报错

    @pytest.mark.asyncio
    async def test_broadcast(self):
        """broadcast 向频道所有连接广播消息"""
        svc = StreamService()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await svc.connect("backtest", ws1)
        await svc.connect("backtest", ws2)

        message = {"type": "progress", "value": 50}
        await svc.broadcast("backtest", message)

        ws1.send_json.assert_called_once_with(message)
        ws2.send_json.assert_called_once_with(message)

    @pytest.mark.asyncio
    async def test_broadcast_dead_connection_removed(self):
        """broadcast 自动清理断开的连接"""
        svc = StreamService()
        ws_ok = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_json = AsyncMock(side_effect=Exception("Connection closed"))

        await svc.connect("backtest", ws_ok)
        await svc.connect("backtest", ws_dead)
        assert svc.get_connection_count("backtest") == 2

        await svc.broadcast("backtest", {"type": "test"})
        assert svc.get_connection_count("backtest") == 1

    @pytest.mark.asyncio
    async def test_broadcast_nonexistent_channel(self):
        """broadcast 到不存在的频道不崩溃"""
        svc = StreamService()
        await svc.broadcast("nonexistent", {"type": "test"})  # 不应报错

    @pytest.mark.asyncio
    async def test_broadcast_all(self):
        """broadcast_all 向所有频道广播"""
        svc = StreamService()
        ws_backtest = AsyncMock()
        ws_agents = AsyncMock()
        ws_system = AsyncMock()

        await svc.connect("backtest", ws_backtest)
        await svc.connect("agents", ws_agents)
        await svc.connect("system", ws_system)

        message = {"type": "system_notification"}
        await svc.broadcast_all(message)

        ws_backtest.send_json.assert_called_once_with(message)
        ws_agents.send_json.assert_called_once_with(message)
        ws_system.send_json.assert_called_once_with(message)

    def test_get_connection_count(self):
        """get_connection_count 返回正确连接数"""
        svc = StreamService()
        assert svc.get_connection_count("backtest") == 0
        assert svc.get_connection_count("nonexistent") == 0

    @pytest.mark.asyncio
    async def test_get_connection_count_after_connects(self):
        """多次连接后连接数正确"""
        svc = StreamService()
        for _ in range(5):
            await svc.connect("agents", AsyncMock())
        assert svc.get_connection_count("agents") == 5

    def test_on_pong(self):
        """on_pong 更新最后响应时间"""
        import time
        svc = StreamService()
        mock_ws = MagicMock()
        # 先手动设置元数据
        svc._connection_meta[mock_ws] = {"last_pong": 0, "channel": "backtest"}

        svc.on_pong(mock_ws)
        assert svc._connection_meta[mock_ws]["last_pong"] > 0

    def test_on_pong_unknown_connection(self):
        """on_pong 对未知连接不崩溃"""
        svc = StreamService()
        svc.on_pong(MagicMock())  # 不应报错


# ============================================================================
# 协议驱动连接测试（test-connection 不再白名单拒绝，未知 provider 按 openai）
# ============================================================================


class TestConnectionProtocolTests:
    """test_connection 协议驱动测试（全 mock 不触网）"""

    async def _make_svc(self):
        from finhack_pro.webui.services import ConfigService
        return ConfigService()

    def test_openai_unknown_provider_not_rejected(self):
        """未知 provider 按 openai 协议尝试，不再拒绝"""
        import asyncio

        from finhack_pro.webui.services import ConfigService

        svc = ConfigService()
        called = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {"data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]}

            @property
            def text(self):
                return ""

        async def fake_get(url, headers=None):
            called["url"] = url
            called["headers"] = headers
            return FakeResp()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = fake_get
            result = asyncio.run(svc.test_connection(
                provider="deepseek",
                api_key="sk-test",
                base_url="https://api.deepseek.com/v1",
            ))

        assert result.success is True
        assert result.provider == "deepseek"
        assert called["url"] == "https://api.deepseek.com/v1/models"
        assert called["headers"]["Authorization"] == "Bearer sk-test"

    def test_openai_custom_base_url(self):
        """自定义 base_url 精确拼 /models"""
        import asyncio

        from finhack_pro.webui.services import ConfigService

        svc = ConfigService()
        called = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {"data": []}

            @property
            def text(self):
                return ""

        async def fake_get(url, headers=None):
            called["url"] = url
            return FakeResp()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = fake_get
            asyncio.run(svc.test_connection(
                provider="orca",
                api_key="sk-test",
                base_url="https://api.orcarouter.ai/v1",
            ))

        assert called["url"] == "https://api.orcarouter.ai/v1/models"

    def test_openai_missing_key(self):
        """缺 key → 失败且提示"""
        import asyncio

        from finhack_pro.webui.services import ConfigService

        svc = ConfigService()
        result = asyncio.run(svc.test_connection(
            provider="openai",
            api_key="",
            base_url="https://api.openai.com/v1",
        ))
        assert result.success is False
        assert "API Key 未配置" in result.message

    def test_anthropic_protocol_custom_base_url(self):
        """anthropic 协议 + 自定义 base_url → 归一为 /v1/messages"""
        import asyncio

        from finhack_pro.webui.services import ConfigService

        svc = ConfigService()
        called = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {}

            @property
            def text(self):
                return ""

        async def fake_post(url, headers=None, json=None):
            called["url"] = url
            called["headers"] = headers
            called["body"] = json
            return FakeResp()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = fake_post
            result = asyncio.run(svc.test_connection(
                provider="anthropic",
                api_key="sk-ant-test",
                base_url="https://custom.example",
                protocol="anthropic",
            ))

        assert result.success is True
        assert called["url"] == "https://custom.example/v1/messages"
        assert called["headers"]["x-api-key"] == "sk-ant-test"
        assert called["body"]["model"] == "claude-3-haiku-20240307"

    def test_anthropic_protocol_default_url(self):
        """anthropic 无 base_url → 官方默认端点"""
        import asyncio

        from finhack_pro.webui.services import ConfigService

        svc = ConfigService()
        called = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {}

            @property
            def text(self):
                return ""

        async def fake_post(url, headers=None, json=None):
            called["url"] = url
            return FakeResp()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = fake_post
            asyncio.run(svc.test_connection(
                provider="anthropic",
                api_key="sk-ant-test",
                protocol="anthropic",
            ))

        assert called["url"] == "https://api.anthropic.com/v1/messages"

    def test_anthropic_protocol_base_url_with_v1(self):
        """anthropic base_url 以 /v1 结尾 → 补 /messages"""
        from finhack_pro.webui.services import ConfigService
        assert ConfigService._normalize_anthropic_url("https://x.example/v1") == "https://x.example/v1/messages"

    def test_anthropic_missing_key(self):
        """anthropic 缺 key → 失败且提示"""
        import asyncio

        from finhack_pro.webui.services import ConfigService

        svc = ConfigService()
        result = asyncio.run(svc.test_connection(
            provider="anthropic",
            api_key="",
            protocol="anthropic",
        ))
        assert result.success is False
        assert "Anthropic API Key 未配置" in result.message

    def test_no_whitelist_rejection(self):
        """完全未知 provider 不返回'不支持的服务商'，走 openai 路径"""
        import asyncio

        from finhack_pro.webui.services import ConfigService

        svc = ConfigService()

        class FakeResp:
            status_code = 200

            def json(self):
                return {"data": [{"id": "model-x"}]}

            @property
            def text(self):
                return ""

        async def fake_get(url, headers=None):
            return FakeResp()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = fake_get
            result = asyncio.run(svc.test_connection(
                provider="totally-unknown",
                api_key="sk-test",
                base_url="https://anywhere.example/v1",
            ))

        assert result.success is True
        assert "不支持的服务商" not in result.message


# ============================================================================
# 数据源测试（akshare / tushare，独立于 LLM 测试；全 mock 不触网）
# ============================================================================


class TestDataSourceTester:
    """DataSourceTester 测试（用 sys.modules 假模块）"""

    def _make_tester(self):
        from finhack_pro.webui.services import DataSourceTester
        return DataSourceTester()

    def test_tushare_success(self):
        """tushare 成功：trade_cal 返回 6 行"""
        import asyncio
        import sys
        import types

        # 假 tushare 模块
        fake_ts = types.ModuleType("tushare")
        fake_pro = types.SimpleNamespace(
            trade_cal=lambda **kw: __import__("pandas").DataFrame({"cal_date": ["20240101"] * 6})
        )
        fake_ts.set_token = lambda x: None
        fake_ts.pro_api = lambda: fake_pro

        tester = self._make_tester()
        with patch.dict(sys.modules, {"tushare": fake_ts}):
            result = asyncio.run(tester.test_connection(source="tushare", tushare_token="tok"))

        assert result.success is True
        assert result.source == "tushare"
        assert "6 条" in result.message

    def test_tushare_missing_token(self):
        """tushare 无 token → 失败"""
        import asyncio

        from finhack_pro.webui.services import DataSourceTester

        tester = DataSourceTester()
        result = asyncio.run(tester.test_connection(source="tushare", tushare_token=""))
        assert result.success is False
        assert "Token 未配置" in result.message

    def test_tushare_import_error(self):
        """tushare 未安装 → 提示安装（sys.modules 置 None 模拟导入失败）"""
        import asyncio
        import sys

        tester = self._make_tester()
        with patch.dict(sys.modules, {"tushare": None}):
            result = asyncio.run(tester.test_connection(source="tushare", tushare_token="tok"))
        assert result.success is False
        assert "tushare包未安装" in result.message

    def test_akshare_success(self):
        """akshare 成功：stock_zh_a_hist 返回数据"""
        import asyncio
        import sys
        import types

        import pandas as pd

        fake_ak = types.ModuleType("akshare")
        fake_ak.stock_zh_a_hist = lambda **kw: pd.DataFrame({"date": ["2024-01-01"] * 3})
        fake_ak.__name__ = "akshare"

        tester = self._make_tester()
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            result = asyncio.run(tester.test_connection(source="akshare"))

        assert result.success is True
        assert "3 条" in result.message

    def test_akshare_no_data(self):
        """akshare 返回空数据 → 失败"""
        import asyncio
        import sys
        import types

        import pandas as pd

        fake_ak = types.ModuleType("akshare")
        fake_ak.stock_zh_a_hist = lambda **kw: pd.DataFrame()
        fake_ak.__name__ = "akshare"

        tester = self._make_tester()
        with patch.dict(sys.modules, {"akshare": fake_ak}):
            result = asyncio.run(tester.test_connection(source="akshare"))

        assert result.success is False
        assert "未获取到数据" in result.message

    def test_akshare_import_error(self):
        """akshare 未安装 → 提示安装（sys.modules 置 None 模拟导入失败）"""
        import asyncio
        import sys

        tester = self._make_tester()
        with patch.dict(sys.modules, {"akshare": None}):
            result = asyncio.run(tester.test_connection(source="akshare"))
        assert result.success is False
        assert "akshare包未安装" in result.message

    def test_unknown_source(self):
        """未知数据源 → 失败"""
        import asyncio

        tester = self._make_tester()
        result = asyncio.run(tester.test_connection(source="wind"))
        assert result.success is False
        assert "不支持的数据源" in result.message
