"""
Rust 加速统一入口

封装 finhack_pyo3 的常用计算（最大回撤、夏普比率），带 Python fallback。
Rust 模块不可用时自动降级为 NumPy 实现，调用方无需感知。

设计：
- 直接调用 finhack_pyo3 函数（同进程，比子进程隔离更快）
- 若 import 失败或调用异常，自动回退 NumPy
- 暴露与 finhack_pyo3 一致的结果，保证数值一致
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 模块级缓存：finhack_pyo3 是否可用（避免每次调用都尝试 import）
_pyo3_module = None
_pyo3_checked = False


def _get_pyo3_module():
    """获取 finhack_pyo3 模块（缓存检测结果）"""
    global _pyo3_module, _pyo3_checked
    if not _pyo3_checked:
        _pyo3_checked = True
        try:
            import finhack_pyo3  # type: ignore
            _pyo3_module = finhack_pyo3
            logger.debug("[RustAccelerator] finhack_pyo3 可用，启用 Rust 加速")
        except ImportError:
            _pyo3_module = None
            logger.debug("[RustAccelerator] finhack_pyo3 不可用，使用 NumPy 回退")
    return _pyo3_module


def is_available() -> bool:
    """Rust 加速是否可用"""
    return _get_pyo3_module() is not None


def max_drawdown(equity: np.ndarray) -> float:
    """计算最大回撤（Rust 优先，NumPy 回退）

    Args:
        equity: 权益曲线数组

    Returns:
        最大回撤比例 (0~1)
    """
    arr = np.asarray(equity, dtype=np.float64)
    if arr.size == 0:
        return 0.0

    module = _get_pyo3_module()
    if module is not None:
        try:
            return float(module.calculate_max_drawdown(arr))
        except Exception as e:
            logger.warning(f"[RustAccelerator] Rust max_drawdown 失败，回退 NumPy: {e}")

    # NumPy 回退
    peak = np.maximum.accumulate(arr)
    if peak[0] <= 0:
        return 0.0
    return float(((peak - arr) / np.where(peak > 0, peak, 1)).max())


def sharpe_ratio(
    returns: np.ndarray,
    risk_free_rate: Optional[float] = None,
    periods_per_year: float = 252.0,
) -> float:
    """计算夏普比率（Rust 优先，NumPy 回退）

    Args:
        returns: 收益率序列
        risk_free_rate: 无风险利率（每期）
        periods_per_year: 年化期数（日线 252）

    Returns:
        年化夏普比率
    """
    arr = np.asarray(returns, dtype=np.float64)
    if arr.size < 2:
        return 0.0

    module = _get_pyo3_module()
    if module is not None:
        try:
            return float(module.calculate_sharpe_ratio(arr, risk_free_rate))
        except Exception as e:
            logger.warning(f"[RustAccelerator] Rust sharpe 失败，回退 NumPy: {e}")

    # NumPy 回退
    rf = risk_free_rate or 0.0
    excess = arr - rf
    mean = float(excess.mean())
    std = float(excess.std(ddof=1))
    if std <= 0:
        return 0.0
    return mean / std * np.sqrt(periods_per_year)
