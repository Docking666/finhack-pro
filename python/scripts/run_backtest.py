#!/usr/bin/env python3
"""
回测运行脚本

通过命令行运行策略回测。

Usage:
    python scripts/run_backtest.py --strategy dual_thrust --symbol 600519.SH \
        --start 2023-01-01 --end 2024-01-01 --capital 1000000

    python scripts/run_backtest.py --strategy mean_reversion --symbol 000001.SZ \
        --start 2022-01-01 --end 2024-01-01 --mode python
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
from loguru import logger

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.config import FinhackProConfig, get_config, reset_config
from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.data.technical import TechnicalIndicator
from finhack_pro.utils.logger import setup_logger


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="FinHack Pro 回测运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--strategy", "-s",
        type=str,
        default="dual_thrust",
        choices=["dual_thrust", "momentum", "mean_reversion", "ml_strategy"],
        help="策略名称 (默认: dual_thrust)",
    )
    parser.add_argument(
        "--symbol", "-t",
        type=str,
        default="600519.SH",
        help="标的代码 (默认: 600519.SH)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="多个标的(逗号分隔，用于动量策略)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2023-01-01",
        help="开始日期 (默认: 2023-01-01)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="结束日期 (默认: 今天)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=1_000_000,
        help="初始资金 (默认: 1000000)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="python",
        choices=["python", "rust"],
        help="回测模式 (默认: python)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="",
        help="配置文件路径",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="报告输出路径",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )
    parser.add_argument(
        "--params",
        type=str,
        default="",
        help="策略参数(JSON格式，如 '{\"k1\": 0.6, \"k2\": 0.4}')",
    )

    return parser.parse_args()


def main() -> None:
    """主函数"""
    args = parse_args()

    # 初始化日志
    setup_logger(log_level=args.log_level)

    # 加载配置
    reset_config()
    config = get_config(args.config or None)

    logger.info("=" * 60)
    logger.info("FinHack Pro 回测运行器")
    logger.info("=" * 60)
    logger.info(f"策略: {args.strategy}")
    logger.info(f"标的: {args.symbol}")
    logger.info(f"区间: {args.start} ~ {args.end or '今天'}")
    logger.info(f"资金: {args.capital:,.0f}")
    logger.info(f"模式: {args.mode}")

    # 解析策略参数
    strategy_params = {}
    if args.params:
        import json
        try:
            strategy_params = json.loads(args.params)
        except json.JSONDecodeError as e:
            logger.error(f"策略参数JSON解析失败: {e}")
            return

    # 加载策略
    runner = BacktestRunner()
    try:
        strategy = runner.load_strategy(args.strategy)
        if strategy_params:
            strategy.set_parameters(strategy_params)
    except ValueError as e:
        logger.error(f"策略加载失败: {e}")
        return

    # 获取数据
    logger.info("正在获取数据...")
    fetcher = DataFetcher(
        source=config.data.source,
        tushare_token=config.data.tushare_token,
        cache_dir=config.data.cache_dir,
    )

    df = fetcher.get_daily(args.symbol, args.start, args.end)
    if df.empty:
        logger.error(f"获取数据失败: {args.symbol}")
        logger.info("使用模拟数据进行回测演示...")
        # 生成模拟数据
        import numpy as np
        np.random.seed(42)
        n = 200
        dates = pd.date_range(args.start, periods=n, freq="B")
        prices = 100 + np.cumsum(np.random.randn(n) * 2)
        df = pd.DataFrame({
            "date": dates,
            "open": prices * (1 + np.random.randn(n) * 0.005),
            "high": prices * (1 + np.abs(np.random.randn(n) * 0.01)),
            "low": prices * (1 - np.abs(np.random.randn(n) * 0.01)),
            "close": prices,
            "volume": np.random.randint(100000, 1000000, n).astype(float),
        })

    # 添加技术指标
    ti = TechnicalIndicator()
    df = ti.add_all_indicators(df)

    # 运行回测
    result = runner.run(
        strategy=strategy,
        symbol=args.symbol,
        data=df,
        initial_capital=args.capital,
        commission_rate=config.backtest.commission_rate,
        stamp_tax_rate=config.backtest.stamp_tax_rate,
        slippage=config.backtest.slippage,
        params=strategy_params,
    )

    # 输出报告
    runner.print_report(result)

    # 保存报告
    output_path = args.output or f"backtest_report_{args.strategy}_{args.symbol}.json"
    runner.save_report(result, output_path)


if __name__ == "__main__":
    main()
