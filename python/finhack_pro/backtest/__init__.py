"""
FinHack Pro 回测模块

提供回测运行器，支持两种回测模式:
- 向量化模式（高性能，轻量级时间隔离）
- 异步事件驱动模式（严格时间隔离，完整延迟模拟）
- NumPy向量化加速引擎
- 多标的并行回测
- Numba JIT加速（可选）
- Rust核心桥接（三级降级：PyO3子进程 → HTTP → Python回退）
- PyO3子进程隔离包装器

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
from finhack_pro.backtest.accelerated import (
    NumPyVectorizedEngine,
    NumPyEngineConfig,
    run_multi_symbol_backtest,
    run_multi_symbol_async,
    MultiSymbolResult,
    numba_jit_available,
    RustCoreBridge,
    get_rust_bridge,
)
from finhack_pro.backtest.pyo3_isolated import (
    PyO3Isolated,
    get_pyo3_isolated,
)

__all__ = [
    # 原有接口
    "BacktestRunner",
    "BacktestResult",
    # 引擎工厂
    "create_engine",
    "run_backtest",
    "compare_modes",
    # 时间切片
    "BacktestMode",
    "DataBarrier",
    "TimeSliceContext",
    "PortfolioSnapshot",
    "EngineSnapshot",
    "LatencyConfig",
    "LatencySimulator",
    "LookAheadError",
    "EngineResult",
    # 加速模块
    "NumPyVectorizedEngine",
    "NumPyEngineConfig",
    "run_multi_symbol_backtest",
    "run_multi_symbol_async",
    "MultiSymbolResult",
    "numba_jit_available",
    "RustCoreBridge",
    "get_rust_bridge",
    # PyO3 子进程隔离
    "PyO3Isolated",
    "get_pyo3_isolated",
]
