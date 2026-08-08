#!/usr/bin/env python3
"""Rust 加速层 benchmark

对比 finhack_pyo3 与纯 Python/NumPy 实现的性能：
- 指标计算（RSI/MACD/BB/ATR）
- 最大回撤 / 夏普比率
- 批量回测（参数扫描）

用法：
    python scripts/benchmark_rust.py [--bars 10000] [--strategies 50]
"""

import argparse
import time

import numpy as np
import pandas as pd


def make_data(n: int) -> pd.DataFrame:
    """生成 OHLCV 测试数据"""
    np.random.seed(42)
    dates = pd.date_range("2020-01-01", periods=n, freq="5min")
    # 有趋势的随机游走，指标更有意义
    drift = np.random.normal(0.0001, 0.002, n)
    closes = 100 * np.exp(np.cumsum(drift))
    return pd.DataFrame({
        "date": dates,
        "open": closes * 0.998,
        "high": closes * 1.015,
        "low": closes * 0.985,
        "close": closes,
        "volume": np.random.uniform(1e5, 1e7, n),
    })


def bench_indicators(df: pd.DataFrame):
    """指标计算：Rust vs 纯 Python"""
    closes = df["close"].to_numpy(dtype=np.float64)
    highs = df["high"].to_numpy(dtype=np.float64)
    lows = df["low"].to_numpy(dtype=np.float64)
    indicators = ["rsi", "macd", "bollinger", "atr"]

    # --- Rust (finhack_pyo3) ---
    try:
        import finhack_pyo3
        t0 = time.perf_counter()
        result = finhack_pyo3.calculate_indicators(closes, highs, lows, indicators)
        rust_time = time.perf_counter() - t0
        rust_valid = sum(1 for v in result["rsi"] if v is not None)
    except ImportError:
        rust_time, rust_valid = None, 0
        print("  [finhack_pyo3 未编译，跳过 Rust 指标对比]")

    # --- Python (pandas 滚动窗口，模拟 ta 库开销) ---
    s = pd.Series(closes)

    def _rsi_window(x: np.ndarray) -> float:
        diffs = np.diff(x)
        gains = np.clip(diffs, 0, None).mean()
        losses = np.abs(np.clip(diffs, None, 0)).mean()
        if losses == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + gains / losses)

    t0 = time.perf_counter()
    rsi = s.rolling(14).apply(_rsi_window, raw=True)
    macd = s.ewm(span=12).mean() - s.ewm(span=26).mean()
    bb_mid = s.rolling(20).mean()
    bb_std = s.rolling(20).std()
    py_time = time.perf_counter() - t0

    if rust_time is not None:
        speedup = py_time / rust_time if rust_time > 0 else float("inf")
        print(f"  指标计算: Rust={rust_time*1000:.1f}ms  Python={py_time*1000:.1f}ms  加速比={speedup:.1f}x")
    else:
        print(f"  指标计算: Python={py_time*1000:.1f}ms")
    print(f"  RSI 有效值: Rust={rust_valid}/{len(closes)}")


def bench_stats(equity: np.ndarray, returns: np.ndarray):
    """最大回撤/夏普：Rust vs NumPy"""
    # --- Rust ---
    try:
        import finhack_pyo3
        t0 = time.perf_counter()
        dd_rust = finhack_pyo3.calculate_max_drawdown(equity)
        sr_rust = finhack_pyo3.calculate_sharpe_ratio(returns, None)
        rust_time = time.perf_counter() - t0
    except ImportError:
        rust_time = None

    # --- NumPy ---
    t0 = time.perf_counter()
    peak = np.maximum.accumulate(equity)
    dd_py = float(((peak - equity) / peak).max())
    std = returns.std(ddof=1)
    sr_py = float(returns.mean() / std * np.sqrt(252)) if std > 0 else 0.0
    py_time = time.perf_counter() - t0

    if rust_time is not None:
        speedup = py_time / rust_time if rust_time > 0 else float("inf")
        print(f"  回撤/夏普: Rust={rust_time*1000:.2f}ms  Python={py_time*1000:.2f}ms  加速比={speedup:.1f}x")
        assert abs(dd_rust - dd_py) < 1e-9, "最大回撤结果不一致!"
        assert abs(sr_rust - sr_py) < 1e-6, "夏普比率结果不一致!"
        print(f"  数值一致性: ✓ (dd={dd_rust:.6f}, sharpe={sr_rust:.4f})")
    else:
        print(f"  回撤/夏普: Python={py_time*1000:.2f}ms")


