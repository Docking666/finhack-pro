#!/usr/bin/env python3
"""
Agent系统运行脚本

启动所有Agent，运行完整的市场分析->策略生成->风控->执行流程。

Usage:
    # 使用模拟数据运行一次分析
    python scripts/run_agents.py --symbol 600519.SH --once

    # 启动定时分析循环
    python scripts/run_agents.py --symbol 600519.SH --interval 300

    # 使用配置文件
    python scripts/run_agents.py --config config.yaml --symbol 600519.SH
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from finhack_pro.agents.coordinator import AgentCoordinator
from finhack_pro.config import FinhackProConfig, get_config, reset_config
from finhack_pro.utils.logger import setup_logger

console = Console()


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="FinHack Pro Agent系统运行器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--symbol", "-s",
        type=str,
        default="600519.SH",
        help="标的代码 (默认: 600519.SH)",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="",
        help="配置文件路径",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只运行一次分析(不启动定时循环)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="定时分析间隔(秒，默认: 300)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="模拟模式(不实际下单，默认开启)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别 (默认: INFO)",
    )

    return parser.parse_args()


def generate_mock_market_data(symbol: str) -> dict:
    """生成模拟市场数据(用于演示)"""
    np.random.seed(42)
    n = 30
    dates = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="B")
    base_price = 1800.0 if "600519" in symbol else 50.0
    prices = base_price + np.cumsum(np.random.randn(n) * base_price * 0.02)

    recent_bars = []
    for i in range(n):
        bar = {
            "date": str(dates[i].date()),
            "open": round(prices[i] * (1 + np.random.randn() * 0.005), 2),
            "high": round(prices[i] * (1 + abs(np.random.randn()) * 0.01), 2),
            "low": round(prices[i] * (1 - abs(np.random.randn()) * 0.01), 2),
            "close": round(prices[i], 2),
            "volume": int(np.random.randint(50000, 500000)),
        }
        recent_bars.append(bar)

    current = {
        "close": round(prices[-1], 2),
        "change_pct": round((prices[-1] - prices[-2]) / prices[-2] * 100, 2),
    }

    return {
        "recent_bars": recent_bars[-10:],
        "current": current,
    }


def generate_mock_indicators() -> dict:
    """生成模拟技术指标"""
    np.random.seed(42)
    return {
        "RSI(14)": round(np.random.uniform(20, 80), 2),
        "MACD": round(np.random.uniform(-2, 2), 4),
        "MACD_Signal": round(np.random.uniform(-1, 1), 4),
        "MACD_Hist": round(np.random.uniform(-1, 1), 4),
        "MA5": round(np.random.uniform(1700, 1900), 2),
        "MA10": round(np.random.uniform(1700, 1900), 2),
        "MA20": round(np.random.uniform(1700, 1900), 2),
        "MA60": round(np.random.uniform(1700, 1900), 2),
        "BOLL_Upper": round(np.random.uniform(1800, 2000), 2),
        "BOLL_Middle": round(np.random.uniform(1700, 1900), 2),
        "BOLL_Lower": round(np.random.uniform(1600, 1800), 2),
        "ATR(14)": round(np.random.uniform(10, 50), 2),
        "OBV": round(np.random.uniform(1e6, 1e8), 0),
    }


def print_analysis_result(result: dict) -> None:
    """打印分析结果"""
    # 市场分析
    analysis = result.get("analysis", {})
    if analysis:
        table = Table(title="市场分析报告", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")

        table.add_row("标的", analysis.get("symbol", ""))
        table.add_row("市场状态", analysis.get("market_state", ""))
        table.add_row("趋势方向", analysis.get("trend_direction", ""))
        table.add_row("置信度", f"{analysis.get('confidence', 0):.2%}")
        table.add_row("风险等级", analysis.get("risk_level", ""))

        key_factors = analysis.get("key_factors", [])
        if key_factors:
            table.add_row("关键因素", "\n".join(f"- {f}" for f in key_factors))

        if analysis.get("suggestion"):
            table.add_row("操作建议", analysis.get("suggestion", ""))

        console.print(table)

    # 策略信号
    signal = result.get("signal", {})
    if signal:
        table = Table(title="策略信号", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")

        table.add_row("标的", signal.get("symbol", ""))
        table.add_row("方向", signal.get("direction", ""))
        table.add_row("置信度", f"{signal.get('confidence', 0):.2%}")
        table.add_row("建议仓位", f"{signal.get('position_size_pct', 0):.1%}")
        table.add_row("止损价", f"{signal.get('stop_loss', 0):.2f}")
        table.add_row("止盈价", f"{signal.get('take_profit', 0):.2f}")
        table.add_row("策略类型", signal.get("strategy_type", ""))
        table.add_row("持有周期", signal.get("time_horizon", ""))

        if signal.get("reasoning"):
            table.add_row("决策理由", signal.get("reasoning", ""))

        console.print(table)

    # 风控决策
    risk = result.get("risk_decision", {})
    if risk:
        table = Table(title="风控决策", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")

        approved = risk.get("approved", False)
        table.add_row("审批结果", "[green]通过[/green]" if approved else "[red]拒绝[/red]")
        table.add_row("调整后仓位", f"{risk.get('adjusted_position_size', 0):.1%}" if risk.get("adjusted_position_size") else "未调整")

        alerts = risk.get("risk_alerts", [])
        if alerts:
            table.add_row("风险预警", "\n".join(f"- {a}" for a in alerts))

        if risk.get("reasoning"):
            table.add_row("决策理由", risk.get("reasoning", ""))

        console.print(table)

    # 执行报告
    execution = result.get("execution", {})
    if execution:
        table = Table(title="执行报告", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")

        table.add_row("订单ID", execution.get("order_id", ""))
        table.add_row("标的", execution.get("symbol", ""))
        table.add_row("方向", execution.get("side", ""))
        table.add_row("价格", f"{execution.get('price', 0):.2f}")
        table.add_row("数量", f"{execution.get('volume', 0)}股")
        table.add_row("算法", execution.get("algorithm", ""))
        table.add_row("状态", execution.get("status", ""))
        table.add_row("预估成本", f"{execution.get('estimated_cost', 0):.2f}")

        console.print(table)


async def run_once(coordinator: AgentCoordinator, symbol: str) -> None:
    """运行一次完整分析"""
    console.print(Panel(
        f"开始分析 {symbol}",
        style="bold blue",
    ))

    # 生成模拟数据
    market_data = generate_mock_market_data(symbol)
    indicators = generate_mock_indicators()
    current_price = market_data["current"]["close"]

    # 运行分析流水线
    result = await coordinator.run_analysis_pipeline(
        symbol=symbol,
        market_data=market_data,
        indicators=indicators,
        current_price=current_price,
    )

    # 打印结果
    print_analysis_result(result)

    if result.get("error"):
        console.print(f"[red]分析出错: {result['error']}[/red]")

    console.print(Panel(
        "分析完成",
        style="bold green",
    ))


async def main_async() -> None:
    """异步主函数"""
    args = parse_args()

    # 初始化日志
    setup_logger(log_level=args.log_level)

    # 加载配置
    reset_config()
    config = get_config(args.config or None)

    console.print(Panel(
        "[bold]FinHack Pro AI Agent系统[/bold]\n"
        f"标的: {args.symbol}\n"
        f"模式: {'单次运行' if args.once else f'定时循环({args.interval}秒)'}\n"
        f"模拟模式: {args.dry_run}",
        title="Agent系统",
        border_style="blue",
    ))

    # 构建配置
    agent_config = config.model_dump()

    # 创建协调器
    coordinator = AgentCoordinator(agent_config)

    try:
        await coordinator.start()

        if args.once:
            await run_once(coordinator, args.symbol)
        else:
            # 定时循环
            while True:
                await run_once(coordinator, args.symbol)
                console.print(f"\n等待 {args.interval} 秒后进行下一次分析...\n")
                await asyncio.sleep(args.interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]用户中断，正在停止...[/yellow]")
    finally:
        await coordinator.stop()


def main() -> None:
    """主函数入口"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
