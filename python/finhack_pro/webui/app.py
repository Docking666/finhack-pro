"""
FinHack Pro WebUI - FastAPI主应用

提供Web管理界面，用于监控和管理多智能体量化交易系统。
支持静态文件服务、REST API和WebSocket实时通信。

启动方式:
    cd /workspace/finhack-pro/python
    python -m finhack_pro.webui.app
    # 访问 http://localhost:8000
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from finhack_pro.webui.api_routes import router as api_router
from finhack_pro.webui.export_routes import router as export_router
from finhack_pro.webui.services import (
    AgentService,
    BacktestService,
    ConfigService,
    MemoryService,
    StreamService,
)
from finhack_pro.webui.strategy_routes import router as strategy_router
from finhack_pro.webui.ws_routes import router as ws_router


def create_app(config_path: Optional[str] = None) -> FastAPI:
    """创建FastAPI应用实例

    Args:
        config_path: 配置文件路径

    Returns:
        配置好的FastAPI应用
    """
    app = FastAPI(
        title="FinHack Pro WebUI",
        description="多智能体量化交易系统 - Web管理界面",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # ---- CORS中间件 ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 初始化服务 ----
    app.state.config_service = ConfigService(config_path)
    app.state.backtest_service = BacktestService()
    app.state.agent_service = AgentService()
    app.state.memory_service = MemoryService()
    app.state.stream_service = StreamService()
    app.state.start_time = time.time()
    app.state.pipeline_results = {}

    # ---- 注册路由 ----
    app.include_router(api_router)
    app.include_router(export_router)
    app.include_router(strategy_router)
    app.include_router(ws_router)

    # ---- 静态文件目录 ----
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ---- 页面路由 ----
    @app.get("/", response_class=FileResponse)
    async def index():
        """主页面"""
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/dashboard", response_class=FileResponse)
    async def dashboard():
        """仪表盘页面(兼容直接访问)"""
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/config", response_class=FileResponse)
    async def config_page():
        """配置页面(兼容直接访问)"""
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/backtest", response_class=FileResponse)
    async def backtest_page():
        """回测页面(兼容直接访问)"""
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/agents", response_class=FileResponse)
    async def agents_page():
        """Agent页面(兼容直接访问)"""
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/memory", response_class=FileResponse)
    async def memory_page():
        """记忆页面(兼容直接访问)"""
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/workshop", response_class=FileResponse)
    async def workshop_page():
        """策略工坊页面(兼容直接访问)"""
        return FileResponse(str(static_dir / "index.html"))

    # ---- 启动事件 ----
    @app.on_event("startup")
    async def on_startup():
        """应用启动时初始化"""
        logger.info("=" * 60)
        logger.info("FinHack Pro WebUI 启动中...")
        logger.info("=" * 60)

        # 尝试初始化Agent系统(可选)
        try:
            from finhack_pro.agents.coordinator import AgentCoordinator
            config_data = app.state.config_service.get_full_config()
            coordinator = AgentCoordinator(config_data)
            await coordinator.start()
            app.state.agent_service.set_coordinator(coordinator)
            app.state.memory_service.set_shared_memory(coordinator.shared_memory)
            logger.info("Agent系统初始化成功")
        except Exception as e:
            logger.warning(f"Agent系统初始化失败(不影响WebUI使用): {e}")
            logger.info("WebUI将以演示模式运行")

        logger.info("FinHack Pro WebUI 启动完成")
        logger.info("访问 http://localhost:8000 打开管理界面")

    # ---- 关闭事件 ----
    @app.on_event("shutdown")
    async def on_shutdown():
        """应用关闭时清理"""
        logger.info("FinHack Pro WebUI 正在关闭...")

        # 停止Agent系统
        agent_svc = app.state.agent_service
        if agent_svc._coordinator:
            try:
                await agent_svc._coordinator.stop()
                logger.info("Agent系统已停止")
            except Exception as e:
                logger.error(f"Agent系统停止失败: {e}")

    return app


# 默认应用实例(用于 python -m 启动)
app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "finhack_pro.webui.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
