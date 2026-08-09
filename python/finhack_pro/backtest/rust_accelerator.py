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

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 模块级缓存：finhack_pyo3 是否可用（避免每次调用都尝试 import）
_pyo3_module = None
_pyo3_checked = False

# 指标结果 LRU 缓存：数据指纹 + 指标列表 -> {indicator: np.ndarray}
# 参数扫描/多策略复用同一份数据时，避免重复计算指标
_INDICATOR_CACHE: "OrderedDict[Tuple[str, ...], Dict[str, np.ndarray]]" = OrderedDict()
_INDICATOR_CACHE_MAX = 64
_INDICATOR_CACHE_LOCK = threading.Lock()


def _clear_indicator_cache() -> None:
    """清空指标缓存（测试用）"""
    with _INDICATOR_CACHE_LOCK:
        _INDICATOR_CACHE.clear()


def _data_fingerprint(
    closes: np.ndarray,
    highs: Optional[np.ndarray],
    lows: Optional[np.ndarray],
) -> str:
    """计算数据指纹（用于缓存 key）

    用首/末值 + 长度 + 前 16 字节内容哈希，避免对大数组做全量哈希。
    数组内容差异几乎必然反映在首部或长度上（行情数据升序时间序列）。
    """
    c = np.asarray(closes, dtype=np.float64)
    h = np.asarray(highs, dtype=np.float64) if highs is not None else None
    lo = np.asarray(lows, dtype=np.float64) if lows is not None else None
    hasher = hashlib.md5()
    hasher.update(c.tobytes()[:256])
    hasher.update(str(c.size).encode())
    hasher.update(str(float(c[0]) if c.size else 0).encode())
    hasher.update(str(float(c[-1]) if c.size else 0).encode())
    if h is not None:
        hasher.update(h.tobytes()[:64])
    if lo is not None:
        hasher.update(lo.tobytes()[:64])
    return hasher.hexdigest()


def calculate_indicators_cached(
    closes: np.ndarray,
    highs: Optional[np.ndarray] = None,
    lows: Optional[np.ndarray] = None,
    indicators: Optional[List[str]] = None,
    use_cache: bool = True,
) -> Dict[str, np.ndarray]:
    """技术指标计算（Rust 优先 + LRU 缓存 + NumPy 回退）

    Args:
        closes: 收盘价数组
        highs: 最高价数组（ATR 需要）
        lows: 最低价数组（ATR 需要）
        indicators: 指标名列表，如 ["rsi", "macd", "bollinger", "atr"]
        use_cache: 是否使用 LRU 缓存（参数扫描场景建议开启）

    Returns:
        {indicator: np.ndarray}，缺失值用 NaN 填充
    """
    closes_arr = np.asarray(closes, dtype=np.float64)
    highs_arr = np.asarray(highs, dtype=np.float64) if highs is not None else None
    lows_arr = np.asarray(lows, dtype=np.float64) if lows is not None else None
    inds = sorted(set(indicators or ["rsi"]))
    if not inds:
        return {}

    if use_cache:
        key: Tuple[str, ...] = (_data_fingerprint(closes_arr, highs_arr, lows_arr), *inds)
        with _INDICATOR_CACHE_LOCK:
            if key in _INDICATOR_CACHE:
                _INDICATOR_CACHE.move_to_end(key)
                return {k: v.copy() for k, v in _INDICATOR_CACHE[key].items()}
    else:
        key = None

    result = _calculate_indicators_impl(closes_arr, highs_arr, lows_arr, inds)

    if use_cache and key is not None:
        with _INDICATOR_CACHE_LOCK:
            _INDICATOR_CACHE[key] = {k: v.copy() for k, v in result.items()}
            while len(_INDICATOR_CACHE) > _INDICATOR_CACHE_MAX:
                _INDICATOR_CACHE.popitem(last=False)

    return result


