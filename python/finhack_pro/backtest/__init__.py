"""
FinHack Pro 回测模块

提供回测运行器，支持两种回测模式:
- 向量化模式（高性能，轻量级时间隔离）
- 异步事件驱动模式（严格时间隔离，完整延迟模拟）

通过 engine_factory 统一创建和切换。
"""

from finhack_pro.backtest.runner import BacktestRunner, BacktestResult
from finhack_pro.backtest.engine_factory import (
    create_engine,
    run_backtest,
    compare_modes,
)
from finhack_pro.backtest.time_slice import (
    BacktestMode,
    DataBarrier,
    TimeSliceContext,
    PortfolioSnapshot,
    EngineSnapshot,
    LatencyConfig,
    LatencySimulator,
    LookAheadError,
    EngineResult,
)

__all__ = [
    # 原有接口
    "BacktestRunner",
    "BacktestResult",
    # 新增：引擎工厂
    "create_engine",
    "run_backtest",
    "compare_modes",
    # 新增：时间切片
    "BacktestMode",
    "DataBarrier",
    "TimeSliceContext",
    "PortfolioSnapshot",
    "EngineSnapshot",
    "LatencyConfig",
    "LatencySimulator",
    "LookAheadError",
    "EngineResult",
]
