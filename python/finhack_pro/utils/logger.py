"""
日志配置模块

基于loguru的统一日志配置，支持文件输出、控制台输出和日志轮转。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger


def setup_logger(
    log_level: str = "INFO",
    log_file: str = "",
    rotation: str = "100 MB",
    retention: str = "30 days",
    format_string: str = "",
) -> None:
    """配置loguru日志

    Args:
        log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_file: 日志文件路径(为空则不输出到文件)
        rotation: 日志轮转大小
        retention: 日志保留时间
        format_string: 自定义日志格式(为空则使用默认格式)
    """
    # 移除默认handler
    logger.remove()

    # 默认格式
    if not format_string:
        format_string = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )

    # 控制台输出
    logger.add(
        sys.stderr,
        level=log_level,
        format=format_string,
        colorize=True,
    )

    # 文件输出
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            str(log_path),
            level=log_level,
            format=format_string,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
            enqueue=True,  # 异步写入
        )

    logger.info(f"日志系统初始化完成: level={log_level}, file={log_file or 'console only'}")


def get_logger(name: str):
    """获取logger实例

    Args:
        name: logger名称(通常使用模块名 __name__)

    Returns:
        loguru logger实例(绑定name)
    """
    return logger.bind(name=name)