def bench_batch_backtest(closes: np.ndarray, n_strategies: int):
    """批量回测：Rust (rayon) vs Python 串行"""
    configs = [{"fast_period": f, "slow_period": 20} for f in range(3, 3 + n_strategies)]

    # --- Rust ---
    try:
        import finhack_pyo3
        t0 = time.perf_counter()
        result = finhack_pyo3.batch_backtest(closes, configs, 1_000_000.0)
        rust_time = time.perf_counter() - t0
        n_results = len(result["results"])
    except ImportError:
        rust_time, n_results = None, 0
        print("  [finhack_pyo3 未编译，跳过批量回测对比]")

    # --- Python 串行 ---
    def py_backtest(closes, fast, slow, capital=1_000_000.0):
        n = len(closes)
        cash, pos, pos_cost = capital, 0, 0.0
        for i in range(slow, n):
            fa = closes[i + 1 - fast:i + 1].mean()
            sa = closes[i + 1 - slow:i + 1].mean()
            pfa = closes[i - fast:i].mean()
            psa = closes[i - slow:i].mean()
            if fa > sa and pfa <= psa and pos == 0:
                vol = int(cash * 0.9 / closes[i] / 100) * 100
                if vol > 0:
                    cash -= vol * closes[i] * 1.001
                    pos, pos_cost = vol, closes[i] * 1.001
            elif fa < sa and pfa >= psa and pos > 0:
                cash += pos * closes[i] * 0.999
                pos, pos_cost = 0, 0.0
        return (cash + pos * closes[-1] - capital) / capital

    t0 = time.perf_counter()
    py_results = [py_backtest(closes, c["fast_period"], c["slow_period"]) for c in configs]
    py_time = time.perf_counter() - t0

    if rust_time is not None:
        speedup = py_time / rust_time if rust_time > 0 else float("inf")
        print(f"  批量回测({n_strategies} 策略): Rust={rust_time*1000:.1f}ms  Python={py_time*1000:.1f}ms  加速比={speedup:.1f}x")
        # 数值近似一致（滑点/费用建模略有差异，仅做趋势校验）
        corr = np.corrcoef([r["total_return"] for r in result["results"]], py_results)[0, 1]
        print(f"  收益相关性: {corr:.4f} (接近 1 表示逻辑一致)")
    else:
        print(f"  批量回测({n_strategies} 策略): Python={py_time*1000:.1f}ms")


def main():
    parser = argparse.ArgumentParser(description="Rust 加速层 benchmark")
    parser.add_argument("--bars", type=int, default=10000, help="K线数量")
    parser.add_argument("--strategies", type=int, default=50, help="批量回测策略数")
    args = parser.parse_args()

    print("=" * 60)
    print(f"FinHack Pro Rust 加速 benchmark (bars={args.bars})")
    print("=" * 60)

    df = make_data(args.bars)
    closes = df["close"].to_numpy(dtype=np.float64)
    equity = 1_000_000 * np.cumprod(1 + np.random.normal(0.0001, 0.01, args.bars))
    returns = np.diff(equity) / equity[:-1]

    print("\n[1/3] 指标计算")
    bench_indicators(df)

    print("\n[2/3] 绩效统计")
    bench_stats(equity, returns)

    print("\n[3/3] 批量回测")
    bench_batch_backtest(closes, args.strategies)

    print("\n" + "=" * 60)
    print("完成")


if __name__ == "__main__":
    main()
