"""
辅助函数模块

提供量化交易常用的辅助函数。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import List, Optional

import numpy as np


def generate_order_id(prefix: str = "ORD") -> str:
    """生成唯一订单ID

    Args:
        prefix: 订单ID前缀

    Returns:
        格式为 {prefix}_{timestamp}_{uuid} 的订单ID
    """
    timestamp = int(time.time() * 1000)
    unique_id = uuid.uuid4().hex[:8].upper()
    return f"{prefix}_{timestamp}_{unique_id}"


def timestamp_to_datetime(timestamp: float) -> datetime:
    """将时间戳转换为datetime对象

    Args:
        timestamp: Unix时间戳(秒或毫秒)

    Returns:
        datetime对象
    """
    if timestamp > 1e12:
        # 毫秒级时间戳
        timestamp = timestamp / 1000.0
    return datetime.fromtimestamp(timestamp)


def format_number(value: float, decimals: int = 2) -> str:
    """格式化数字，添加千分位分隔符

    Args:
        value: 数值
        decimals: 小数位数

    Returns:
        格式化后的字符串
    """
    return f"{value:,.{decimals}f}"


def format_percent(value: float, decimals: int = 2) -> str:
    """格式化百分比

    Args:
        value: 小数形式的百分比(如0.05表示5%)
        decimals: 小数位数

    Returns:
        格式化后的百分比字符串
    """
    return f"{value * 100:.{decimals}f}%"


def calculate_sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = 0.03,
    periods_per_year: int = 252,
) -> float:
    """计算夏普比率

    Args:
        returns: 收益率序列
        risk_free_rate: 无风险利率(年化)
        periods_per_year: 年化周期数

    Returns:
        夏普比率
    """
    if not returns or len(returns) < 2:
        return 0.0

    arr = np.array(returns)
    avg_return = np.mean(arr) * periods_per_year
    std_return = np.std(arr) * np.sqrt(periods_per_year)

    if std_return == 0:
        return 0.0

    return (avg_return - risk_free_rate) / std_return


def calculate_max_drawdown(equity_curve: List[float]) -> float:
    """计算最大回撤

    Args:
        equity_curve: 权益曲线(净值序列)

    Returns:
        最大回撤(正数，如0.15表示15%)
    """
    if not equity_curve:
        return 0.0

    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    drawdown = (peak - arr) / peak
    return float(np.max(drawdown))


def calculate_sortino_ratio(
    returns: List[float],
    risk_free_rate: float = 0.03,
    periods_per_year: int = 252,
) -> float:
    """计算Sortino比率(仅考虑下行波动)

    Args:
        returns: 收益率序列
        risk_free_rate: 无风险利率
        periods_per_year: 年化周期数

    Returns:
        Sortino比率
    """
    if not returns or len(returns) < 2:
        return 0.0

    arr = np.array(returns)
    avg_return = np.mean(arr) * periods_per_year

    # 下行波动率(仅计算负收益的标准差)
    downside = arr[arr < 0]
    if len(downside) == 0:
        return float("inf")

    downside_std = np.std(downside) * np.sqrt(periods_per_year)
    return (avg_return - risk_free_rate) / downside_std


def calculate_calmar_ratio(
    annual_return: float,
    max_drawdown: float,
) -> float:
    """计算Calmar比率(年化收益/最大回撤)

    Args:
        annual_return: 年化收益率
        max_drawdown: 最大回撤(正数)

    Returns:
        Calmar比率
    """
    if max_drawdown == 0:
        return float("inf")
    return annual_return / max_drawdown


def calculate_win_rate(trades: list) -> float:
    """计算胜率

    Args:
        trades: 交易记录列表，每条需包含"pnl"字段

    Returns:
        胜率(0-1)
    """
    if not trades:
        return 0.0

    winning = sum(1 for t in trades if t.get("pnl", 0) > 0)
    return winning / len(trades)


def calculate_profit_loss_ratio(trades: list) -> float:
    """计算盈亏比

    Args:
        trades: 交易记录列表

    Returns:
        盈亏比
    """
    wins = [t["pnl"] for t in trades if t.get("pnl", 0) > 0]
    losses = [abs(t["pnl"]) for t in trades if t.get("pnl", 0) < 0]

    if not wins or not losses:
        return 0.0

    avg_win = np.mean(wins)
    avg_loss = np.mean(losses)

    return avg_win / avg_loss if avg_loss > 0 else 0.0


def normalize_symbol(symbol: str) -> str:
    """标准化标的代码

    Args:
        symbol: 原始代码(可能带或不带后缀)

    Returns:
        标准化后的代码(纯数字)
    """
    return symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "").strip()


def is_stock_symbol(symbol: str) -> bool:
    """判断是否为A股代码

    Args:
        symbol: 标的代码

    Returns:
        是否为A股代码
    """
    code = normalize_symbol(symbol)
    if len(code) != 6:
        return False
    return code.startswith(("6", "0", "3", "4", "8"))


def clamp(value: float, min_val: float, max_val: float) -> float:
    """将值限制在指定范围内

    Args:
        value: 输入值
        min_val: 最小值
        max_val: 最大值

    Returns:
        限制后的值
    """
    return max(min_val, min(value, max_val))