def _calculate_indicators_impl(
    closes: np.ndarray,
    highs: Optional[np.ndarray],
    lows: Optional[np.ndarray],
    indicators: List[str],
) -> Dict[str, np.ndarray]:
    """实际指标计算：Rust 优先，Python 回退"""
    module = _get_pyo3_module()
    if module is not None:
        try:
            rust_highs = highs if highs is not None else None
            rust_lows = lows if lows is not None else None
            rust_result = module.calculate_indicators(
                closes, rust_highs, rust_lows, indicators
            )
            # 转成 numpy 数组（NaN 填充 None）
            out: Dict[str, np.ndarray] = {}
            for name in indicators:
                vals = rust_result.get(name) if isinstance(rust_result, dict) else None
                if vals is None:
                    out[name] = np.full(closes.size, np.nan)
                else:
                    arr = np.asarray(vals, dtype=np.float64)
                    out[name] = np.where(np.isnan(arr), np.nan, arr)
            return out
        except Exception as e:
            logger.warning(f"[RustAccelerator] Rust indicators 失败，回退 NumPy: {e}")

    # NumPy 回退
    out = {}
    n = closes.size
    for name in indicators:
        out[name] = np.full(n, np.nan)
    try:
        if "rsi" in indicators:
            out["rsi"] = _rsi_numpy(closes, 14)
        if "macd" in indicators:
            ema12 = _ema_numpy(closes, 12)
            ema26 = _ema_numpy(closes, 26)
            out["macd"] = ema12 - ema26
        if "bollinger" in indicators:
            mid = _sma_numpy(closes, 20)
            std = _rolling_std_numpy(closes, 20)
            out["bollinger_upper"] = mid + 2 * std
            out["bollinger_lower"] = mid - 2 * std
        if "atr" in indicators:
            out["atr"] = _atr_numpy(closes, highs, lows, 14)
    except Exception as e:
        logger.warning(f"[RustAccelerator] NumPy indicators 计算失败: {e}")
    return out


def _ema_numpy(values: np.ndarray, period: int) -> np.ndarray:
    """EMA 计算（NumPy）"""
    s = pd.Series(values)
    return s.ewm(span=period, adjust=False).mean().to_numpy()


def _sma_numpy(values: np.ndarray, period: int) -> np.ndarray:
    """SMA 计算（NumPy）"""
    s = pd.Series(values)
    return s.rolling(period).mean().to_numpy()


def _rolling_std_numpy(values: np.ndarray, period: int) -> np.ndarray:
    s = pd.Series(values)
    return s.rolling(period).std().to_numpy()


