#!/usr/bin/env python3
"""
Agent系统示例

演示如何使用FinHack Pro AI Agent系统进行市场分析。
使用模拟数据，不依赖LLM API(会fallback到默认值)。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from finhack_pro.agents.coordinator import AgentCoordinator
from finhack_pro.utils.logger import setup_logger

console = Console()


def generate_mock_data(symbol: str = "600519.SH") -> dict:
    """生成模拟市场数据"""
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

    return {
        "recent_bars": recent_bars[-10:],
        "current": {
            "close": round(prices[-1], 2),
            "change_pct": round((prices[-1] - prices[-2]) / prices[-2] * 100, 2),
        },
    }


def generate_mock_indicators() -> dict:
    """生成模拟技术指标"""
    np.random.seed(42)
    return {
        "RSI(14)": round(np.random.uniform(25, 75), 2),
        "MACD": round(np.random.uniform(-2, 2), 4),
        "MACD_Signal": round(np.random.uniform(-1, 1), 4),
        "MACD_Hist": round(np.random.uniform(-1, 1), 4),
        "MA5": round(np.random.uniform(1750, 1850), 2),
        "MA10": round(np.random.uniform(1750, 1850), 2),
        "MA20": round(np.random.uniform(1750, 1850), 2),
        "MA60": round(np.random.uniform(1700, 1900), 2),
        "BOLL_Upper": round(np.random.uniform(1800, 2000), 2),
        "BOLL_Middle": round(np.random.uniform(1750, 1850), 2),
        "BOLL_Lower": round(np.random.uniform(1650, 1800), 2),
        "ATR(14)": round(np.random.uniform(15, 45), 2),
        "OBV": round(np.random.uniform(1e6, 1e8), 0),
    }


def print_step(step: int, title: str) -> None:
    """打印步骤标题"""
    console.print(Panel(
        f"[bold]{title}[/bold]",
        title=f"Step {step}/4",
        border_style="blue",
    ))


def print_result(result: dict) -> None:
    """打印分析结果"""
    # 市场分析
    analysis = result.get("analysis", {})
    if analysis:
        table = Table(title="市场分析报告", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")
        table.add_row("标的", analysis.get("symbol", ""))
        table.add_row("市场状态", str(analysis.get("market_state", "")))
        table.add_row("趋势方向", str(analysis.get("trend_direction", "")))
        table.add_row("置信度", f"{analysis.get('confidence', 0):.2%}")
        table.add_row("风险等级", str(analysis.get("risk_level", "")))
        table.add_row("操作建议", str(analysis.get("suggestion", "")))
        console.print(table)

    # 策略信号
    signal = result.get("signal", {})
    if signal:
        table = Table(title="策略信号", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")
        table.add_row("方向", str(signal.get("direction", "")))
        table.add_row("置信度", f"{signal.get('confidence', 0):.2%}")
        table.add_row("建议仓位", f"{signal.get('position_size_pct', 0):.1%}")
        table.add_row("止损价", f"{signal.get('stop_loss', 0):.2f}")
        table.add_row("止盈价", f"{signal.get('take_profit', 0):.2f}")
        table.add_row("策略类型", str(signal.get("strategy_type", "")))
        table.add_row("决策理由", str(signal.get("reasoning", "")))
        console.print(table)

    # 风控决策
    risk = result.get("risk_decision", {})
    if risk:
        table = Table(title="风控决策", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")
        approved = risk.get("approved", False)
        table.add_row("审批结果", "[green]通过[/green]" if approved else "[red]拒绝[/red]")
        alerts = risk.get("risk_alerts", [])
        if alerts:
            table.add_row("风险预警", "\n".join(f"- {a}" for a in alerts))
        table.add_row("决策理由", str(risk.get("reasoning", "")))
        console.print(table)

    # 执行报告
    execution = result.get("execution", {})
    if execution:
        table = Table(title="执行报告", show_lines=True)
        table.add_column("指标", style="cyan", width=16)
        table.add_column("值", style="green")
        table.add_row("订单ID", execution.get("order_id", ""))
        table.add_row("方向", str(execution.get("side", "")))
        table.add_row("价格", f"{execution.get('price', 0):.2f}")
        table.add_row("数量", f"{execution.get('volume', 0)}股")
        table.add_row("状态", str(execution.get("status", "")))
        console.print(table)


async def main() -> None:
    """主函数"""
    setup_logger(log_level="INFO")

    symbol = "600519.SH"

    console.print(Panel(
        "[bold]FinHack Pro AI Agent系统示例[/bold]\n\n"
        "本示例演示完整的Agent决策流程:\n"
        "1. 市场分析Agent -> 分析市场状态\n"
        "2. 策略生成Agent -> 生成交易策略\n"
        "3. 风险管理Agent -> 风控审批\n"
        "4. 交易执行Agent -> 执行交易\n\n"
        f"分析标的: {symbol} (贵州茅台)\n"
        "注意: 使用模拟数据，LLM不可用时会使用默认值",
        title="Agent系统示例",
        border_style="blue",
    ))

    # 准备模拟数据
    market_data = generate_mock_data(symbol)
    indicators = generate_mock_indicators()
    current_price = market_data["current"]["close"]

    print_step(0, "准备数据")
    console.print(f"  标的: {symbol}")
    console.print(f"  当前价格: {current_price:.2f}")
    console.print(f"  今日涨跌: {market_data['current']['change_pct']:.2f}%")

    # 创建Agent协调器
    config = {
        "llm": {
            "provider": "openai",
            "api_key": "",  # 空key，将使用fallback
            "model": "gpt-4o",
        },
        "agent": {
            "market_analyzer": {},
            "strategy_generator": {},
            "risk_manager": {},
            "trade_executor": {"dry_run": True},
        },
    }

    coordinator = AgentCoordinator(config)

    try:
        await coordinator.start()

        # 运行完整分析流水线
        print_step(1, "市场分析 -> 策略生成 -> 风控审批 -> 交易执行")
        result = await coordinator.run_analysis_pipeline(
            symbol=symbol,
            market_data=market_data,
            indicators=indicators,
            current_price=current_price,
        )

        # 打印结果
        print_result(result)

        if result.get("error"):
            console.print(f"\n[yellow]注意: {result['error']}[/yellow]")
            console.print("[yellow]这是预期行为 - 没有配置LLM API Key时，Agent会使用默认值。[/yellow]")
            console.print("[yellow]要获得完整的AI分析功能，请在配置文件中设置API Key。[/yellow]")

        console.print(Panel(
            "[bold green]Agent系统示例运行完成[/bold green]\n\n"
            "要启用完整的AI分析功能:\n"
            "1. 在配置文件中设置 OPENAI_API_KEY 或 ANTHROPIC_API_KEY\n"
            "2. 或设置环境变量 FINHACK_LLM__API_KEY\n"
            "3. 重新运行此示例",
            title="完成",
            border_style="green",
        ))

    except Exception as e:
        console.print(f"[red]运行出错: {e}[/red]")
        raise
    finally:
        await coordinator.stop()


if __name__ == "__main__":
    asyncio.run(main())
