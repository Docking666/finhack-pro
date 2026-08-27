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

from fastapi import APIRouter, HTTPException, Query, Request

from finhack_pro.webui.models import (
    AgentInfo,
    APIResponse,
    BacktestRequest,
    BacktestResult,
    BacktestStatus,
    ConfigUpdate,
    ConnectionTestRequest,
    ConnectionTestResult,
    DataSourceTestRequest,
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
    DataSourceTester,
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
async def get_system_info(request: Request):
    """获取系统信息"""
    config_svc = _get_config_service(request)
    agent_svc = _get_agent_service(request)
    memory_svc = _get_memory_service(request)

    # 获取Agent和记忆统计
    agent_count = 7  # 默认7个Agent（七智能体架构）
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
async def health_check(request: Request):
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
async def get_config(request: Request):
    """获取当前配置(隐藏敏感字段)"""
    config_svc = _get_config_service(request)
    config = config_svc.get_config()
    return APIResponse(data=config)


@router.get("/api/config/full", response_model=APIResponse)
async def get_full_config(request: Request):
    """获取完整配置(包含敏感字段)"""
    config_svc = _get_config_service(request)
    config = config_svc.get_full_config()
    return APIResponse(data=config)


@router.put("/api/config", response_model=APIResponse)
async def update_config(request: Request, updates: ConfigUpdate):
    """更新配置"""
    config_svc = _get_config_service(request)
    update_dict = updates.model_dump(exclude_none=True)
    updated = config_svc.update_config(update_dict)
    return APIResponse(message="配置已更新", data=updated)


@router.post("/api/config/test-connection", response_model=APIResponse)
async def test_connection(request: Request, req: ConnectionTestRequest):
    """测试API连接（协议驱动：openai 兼容 / anthropic）"""
    config_svc = _get_config_service(request)
    result = await config_svc.test_connection(
        provider=req.provider,
        api_key=req.api_key,
        base_url=req.base_url,
        protocol=req.protocol,
    )
    return APIResponse(data=result.model_dump())


@router.post("/api/data/test-connection", response_model=APIResponse)
async def test_data_source(request: Request, req: DataSourceTestRequest):
    """测试数据源连通性（akshare / tushare）——独立于 LLM 协议测试"""
    tester: DataSourceTester = request.app.state.data_source_tester
    result = await tester.test_connection(
        source=req.source,
        tushare_token=req.tushare_token,
    )
    return APIResponse(data=result.model_dump())


@router.post("/api/config/save", response_model=APIResponse)
async def save_config(request: Request):
    """保存配置到文件，保存后自动重建 Agent 系统使 per-Agent 配置生效"""
    config_svc = _get_config_service(request)
    path = config_svc.save_config()

    # 重建 Agent 系统（per-Agent 配置变更后生效）
    reload_msg = ""
    try:
        from finhack_pro.webui.app import reload_agent_system
        ok = await reload_agent_system(request.app)
        if ok:
            reload_msg = "，Agent 系统已按新配置重建"
        else:
            reload_msg = "，Agent 系统重建失败(受限模式)"
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"保存后重建 Agent 系统失败: {e}")
        reload_msg = "，Agent 系统重建失败"

    return APIResponse(message=f"配置已保存到: {path}{reload_msg}")


@router.post("/api/config/reload-agents", response_model=APIResponse)
async def reload_agents(request: Request):
    """显式重建 Agent 系统（使 per-Agent 配置生效）"""
    try:
        from finhack_pro.webui.app import reload_agent_system
        ok = await reload_agent_system(request.app)
        if ok:
            return APIResponse(message="Agent 系统已按当前配置重建")
        return APIResponse(message="Agent 系统重建失败(受限模式)", success=False)
    except Exception as e:
        return APIResponse(message=f"Agent 系统重建失败: {e}", success=False)


# ============================================================
# 回测管理
# ============================================================

@router.get("/api/backtest/strategies", response_model=APIResponse)
async def list_backtest_strategies():
    """回测可用策略列表：内置策略 + 策略工坊保存的自有策略"""
    from pathlib import Path

    builtin = ["dual_thrust", "momentum", "mean_reversion", "ml_strategy"]
    custom: list = []
    gen_dir = Path("data/generated_strategies")
    if gen_dir.is_dir():
        for d in sorted(gen_dir.iterdir()):
            if not (d / "strategy.py").exists():
                continue
            label = d.name
            manifest = d / "manifest.yaml"
            if manifest.exists():
                try:
                    import yaml
                    m = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
                    label = m.get("strategy_name") or m.get("name") or d.name
                except Exception:
                    pass
            custom.append({"id": d.name, "name": label})
    return APIResponse(data={"builtin": builtin, "custom": custom})


@router.post("/api/backtest/run", response_model=APIResponse)
async def run_backtest(request: Request, req: BacktestRequest):
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
async def get_backtest_status(request: Request, task_id: str):
    """获取回测状态"""
    backtest_svc = _get_backtest_service(request)
    status = backtest_svc.get_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"回测任务不存在: {task_id}")
    return APIResponse(data=status.model_dump())


@router.get("/api/backtest/{task_id}/result", response_model=APIResponse)
async def get_backtest_result(request: Request, task_id: str):
    """获取回测结果"""
    backtest_svc = _get_backtest_service(request)
    result = backtest_svc.get_task_result(task_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"回测结果不存在: {task_id}")
    return APIResponse(data=result.model_dump())


@router.get("/api/backtest/history", response_model=APIResponse)
async def get_backtest_history(request: Request, limit: int = Query(20, ge=1, le=100)):
    """获取历史回测列表"""
    backtest_svc = _get_backtest_service(request)
    history = backtest_svc.get_history(limit=limit)
    return APIResponse(data=history)


