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

import os
import sys
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
    DataSourceTester,
    MemoryService,
    StreamService,
)
from finhack_pro.webui.strategy_routes import router as strategy_router
from finhack_pro.webui.theme_routes import (
    WALLPAPER_DIR,
    ensure_dirs,
)
from finhack_pro.webui.theme_routes import (
    router as theme_router,
)
from finhack_pro.webui.workshop_routes import router as workshop_router
from finhack_pro.webui.ws_routes import router as ws_router

# 默认只允许「本机 + 私有网段」来源访问 WebUI。
#
# 背景：WebUI 没有鉴权，而配置里存着 LLM / 数据源的 API Key。原先是
# allow_origins=["*"] + allow_credentials=True —— 这意味着用户浏览器里任意第三方
# 页面（比如某个恶意站点）都能跨域读到本机 8000 端口的响应，简单请求即可命中、
# 连预检都不需要。真正的威胁是公网上的任意站点，因此这里按来源做拦截。
#
# 私有网段（10.x / 192.168.x / 172.16-31.x）仍然放行：这些来源本来就能绕过 CORS
# 直连后端（非浏览器的客户端不受同源策略约束），放行它们不会引入额外风险，
# 但能保住局域网访问场景（app.py 默认绑 0.0.0.0）。
#
# 需要放开到公网来源时，用环境变量显式指定：
#   FINHACK_WEBUI_ALLOW_ORIGINS=https://finhack.example.com
#   FINHACK_WEBUI_ALLOW_ORIGINS=*     （等价旧行为，仅建议在可信网络使用）
_LOCAL_ORIGIN_RE = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|\[::1\]"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)


def _cors_kwargs() -> dict:
    """构建 CORS 中间件参数（优先读环境变量，默认仅限本机来源）"""
    raw = os.environ.get("FINHACK_WEBUI_ALLOW_ORIGINS", "").strip()
    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        return {"allow_origins": origins}
    return {"allow_origin_regex": _LOCAL_ORIGIN_RE}


def get_base_path():
    """获取应用基础路径（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的路径
        return Path(sys._MEIPASS)
    # 开发环境路径
    return Path(__file__).parent.parent.parent  # 返回项目根目录


def get_static_dir():
    """获取静态文件目录（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的路径：sys._MEIPASS/finhack_pro/webui/static
        return Path(sys._MEIPASS) / "finhack_pro" / "webui" / "static"
    # 开发环境路径
    return Path(__file__).parent / "static"


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
    # 默认仅限 localhost / 127.0.0.1 来源（见 _cors_kwargs 注释）。
    # 桌面端从 http://localhost:8000 加载，浏览器直连为 http://127.0.0.1:8000，均可命中。
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        **_cors_kwargs(),
    )

    # ---- 初始化服务 ----
    app.state.config_service = ConfigService(config_path)
    app.state.backtest_service = BacktestService()
    app.state.agent_service = AgentService()
    app.state.memory_service = MemoryService()
    app.state.stream_service = StreamService()
    app.state.data_source_tester = DataSourceTester()
    app.state.start_time = time.time()
    app.state.pipeline_results = {}

    # ---- 注册路由 ----
    app.include_router(api_router)
    app.include_router(export_router)
    app.include_router(strategy_router)
    app.include_router(workshop_router)
    app.include_router(theme_router)
    app.include_router(ws_router)

    # ---- 静态文件目录 ----
    static_dir = get_static_dir()
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    else:
        logger.warning(f"静态文件目录不存在: {static_dir}")

    # ---- 用户壁纸目录（主题系统使用，运行时创建）----
    try:
        ensure_dirs()
        app.mount("/themes/wallpapers", StaticFiles(directory=str(WALLPAPER_DIR)), name="wallpapers")
    except Exception as exc:  # pragma: no cover - 挂载失败不应阻断启动
        logger.warning(f"壁纸目录挂载失败: {exc}")

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
            import traceback
            logger.error(f"Agent系统初始化失败: {e}")
            logger.error(f"详细错误信息:\n{traceback.format_exc()}")
            logger.info("WebUI将以受限模式运行(流水线功能不可用，请检查API配置)")

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


async def reload_agent_system(app: FastAPI) -> bool:
    """重建 Agent 系统（配置变更后使 per-Agent 配置生效）

    停止旧 coordinator → 按当前配置重建 → 启动。

    Returns:
        是否重建成功
    """
    logger.info("正在重建 Agent 系统...")
    try:
        from finhack_pro.agents.coordinator import AgentCoordinator

        # 停止旧的 coordinator
        agent_svc = app.state.agent_service
        if agent_svc._coordinator:
            try:
                await agent_svc._coordinator.stop()
                logger.info("旧 Agent 系统已停止")
            except Exception as e:
                logger.warning(f"停止旧 Agent 系统失败: {e}")

        # 按当前配置重建
        config_data = app.state.config_service.get_full_config()
        coordinator = AgentCoordinator(config_data)
        await coordinator.start()
        agent_svc.set_coordinator(coordinator)
        app.state.memory_service.set_shared_memory(coordinator.shared_memory)
        logger.info("Agent 系统重建成功")
        return True
    except Exception as e:
        import traceback
        logger.error(f"Agent 系统重建失败: {e}")
        logger.error(f"详细错误信息:\n{traceback.format_exc()}")
        return False


if __name__ == "__main__":
    uvicorn.run(
        "finhack_pro.webui.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
