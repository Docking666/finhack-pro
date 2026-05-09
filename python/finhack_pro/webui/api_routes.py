"""
REST API路由

提供系统管理、配置管理、回测管理、Agent管理、共享记忆、工具集等REST API端点。
"""

from __future__ import annotations

import asyncio
import platform
import time
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from finhack_pro.webui.models import (
    AgentInfo,
    APIResponse,
    BacktestRequest,
    BacktestResult,
    BacktestStatus,
    ConfigUpdate,
    ConnectionTestRequest,
    ConnectionTestResult,
    HealthStatus,
    MemoryEntryResponse,
    MemorySearchRequest,
    MemoryStats,
    PipelineRunRequest,
    PipelineRunResult,
    SystemInfo,
    ToolCallStats,
    ToolInfo,
)
from finhack_pro.webui.services import (
    AgentService,
    BacktestService,
    ConfigService,
    MemoryService,
    StreamService,
)

router = APIRouter()


# ============================================================
# 依赖注入 - 通过app.state管理服务实例
# ============================================================

def _get_config_service(request) -> ConfigService:
    return request.app.state.config_service

def _get_backtest_service(request) -> BacktestService:
    return request.app.state.backtest_service

def _get_agent_service(request) -> AgentService:
    return request.app.state.agent_service

def _get_memory_service(request) -> MemoryService:
    return request.app.state.memory_service

def _get_stream_service(request) -> StreamService:
    return request.app.state.stream_service


# ============================================================
# 系统管理
# ============================================================

@router.get("/api/system/info", response_model=APIResponse)
async def get_system_info(request):
    """获取系统信息"""
    config_svc = _get_config_service(request)
    agent_svc = _get_agent_service(request)
    memory_svc = _get_memory_service(request)

    # 获取Agent和记忆统计
    agent_count = 6  # 默认6个Agent
    memory_count = 0
    tool_count = 7  # 默认7个工具

    try:
        agents = await agent_svc.get_agent_list()
        agent_count = len(agents)
    except Exception:
        pass

    try:
        stats = await memory_svc.get_stats()
        memory_count = stats.total_memories
    except Exception:
        pass

    info = SystemInfo(
        version="1.0.0",
        mode=config_svc._config.environment,
        status="running",
        uptime_seconds=time.time() - request.app.state.start_time,
        python_version=platform.python_version(),
        agent_count=agent_count,
        memory_count=memory_count,
        tool_count=tool_count,
    )
    return APIResponse(data=info.model_dump())


@router.get("/api/system/health", response_model=APIResponse)
async def health_check(request):
    """健康检查"""
    components = {
        "api": "healthy",
        "config": "healthy",
    }

    # 检查Agent系统
    try:
        agent_svc = _get_agent_service(request)
        agents = await agent_svc.get_agent_list()
        components["agents"] = "healthy" if agents else "degraded"
    except Exception:
        components["agents"] = "unhealthy"

    # 检查记忆系统
    try:
        memory_svc = _get_memory_service(request)
        stats = await memory_svc.get_stats()
        components["memory"] = "healthy"
    except Exception:
        components["memory"] = "degraded"

    overall = "healthy"
    if any(v == "unhealthy" for v in components.values()):
        overall = "unhealthy"
    elif any(v == "degraded" for v in components.values()):
        overall = "degraded"

    return APIResponse(data=HealthStatus(
        status=overall,
        components=components,
    ).model_dump())


# ============================================================
# 配置管理
# ============================================================

@router.get("/api/config", response_model=APIResponse)
async def get_config(request):
    """获取当前配置(隐藏敏感字段)"""
    config_svc = _get_config_service(request)
    config = config_svc.get_config()
    return APIResponse(data=config)


@router.get("/api/config/full", response_model=APIResponse)
async def get_full_config(request):
    """获取完整配置(包含敏感字段)"""
    config_svc = _get_config_service(request)
    config = config_svc.get_full_config()
    return APIResponse(data=config)


@router.put("/api/config", response_model=APIResponse)
async def update_config(request, updates: ConfigUpdate):
    """更新配置"""
    config_svc = _get_config_service(request)
    update_dict = updates.model_dump(exclude_none=True)
    updated = config_svc.update_config(update_dict)
    return APIResponse(message="配置已更新", data=updated)


