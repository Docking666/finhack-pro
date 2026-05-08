"""
FinHack Pro 回测模块

提供回测运行器，支持调用Rust引擎或纯Python回测。
"""

from finhack_pro.backtest.runner import BacktestRunner

__all__ = ["BacktestRunner"]
