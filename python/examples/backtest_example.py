#!/usr/bin/env python3
"""
回测示例 - Dual Thrust策略回测贵州茅台

演示如何使用FinHack Pro Python层进行策略回测。
使用模拟数据，不依赖外部数据源。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.data.technical import TechnicalIndicator
from finhack_pro.strategies.dual_thrust import DualThrustStrategy
from finhack_pro.utils.logger import setup_logger


def generate_sample_data(symbol: str = "600519.SH", days: int = 500) -> pd.DataFrame:
    """生成模拟K线数据

    Args:
        symbol: 标的代码
        days: 交易日数量

    Returns:
        OHLCV DataFrame
    """
    np.random.seed(42)
    dates = pd.date_range("2022-01-01", periods=days, freq="B")

    # 模拟价格走势: 基础价格 + 随机游走 + 趋势
    base_price = 1800.0
    trend = 0.3  # 日均涨幅(基点)
    volatility = 1.5  # 日均波动(百分比)

    prices = [base_price]
    for _ in range(days - 1):
        change = np.random.randn() * volatility + trend
        prices.append(max(prices[-1] * (1 + change / 100), 100))

    prices = np.array(prices)

    df = pd.DataFrame({
        "date": dates,
        "open": prices * (1 + np.random.randn(days) * 0.003),
        "high": prices * (1 + np.abs(np.random.randn(days)) * 0.008),
        "low": prices * (1 - np.abs(np.random.randn(days)) * 0.008),
        "close": prices,
        "volume": np.random.randint(200000, 800000, days).astype(float),
        "amount": prices * np.random.randint(200000, 800000, days).astype(float),
    })

    # 确保high >= low
    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"] = df[["open", "close", "low"]].min(axis=1)

    return df


def main() -> None:
    """主函数"""
    # 初始化日志
    setup_logger(log_level="INFO")

    print("=" * 60)
    print("FinHack Pro 回测示例")
    print("策略: Dual Thrust")
    print("标的: 贵州茅台 (600519.SH)")
    print("=" * 60)

    # 1. 生成模拟数据
    print("\n[1/4] 生成模拟数据...")
    df = generate_sample_data("600519.SH", days=500)
    print(f"  数据范围: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
    print(f"  数据量: {len(df)} 个交易日")
    print(f"  价格区间: {df['close'].min():.2f} ~ {df['close'].max():.2f}")

    # 2. 添加技术指标
    print("\n[2/4] 计算技术指标...")
    ti = TechnicalIndicator()
    df = ti.add_all_indicators(df)
    print(f"  已添加指标: RSI, MACD, 布林带, MA, ATR, OBV, KDJ")

    # 3. 初始化策略和回测引擎
    print("\n[3/4] 初始化策略...")
    strategy = DualThrustStrategy()
    strategy.set_parameters({
        "k1": 0.5,
        "k2": 0.5,
        "lookback": 20,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
    })
    print(f"  策略: {strategy.strategy_name}")
    print(f"  参数: k1=0.5, k2=0.5, lookback=20")

    runner = BacktestRunner()

    # 4. 运行回测
    print("\n[4/4] 运行回测...")
    result = runner.run(
        strategy=strategy,
        symbol="600519.SH",
        data=df,
        initial_capital=1_000_000,
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
        slippage=0.001,
    )

    # 输出报告
    print()
    runner.print_report(result)

    # 保存报告
    report_path = "backtest_report_dual_thrust_600519.json"
    runner.save_report(result, report_path)
    print(f"\n报告已保存: {report_path}")

    # 额外信息
    print("\n" + "=" * 60)
    print("回测参数说明:")
    print("  - 初始资金: 1,000,000 元")
    print("  - 佣金: 万三 (最低5元)")
    print("  - 印花税: 千一 (仅卖出)")
    print("  - 滑点: 0.1%")
    print("=" * 60)


if __name__ == "__main__":
    main()