def _rsi_numpy(values: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI 计算（Wilder 平滑，与 Rust 一致）"""
    s = pd.Series(values)
    diff = s.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.to_numpy()


def _atr_numpy(closes: np.ndarray, highs: Optional[np.ndarray], lows: Optional[np.ndarray], period: int = 14) -> np.ndarray:
    if highs is None or lows is None:
        return np.full(closes.size, np.nan)
    h_ser, l_ser, c_ser = pd.Series(highs), pd.Series(lows), pd.Series(closes)
    tr = pd.concat([h_ser - l_ser, (h_ser - c_ser.shift(1)).abs(), (l_ser - c_ser.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean().to_numpy()


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


def run_backtest_vectorized(
    closes: np.ndarray,
    fast_period: int = 5,
    slow_period: int = 20,
    initial_capital: float = 1_000_000.0,
    commission_rate: float = 0.0003,
    slippage: float = 0.001,
    use_cache: bool = True,
    pre_closes: Optional[np.ndarray] = None,
    limit_pct: float = 0.10,
    enable_limit_up_down: bool = False,
) -> Dict[str, Any]:
    """向量化回测（Rust 批量回测优先，NumPy 回退）

    双均线策略的完整回测循环：指标 → 信号 → 撮合 → 绩效统计。
    Rust 侧（batch_backtest）在 rayon 中并行完成，Python 侧仅在
    Rust 不可用时回退到逐 bar 循环。
    支持 A 股涨跌停约束（enable_limit_up_down=True 时启用）。

    Returns:
        dict: {total_return, max_drawdown, sharpe_ratio, total_trades,
               winning_trades, losing_trades, rejected_trades?}
    """
    closes_arr = np.asarray(closes, dtype=np.float64)
    if closes_arr.size < slow_period + 1:
        return {
            "total_return": 0.0, "max_drawdown": 0.0, "sharpe_ratio": 0.0,
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        }

    module = _get_pyo3_module()
    if module is not None:
        try:
            if enable_limit_up_down:
                pre = np.asarray(pre_closes, dtype=np.float64) if pre_closes is not None \
                    else np.zeros(closes_arr.size, dtype=np.float64)
                if pre.size != closes_arr.size:
                    pre = np.zeros(closes_arr.size, dtype=np.float64)
                return dict(module.backtest_ma_constrained(
                    closes_arr, pre, fast_period, slow_period,
                    float(initial_capital), float(commission_rate), float(slippage),
                    float(limit_pct), True,
                ))
            config = [{"fast_period": fast_period, "slow_period": slow_period}]
            result = module.batch_backtest(closes_arr, config, float(initial_capital))
            items = result.get("results", []) if isinstance(result, dict) else []
            if items:
                return dict(items[0])
        except Exception as e:
            logger.warning(f"[RustAccelerator] Rust backtest 失败，回退 NumPy: {e}")

    # NumPy 回退：双均线完整回测
    return _backtest_numpy(
        closes_arr, fast_period, slow_period, initial_capital,
        commission_rate, slippage,
    )


def _backtest_numpy(
    closes: np.ndarray,
    fast_period: int,
    slow_period: int,
    initial_capital: float,
    commission_rate: float,
    slippage: float,
) -> Dict[str, Any]:
    """NumPy 双均线回测（与 Rust 结果一致）"""
    n = closes.size
    cash = initial_capital
    position = 0
    position_cost = 0.0
    peak = initial_capital
    max_dd = 0.0
    total_trades = 0
    winning = 0
    losing = 0
    equity_values: List[float] = [initial_capital]

    loop_start = max(slow_period, fast_period)
    if loop_start >= n:
        return {
            "total_return": 0.0, "max_drawdown": 0.0, "sharpe_ratio": 0.0,
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        }

    fast_ma = np.convolve(closes, np.ones(fast_period) / fast_period, mode="valid")
    slow_ma = np.convolve(closes, np.ones(slow_period) / slow_period, mode="valid")
    # align: convolve 'valid' 输出从 index fast_period-1 开始
    offset_f = fast_period - 1
    offset_s = slow_period - 1

    for i in range(loop_start, n):
        fi = i - offset_f
        si = i - offset_s
        if fi < 0 or si < 0:
            continue
        fast_val = fast_ma[fi]
        slow_val = slow_ma[si]
        prev_fast = fast_ma[fi - 1] if fi - 1 >= 0 else fast_val
        prev_slow = slow_ma[si - 1] if si - 1 >= 0 else slow_val

        if fast_val > slow_val and prev_fast <= prev_slow and position == 0:
            price = closes[i] * (1 + slippage)
            available = cash * 0.9
            vol = int(available / price / 100) * 100
            if vol > 0:
                cost = vol * price
                comm = cost * commission_rate
                cash -= cost + comm
                position = vol
                position_cost = price
                total_trades += 1
        elif fast_val < slow_val and prev_fast >= prev_slow and position > 0:
            price = closes[i] * (1 - slippage)
            revenue = position * price
            comm = revenue * commission_rate
            tax = revenue * 0.001
            pnl = revenue - position * position_cost - comm - tax
            cash += revenue - comm - tax
            if pnl > 0:
                winning += 1
            else:
                losing += 1
            position = 0
            position_cost = 0.0

        value = cash + position * closes[i]
        equity_values.append(value)
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd

    final_value = cash + position * closes[-1]
    total_return = (final_value - initial_capital) / initial_capital if initial_capital > 0 else 0.0

    # 夏普（Rust 优先）
    returns_arr = np.diff(np.asarray(equity_values, dtype=np.float64))
    sharpe = sharpe_ratio(returns_arr) if returns_arr.size > 1 else 0.0

    return {
        "total_return": total_return,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "total_trades": total_trades,
        "winning_trades": winning,
        "losing_trades": losing,
    }
