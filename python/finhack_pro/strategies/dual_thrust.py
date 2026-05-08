"""
Dual Thrust策略

经典开盘区间突破策略，由Michael Chalek开发。
通过计算N日最高价-收盘价和收盘价-最低价的范围来确定上下轨，
当价格突破上轨时买入，突破下轨时卖出。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from finhack_pro.strategies.base import (
    BarData,
    BaseStrategy,
    Context,
    Signal,
    SignalDirection,
)
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class DualThrustStrategy(BaseStrategy):
    """Dual Thrust 开盘区间突破策略

    策略原理:
    1. 计算过去N日的:
       - N1 = HH(最高价) - LC(收盘价)
       - N2 = HC(收盘价) - LL(最低价)
    2. 计算上下轨:
       - 上轨 = 今日开盘价 + k1 * max(N1, N2)
       - 下轨 = 今日开盘价 - k2 * max(N1, N2)
    3. 突破上轨买入，突破下轨卖出

    参数:
        - k1: 上轨系数 (默认0.5)
        - k2: 下轨系数 (默认0.5)
        - lookback: 回看天数 (默认20)
        - stop_loss_pct: 止损百分比 (默认0.03)
        - take_profit_pct: 止盈百分比 (默认0.06)

    Usage:
        strategy = DualThrustStrategy()
        strategy.set_parameters({
            "k1": 0.5,
            "k2": 0.5,
            "lookback": 20,
        })
    """

    def __init__(self) -> None:
        super().__init__()
        self.strategy_name = "DualThrust"
        self._params = {
            "k1": 0.5,
            "k2": 0.5,
            "lookback": 20,
            "stop_loss_pct": 0.03,
            "take_profit_pct": 0.06,
        }
        # 策略状态
        self._history_bars: List[BarData] = []
        self._upper_band: float = 0.0
        self._lower_band: float = 0.0
        self._today_open: float = 0.0
        self._signal_generated_today: bool = False

    def on_init(self, context: Context) -> None:
        """策略初始化"""
        self._params.update(context.params)
        self._history_bars = []
        self._signal_generated_today = False
        logger.info(
            f"Dual Thrust策略初始化: k1={self._params['k1']}, "
            f"k2={self._params['k2']}, lookback={self._params['lookback']}"
        )

    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        """K线回调"""
        signals: List[Signal] = []

        # 添加到历史数据
        self._history_bars.append(bar)

        # 保留足够的历史数据
        lookback = self._params["lookback"]
        if len(self._history_bars) < lookback + 1:
            return signals

        # 只保留最近的数据
        if len(self._history_bars) > lookback + 1:
            self._history_bars = self._history_bars[-(lookback + 1):]

        # 计算上下轨
        self._calculate_bands()

        # 检查突破信号
        if not self._signal_generated_today:
            k1 = self._params["k1"]
            k2 = self._params["k2"]
            stop_loss_pct = self._params["stop_loss_pct"]
            take_profit_pct = self._params["take_profit_pct"]

            # 突破上轨 -> 买入
            if bar.close > self._upper_band:
                signal = Signal(
                    symbol=bar.symbol,
                    direction=SignalDirection.BUY,
                    price=bar.close,
                    volume=100,
                    stop_loss=bar.close * (1 - stop_loss_pct),
                    take_profit=bar.close * (1 + take_profit_pct),
                    timestamp=bar.datetime,
                    strategy_name=self.strategy_name,
                    extra={
                        "upper_band": self._upper_band,
                        "lower_band": self._lower_band,
                        "breakout_pct": (bar.close - self._upper_band) / self._upper_band,
                    },
                )
                signals.append(signal)
                self._signal_generated_today = True
                logger.info(
                    f"[{bar.symbol}] 突破上轨买入: 价格={bar.close:.2f}, "
                    f"上轨={self._upper_band:.2f}"
                )

            # 突破下轨 -> 卖出
            elif bar.close < self._lower_band:
                signal = Signal(
                    symbol=bar.symbol,
                    direction=SignalDirection.SELL,
                    price=bar.close,
                    volume=100,
                    stop_loss=bar.close * (1 + stop_loss_pct),
                    take_profit=bar.close * (1 - take_profit_pct),
                    timestamp=bar.datetime,
                    strategy_name=self.strategy_name,
                    extra={
                        "upper_band": self._upper_band,
                        "lower_band": self._lower_band,
                        "breakout_pct": (self._lower_band - bar.close) / self._lower_band,
                    },
                )
                signals.append(signal)
                self._signal_generated_today = True
                logger.info(
                    f"[{bar.symbol}] 突破下轨卖出: 价格={bar.close:.2f}, "
                    f"下轨={self._lower_band:.2f}"
                )

        return signals

    def _calculate_bands(self) -> None:
        """计算Dual Thrust上下轨"""
        lookback = self._params["lookback"]
        k1 = self._params["k1"]
        k2 = self._params["k2"]

        # 取前lookback根K线(不含当日)
        history = self._history_bars[:-1]
        if len(history) < lookback:
            history = self._history_bars[:-1]

        if not history:
            return

        highs = np.array([bar.high for bar in history])
        lows = np.array([bar.low for bar in history])
        closes = np.array([bar.close for bar in history])

        # 计算N1和N2
        hh = np.max(highs)  # 最高价的最大值
        ll = np.min(lows)  # 最低价的最小值
        hc = np.max(closes)  # 收盘价的最大值
        lc = np.min(closes)  # 收盘价的最小值

        n1 = hh - lc
        n2 = hc - ll

        # 今日开盘价
        self._today_open = self._history_bars[-1].open

        # 计算上下轨
        range_val = max(n1, n2)
        self._upper_band = self._today_open + k1 * range_val
        self._lower_band = self._today_open - k2 * range_val

    @staticmethod
    def backtest_with_dataframe(
        df: pd.DataFrame,
        k1: float = 0.5,
        k2: float = 0.5,
        lookback: int = 20,
        initial_capital: float = 1_000_000.0,
    ) -> Dict[str, Any]:
        """使用DataFrame进行简单回测

        Args:
            df: 包含OHLCV数据的DataFrame
            k1: 上轨系数
            k2: 下轨系数
            lookback: 回看天数
            initial_capital: 初始资金

        Returns:
            回测结果字典
        """
        if df.empty:
            return {"error": "数据为空"}

        df = df.copy()
        df = df.sort_values("date").reset_index(drop=True)

        # 计算指标
        df["hh"] = df["high"].rolling(lookback).max()
        df["ll"] = df["low"].rolling(lookback).min()
        df["hc"] = df["close"].rolling(lookback).max()
        df["lc"] = df["close"].rolling(lookback).min()

        df["n1"] = df["hh"] - df["lc"]
        df["n2"] = df["hc"] - df["ll"]
        df["range_val"] = df[["n1", "n2"]].max(axis=1)

        df["upper_band"] = df["open"] + k1 * df["range_val"]
        df["lower_band"] = df["open"] - k2 * df["range_val"]

        # 模拟交易
        position = 0  # 持仓数量
        cash = initial_capital
        trades: List[Dict[str, Any]] = []
        stop_loss = 0.0
        take_profit = 0.0

        for i in range(lookback, len(df)):
            row = df.iloc[i]
            price = row["close"]
            upper = row["upper_band"]
            lower = row["lower_band"]

            if np.isnan(upper) or np.isnan(lower):
                continue

            # 检查止损止盈
            if position > 0:
                if price <= stop_loss:
                    # 止损卖出
                    cash += position * price
                    trades.append({
                        "date": row["date"],
                        "action": "stop_loss_sell",
                        "price": price,
                        "volume": position,
                    })
                    position = 0
                    continue
                elif price >= take_profit:
                    # 止盈卖出
                    cash += position * price
                    trades.append({
                        "date": row["date"],
                        "action": "take_profit_sell",
                        "price": price,
                        "volume": position,
                    })
                    position = 0
                    continue

            # 突破上轨买入
            if position == 0 and price > upper:
                volume = int(cash * 0.9 / price / 100) * 100
                if volume >= 100:
                    cost = volume * price
                    cash -= cost
                    position = volume
                    stop_loss = price * 0.97
                    take_profit = price * 1.06
                    trades.append({
                        "date": row["date"],
                        "action": "buy",
                        "price": price,
                        "volume": volume,
                    })

            # 突破下轨卖出
            elif position > 0 and price < lower:
                cash += position * price
                trades.append({
                    "date": row["date"],
                    "action": "sell",
                    "price": price,
                    "volume": position,
                })
                position = 0

        # 计算最终结果
        final_value = cash + position * df.iloc[-1]["close"]
        total_return = (final_value - initial_capital) / initial_capital

        return {
            "initial_capital": initial_capital,
            "final_value": final_value,
            "total_return": total_return,
            "total_trades": len(trades),
            "trades": trades,
        }