@router.post("/api/config/test-connection", response_model=APIResponse)
async def test_connection(request, req: ConnectionTestRequest):
    """测试API连接"""
    config_svc = _get_config_service(request)
    result = await config_svc.test_connection(
        provider=req.provider,
        api_key=req.api_key,
        base_url=req.base_url,
    )
    return APIResponse(data=result.model_dump())


@router.post("/api/config/save", response_model=APIResponse)
async def save_config(request):
    """保存配置到文件"""
    config_svc = _get_config_service(request)
    path = config_svc.save_config()
    return APIResponse(message=f"配置已保存到: {path}")


# ============================================================
# 回测管理
# ============================================================

@router.post("/api/backtest/run", response_model=APIResponse)
async def run_backtest(request, req: BacktestRequest):
    """启动回测任务"""
    backtest_svc = _get_backtest_service(request)
    stream_svc = _get_stream_service(request)

    # 创建任务
    status = backtest_svc.create_task(req)

    # 在后台执行回测
    async def _run_with_stream(task_id: str):
        async def _stream_callback(msg: Dict[str, Any]):
            await stream_svc.broadcast("backtest", msg)
        await backtest_svc.run_task(task_id, stream_callback=_stream_callback)

    asyncio.create_task(_run_with_stream(status.task_id))

    return APIResponse(message="回测任务已启动", data=status.model_dump())


@router.get("/api/backtest/{task_id}/status", response_model=APIResponse)
async def get_backtest_status(request, task_id: str):
    """获取回测状态"""
    backtest_svc = _get_backtest_service(request)
    status = backtest_svc.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"回测任务不存在: {task_id}")
    return APIResponse(data=status.model_dump())


@router.get("/api/backtest/{task_id}/result", response_model=APIResponse)
async def get_backtest_result(request, task_id: str):
    """获取回测结果"""
    backtest_svc = _get_backtest_service(request)
    result = backtest_svc.get_task_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"回测结果不存在: {task_id}")
    return APIResponse(data=result.model_dump())


@router.get("/api/backtest/history", response_model=APIResponse)
async def get_backtest_history(request, limit: int = Query(20, ge=1, le=100)):
    """获取历史回测列表"""
    backtest_svc = _get_backtest_service(request)
    history = backtest_svc.get_history(limit=limit)
    return APIResponse(data=history)


# ============================================================
# Agent管理
# ============================================================

@router.get("/api/agents/list", response_model=APIResponse)
async def list_agents(request):
    """获取所有Agent列表和状态"""
    agent_svc = _get_agent_service(request)
    agents = await agent_svc.get_agent_list()
    return APIResponse(data=[a.model_dump() for a in agents])