# ============================================================
# Agent管理
# ============================================================

@router.get("/api/agents/list", response_model=APIResponse)
async def list_agents(request: Request):
    """获取所有Agent列表和状态"""
    agent_svc = _get_agent_service(request)
    agents = await agent_svc.get_agent_list()
    return APIResponse(data=[a.model_dump() for a in agents])


@router.get("/api/agents/{agent_id}/status", response_model=APIResponse)
async def get_agent_status(request: Request, agent_id: str):
    """获取单个Agent状态"""
    agent_svc = _get_agent_service(request)
    agent = await agent_svc.get_agent_status(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent不存在: {agent_id}")
    return APIResponse(data=agent.model_dump())


@router.post("/api/agents/run-pipeline", response_model=APIResponse)
async def run_pipeline(request: Request, req: PipelineRunRequest):
    """触发一次完整的分析流水线"""
    agent_svc = _get_agent_service(request)
    stream_svc = _get_stream_service(request)

    # 并发隔离：已有流水线在运行 → 409 携带 active_run_id，前端据此提供查看/续跑入口
    coordinator = getattr(agent_svc, "_coordinator", None)
    active_run_id = None
    if coordinator is not None and getattr(coordinator, "_pipeline_active", False):
        for rid, entry in getattr(agent_svc, "_running_pipelines", {}).items():
            _res = entry.get("result")
            if _res is not None and getattr(_res, "status", "") == "running":
                active_run_id = rid
                break
        raise HTTPException(
            status_code=409,
            detail={
                "message": "已有分析流水线正在运行，请等待完成、取消或续跑后再启动新的分析",
                "active_run_id": active_run_id,
            },
        )

    # 先返回run_id（调用方显式传入则复用，否则生成）
    run_id = req.run_id or f"pipeline_{int(time.time())}"
    # 关键：回填到请求对象，确保 services/coordinator/事件推送全程使用同一
    # run_id（此前仅赋局部变量，导致前端拿到的 run_id 与事件里的不一致，
    # 前端无法按 run_id 过滤其它任务的事件流）
    req.run_id = run_id

    async def _run_and_store():
        async def _stream_callback(msg: Dict[str, Any]):
            await stream_svc.broadcast("agents", msg)
        result = await agent_svc.run_pipeline(req, stream_callback=_stream_callback)
        # 存储结果供后续查询
        request.app.state.pipeline_results[run_id] = result

    if not hasattr(request.app.state, "pipeline_results"):
        request.app.state.pipeline_results = {}

    task = asyncio.create_task(_run_and_store())
    # 保存 task 句柄到 service 注册表（供 cancel_pipeline 即时取消）
    entry = getattr(agent_svc, "_running_pipelines", {}).get(run_id)
    if entry is not None:
        entry["task"] = task

    return APIResponse(message="分析流水线已启动", data={"run_id": run_id})


@router.get("/api/agents/pipeline/history", response_model=APIResponse)
async def get_pipeline_history(request: Request, limit: int = Query(20, ge=1, le=100)):
    """获取流水线执行历史"""
    agent_svc = _get_agent_service(request)
    history = agent_svc.get_pipeline_history(limit=limit)
    return APIResponse(data=history)


@router.get("/api/agents/pipeline/list", response_model=APIResponse)
async def list_pipeline_runs(request: Request, limit: int = Query(50, ge=1, le=200)):
    """获取所有流水线任务（内存历史 + 运行中 + 磁盘检查点恢复）"""
    agent_svc = _get_agent_service(request)
    runs = agent_svc.list_pipeline_runs(limit=limit)
    return APIResponse(data=runs)


@router.get("/api/agents/pipeline/{run_id}", response_model=APIResponse)
async def get_pipeline_run(request: Request, run_id: str):
    """查询单个流水线任务状态（运行中 / 历史 / 磁盘检查点）"""
    agent_svc = _get_agent_service(request)
    run = agent_svc.get_pipeline_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"流水线任务不存在: {run_id}")
    return APIResponse(data=run)


@router.post("/api/agents/pipeline/{run_id}/cancel", response_model=APIResponse)
async def cancel_pipeline(request: Request, run_id: str):
    """取消运行中的流水线（协作式标志 + task.cancel 即时中断，落盘 cancelled 终态）"""
    agent_svc = _get_agent_service(request)
    ok = agent_svc.cancel_pipeline(run_id)
    if not ok:
        # 不在运行中：若磁盘检查点存在则已非 running，返回当前状态供前端刷新
        existing = agent_svc.get_pipeline_run(run_id)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"流水线任务不存在: {run_id}")
        return APIResponse(data={"run_id": run_id, "cancel_requested": False, "status": existing.get("status")})
    return APIResponse(message="取消请求已发送", data={"run_id": run_id, "cancel_requested": True})


# ============================================================
# 共享记忆
# ============================================================

@router.get("/api/memory/stats", response_model=APIResponse)
async def get_memory_stats(request: Request):
    """获取记忆统计"""
    memory_svc = _get_memory_service(request)
    stats = await memory_svc.get_stats()
    return APIResponse(data=stats.model_dump())


@router.get("/api/memory/search", response_model=APIResponse)
async def search_memory(request: Request,
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
async def get_recent_memory(request: Request, limit: int = Query(20, ge=1, le=100)):
    """获取最近记忆"""
    memory_svc = _get_memory_service(request)
    results = await memory_svc.get_recent(limit=limit)
    return APIResponse(data=[r.model_dump() for r in results])


@router.delete("/api/memory/{memory_id}", response_model=APIResponse)
async def delete_memory(request: Request, memory_id: str):
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
async def list_tools(request: Request):
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
async def get_tool_stats(request: Request):
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
