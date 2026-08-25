"""
WebSocket路由

提供回测实时进度、Agent思考过程流、系统事件等WebSocket端点。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from finhack_pro.webui.services import StreamService

router = APIRouter()


def _get_stream_service(websocket) -> StreamService:
    """从app.state获取StreamService"""
    return websocket.app.state.stream_service


# ============================================================
# WebSocket连接管理
# ============================================================

@router.websocket("/ws/backtest")
async def ws_backtest(websocket: WebSocket):
    """回测实时进度推送

    推送消息类型:
    - backtest_progress: 回测进度更新
    - backtest_completed: 回测完成
    - backtest_failed: 回测失败
    """
    await websocket.accept()
    stream_svc = _get_stream_service(websocket)
    await stream_svc.connect("backtest", websocket)

    try:
        while True:
            # 保持连接，接收客户端消息(心跳等)
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                elif msg.get("type") == "pong":
                    # 客户端响应服务端心跳 ping，更新最后活跃时间
                    stream_svc.on_pong(websocket)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        await stream_svc.disconnect("backtest", websocket)
    except Exception:
        await stream_svc.disconnect("backtest", websocket)


@router.websocket("/ws/agents")
async def ws_agents(websocket: WebSocket):
    """Agent思考过程实时流

    推送消息类型:
    - pipeline_started: 流水线开始
    - agent_thinking: Agent开始思考(加载状态)
    - agent_thought: Agent思考完成(包含完整思考内容)
    - agent_message: Agent间消息传递
    - debate_argument: 辩论论点(多头/空头)
    - pipeline_completed: 流水线完成
    """
    await websocket.accept()
    stream_svc = _get_stream_service(websocket)
    await stream_svc.connect("agents", websocket)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                elif msg.get("type") == "pong":
                    stream_svc.on_pong(websocket)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        await stream_svc.disconnect("agents", websocket)
    except Exception:
        await stream_svc.disconnect("agents", websocket)


@router.websocket("/ws/system")
async def ws_system(websocket: WebSocket):
    """系统事件推送

    推送消息类型:
    - system_log: 系统日志
    - system_error: 错误告警
    - performance_metrics: 性能指标
    - config_changed: 配置变更通知
    """
    await websocket.accept()
    stream_svc = _get_stream_service(websocket)
    await stream_svc.connect("system", websocket)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
                elif msg.get("type") == "pong":
                    stream_svc.on_pong(websocket)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        await stream_svc.disconnect("system", websocket)
    except Exception:
        await stream_svc.disconnect("system", websocket)