@router.get("/api/agents/{agent_id}/status", response_model=APIResponse)
async def get_agent_status(request, agent_id: str):
    """获取单个Agent状态"""
    agent_svc = _get_agent_service(request)
    agent = await agent_svc.get_agent_status(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent不存在: {agent_id}")
    return APIResponse(data=agent.model_dump())


@router.post("/api/agents/run-pipeline", response_model=APIResponse)
async def run_pipeline(request, req: PipelineRunRequest):
    """触发一次完整的分析流水线"""
    agent_svc = _get_agent_service(request)
    stream_svc = _get_stream_service(request)

    # 在后台执行流水线
    async def _run_with_stream():
        async def _stream_callback(msg: Dict[str, Any]):
            await stream_svc.broadcast("agents", msg)
        await agent_svc.run_pipeline(req, stream_callback=_stream_callback)

    # 先返回run_id
    run_id = f"pipeline_{int(time.time())}"

    async def _run_and_store():
        async def _stream_callback(msg: Dict[str, Any]):
            await stream_svc.broadcast("agents", msg)
        result = await agent_svc.run_pipeline(req, stream_callback=_stream_callback)
        # 存储结果供后续查询
        request.app.state.pipeline_results[run_id] = result

    if not hasattr(request.app.state, "pipeline_results"):
        request.app.state.pipeline_results = {}

    asyncio.create_task(_run_and_store())

    return APIResponse(message="分析流水线已启动", data={"run_id": run_id})


@router.get("/api/agents/pipeline/history", response_model=APIResponse)
async def get_pipeline_history(request, limit: int = Query(20, ge=1, le=100)):
    """获取流水线执行历史"""
    agent_svc = _get_agent_service(request)
    history = agent_svc.get_pipeline_history(limit=limit)
    return APIResponse(data=history)


# ============================================================
# 共享记忆
# ============================================================

@router.get("/api/memory/stats", response_model=APIResponse)
async def get_memory_stats(request):
    """获取记忆统计"""
    memory_svc = _get_memory_service(request)
    stats = await memory_svc.get_stats()
    return APIResponse(data=stats.model_dump())


@router.get("/api/memory/search", response_model=APIResponse)
async def search_memory(
    request,
    memory_type: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    keywords: Optional[str] = Query(None, description="逗号分隔的关键词"),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    importance: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """搜索记忆"""
    memory_svc = _get_memory_service(request)
    search_req = MemorySearchRequest(
        memory_type=memory_type,
        agent_id=agent_id,
        keywords=keywords.split(",") if keywords else None,
        start_time=start_time,
        end_time=end_time,
        importance=importance,
        limit=limit,
    )
    results = await memory_svc.search(search_req)
    return APIResponse(data=[r.model_dump() for r in results])


@router.get("/api/memory/recent", response_model=APIResponse)
async def get_recent_memory(request, limit: int = Query(20, ge=1, le=100)):
    """获取最近记忆"""
    memory_svc = _get_memory_service(request)
    results = await memory_svc.get_recent(limit=limit)
    return APIResponse(data=[r.model_dump() for r in results])


@router.delete("/api/memory/{memory_id}", response_model=APIResponse)
async def delete_memory(request, memory_id: str):
    """删除记忆"""
    memory_svc = _get_memory_service(request)
    success = await memory_svc.delete(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"记忆不存在: {memory_id}")
    return APIResponse(message="记忆已删除")


# ============================================================
# 工具集
# ============================================================

@router.get("/api/tools/list", response_model=APIResponse)
async def list_tools(request):
    """获取所有可用工具"""
    agent_svc = _get_agent_service(request)

    # 如果有协调器，从工具注册中心获取
    tools = []
    if agent_svc._coordinator:
        try:
            definitions = agent_svc._coordinator.tool_registry.list_tools()
            for defn in definitions:
                tools.append(ToolInfo(
                    name=defn.name,
                    description=defn.description,
                    category=defn.category.value,
                    parameters=[
                        {
                            "name": p.name,
                            "type": p.type,
                            "description": p.description,
                            "required": p.required,
                        }
                        for p in defn.parameters
                    ],
                    examples=defn.examples,
                ))
        except Exception:
            pass

    # 如果没有协调器，返回默认工具列表
    if not tools:
        default_tools = [
            ToolInfo(name="fetch_market_data", description="获取A股股票的日线/分钟线行情数据", category="data_fetch"),
            ToolInfo(name="calculate_indicator", description="计算股票的技术指标(RSI/MACD/布林带等)", category="technical"),
            ToolInfo(name="search_news", description="搜索与股票相关的新闻、公告、研报信息", category="news_sentiment"),
            ToolInfo(name="analyze_sentiment", description="对文本进行情感分析", category="news_sentiment"),
            ToolInfo(name="fetch_fundamental", description="获取股票的基本面数据(PE/PB/ROE等)", category="fundamental"),
            ToolInfo(name="get_portfolio_status", description="获取当前投资组合的状态", category="risk"),
            ToolInfo(name="calculate_risk_metrics", description="计算投资组合的风险指标", category="risk"),
        ]
        tools = default_tools

    return APIResponse(data=[t.model_dump() for t in tools])


@router.get("/api/tools/stats", response_model=APIResponse)
async def get_tool_stats(request):
    """获取工具调用统计"""
    agent_svc = _get_agent_service(request)

    if agent_svc._coordinator:
        try:
            stats = agent_svc._coordinator.tool_registry.get_stats()
            return APIResponse(data=ToolCallStats(
                total_tools=stats.get("total_tools", 0),
                total_calls=stats.get("total_calls", 0),
                call_counts=stats.get("call_counts", {}),
                categories=stats.get("categories", []),
            ).model_dump())
        except Exception:
            pass

    return APIResponse(data=ToolCallStats(
        total_tools=7,
        total_calls=0,
        call_counts={},
        categories=["data_fetch", "technical", "news_sentiment", "fundamental", "risk"],
    ).model_dump())
