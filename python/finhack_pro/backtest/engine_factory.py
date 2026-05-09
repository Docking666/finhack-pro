"""
回测引擎工厂 - Engine Factory

提供统一的回测引擎创建和切换接口。

支持冷启动切换:
- 向量化模式（默认）: 高性能，轻量级时间切片保护
- 异步事件驱动模式: 严格时间隔离，完整延迟模拟

Usage:
    from finhack_pro.backtest.engine_factory import create_engine, run_backtest
    
    # 方式一：直接创建引擎
    engine = create_engine("vectorized")
    result = engine.run(strategy, symbol, data)
    
    # 方式二：通过配置创建
    engine = create_engine("async_event", config={
        "latency": {"data_latency_ms": 10, "compute_latency_ms": 5},
        "save_snapshots": True,
    })
    result = await engine.run(strategy, symbol, data)
    
    # 方式三：一键回测（自动处理同步/异步）
    result = run_backtest(strategy, symbol, data, mode="vectorized")
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Union

from finhack_pro.backtest.async_engine import AsyncEngineConfig, AsyncEventEngine
from finhack_pro.backtest.time_slice import (
    BacktestMode,
    EngineResult,
    LatencyConfig,
)
from finhack_pro.backtest.vectorized_engine import VectorizedEngine, VectorizedEngineConfig
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


def create_engine(
    mode: str = "vectorized",
    config: Optional[Dict[str, Any]] = None,
) -> Union[VectorizedEngine, AsyncEventEngine]:
    """创建回测引擎
    
    Args:
        mode: 回测模式
            - "vectorized": 向量化模式（高性能，轻量级时间隔离）
            - "async_event": 异步事件驱动模式（严格时间隔离+延迟模拟）
        config: 引擎配置字典
        
    Returns:
        回测引擎实例
        
    Raises:
        ValueError: 未知的回测模式
    """
    config = config or {}
    
    if mode == BacktestMode.VECTORIZED or mode == "vectorized":
        engine_config = VectorizedEngineConfig(
            commission_rate=config.get("commission_rate", 0.0003),
            stamp_tax_rate=config.get("stamp_tax_rate", 0.001),
            slippage=config.get("slippage", 0.001),
            initial_capital=config.get("initial_capital", 1_000_000.0),
            time_column=config.get("time_column", "date"),
            strict_mode=config.get("strict_mode", True),
            enable_time_slice=config.get("enable_time_slice", True),
            enable_data_barrier=config.get("enable_data_barrier", False),
        )
        logger.info(f"[EngineFactory] 创建向量化引擎: strict={engine_config.strict_mode}")
        return VectorizedEngine(engine_config)
    
    elif mode == BacktestMode.ASYNC_EVENT or mode == "async_event":
        # 解析延迟配置
        latency_dict = config.get("latency", {})
        latency_config = LatencyConfig(
            data_latency_ms=latency_dict.get("data_latency_ms", 0.0),
            compute_latency_ms=latency_dict.get("compute_latency_ms", 1.0),
            order_latency_ms=latency_dict.get("order_latency_ms", 5.0),
            fill_latency_ms=latency_dict.get("fill_latency_ms", 10.0),
        )
        
        engine_config = AsyncEngineConfig(
            commission_rate=config.get("commission_rate", 0.0003),
            stamp_tax_rate=config.get("stamp_tax_rate", 0.001),
            slippage=config.get("slippage", 0.001),
            initial_capital=config.get("initial_capital", 1_000_000.0),
            time_column=config.get("time_column", "date"),
            latency=latency_config,
            batch_size=config.get("batch_size", 1),
            save_snapshots=config.get("save_snapshots", True),
            snapshot_interval=config.get("snapshot_interval", 1),
        )
        logger.info(
            f"[EngineFactory] 创建异步事件引擎: "
            f"延迟={latency_config.total_latency_ms:.0f}ms | "
            f"快照={'开启' if engine_config.save_snapshots else '关闭'}"
        )
        return AsyncEventEngine(engine_config)
    
    else:
        raise ValueError(
            f"未知的回测模式: {mode}，"
            f"可选: 'vectorized', 'async_event'"
        )


def run_backtest(
    strategy,
    symbol: str,
    data,
    mode: str = "vectorized",
    config: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> EngineResult:
    """一键运行回测
    
    自动处理同步/异步差异，提供统一接口。
    
    Args:
        strategy: 策略实例
        symbol: 标的代码
        data: OHLCV DataFrame
        mode: 回测模式 ("vectorized" / "async_event")
        config: 引擎配置
        params: 策略参数
        
    Returns:
        EngineResult 回测结果
    """
    engine = create_engine(mode, config)
    
    if mode == BacktestMode.ASYNC_EVENT or mode == "async_event":
        # 异步引擎需要 await
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            engine.run(strategy, symbol, data, params)
        )
    else:
        # 向量化引擎是同步的
        return engine.run(strategy, symbol, data, params)


def compare_modes(
    strategy,
    symbol: str,
    data,
    config: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对比两种模式的回测结果
    
    用于验证向量化模式的正确性，如果两种模式结果差异过大，
    说明向量化模式可能存在未来函数。
    
    Args:
        strategy: 策略实例
        symbol: 标的代码
        data: OHLCV DataFrame
        config: 引擎配置
        params: 策略参数
        
    Returns:
        对比结果字典
    """
    logger.info("[EngineFactory] 开始双模式对比回测...")
    
    # 向量化模式
    vec_engine = create_engine("vectorized", config)
    vec_result = vec_engine.run(strategy, symbol, data, params)
    
    # 异步事件驱动模式
    async_engine = create_engine("async_event", config)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    async_result = loop.run_until_complete(
        async_engine.run(strategy, symbol, data, params)
    )
    
    # 对比
    return_diff = abs(vec_result.total_return - async_result.total_return)
    sharpe_diff = abs(vec_result.sharpe_ratio - async_result.sharpe_ratio)
    drawdown_diff = abs(vec_result.max_drawdown - async_result.max_drawdown)
    
    # 判断是否存在未来函数
    # 如果向量化模式收益显著高于异步模式（>5%），可能存在未来函数
    potential_look_ahead = (
        vec_result.total_return > async_result.total_return * 1.05
        and return_diff > 0.05
    )
    
    comparison = {
        "vectorized": {
            "total_return": vec_result.total_return,
            "sharpe_ratio": vec_result.sharpe_ratio,
            "max_drawdown": vec_result.max_drawdown,
            "total_trades": vec_result.total_trades,
            "execution_time": vec_result.execution_time_seconds,
            "look_ahead_warnings": vec_result.look_ahead_warnings,
        },
        "async_event": {
            "total_return": async_result.total_return,
            "sharpe_ratio": async_result.sharpe_ratio,
            "max_drawdown": async_result.max_drawdown,
            "total_trades": async_result.total_trades,
            "execution_time": async_result.execution_time_seconds,
            "snapshots": len(async_result.snapshots),
        },
        "diff": {
            "return_diff": return_diff,
            "sharpe_diff": sharpe_diff,
            "drawdown_diff": drawdown_diff,
        },
        "diagnosis": {
            "potential_look_ahead": potential_look_ahead,
            "verdict": (
                "⚠️ 可能存在未来函数" if potential_look_ahead 
                else "✅ 两种模式结果一致"
            ),
        },
    }
    
    logger.info(
        f"[EngineFactory] 对比完成: "
        f"向量化收益={vec_result.total_return:.2%} | "
        f"异步收益={async_result.total_return:.2%} | "
        f"差异={return_diff:.2%} | "
        f"诊断={comparison['diagnosis']['verdict']}"
    )
    
    return comparison
