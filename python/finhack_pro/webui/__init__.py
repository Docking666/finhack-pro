"""
FinHack Pro WebUI 包
提供基于FastAPI的Web管理界面，用于监控和管理多智能体量化交易系统。
"""

from finhack_pro.webui.app import create_app

__all__ = ["create_app"]
