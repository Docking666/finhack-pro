"""
均值回归策略

结合RSI超买超卖和布林带回归的均值回归策略。
当价格偏离均值过大时，预期价格将回归均值。
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


class MeanReversionStrategy(BaseStrategy):
    """均值回归策略

    策略原理:
    1. RSI策略: RSI低于oversold时买入(超卖反弹)，RSI高于overbought时卖出(超买回落)
    2. 布林带策略: 价格触及下轨时买入，触及上轨时卖出
    3. 两种信号可组合使用，提高准确性

    参数:
        - rsi_period: RSI计算周期(默认14)
        - oversold: RSI超卖阈值(默认30)
        - overbought: RSI超买阈值(默认70)
        - bb_period: 布林带周期(默认20)
        - bb_std: 布林带标准差倍数(默认2.0)
        - use_rsi: 是否使用RSI信号(默认True)
        - use_bb: 是否使用布林带信号(默认True)
        - stop_loss_pct: 止损百分比(默认0.05)
        - take_profit_pct: 止盈百分比(默认0.08)

    Usage:
        strategy = MeanReversionStrategy()
        strategy.set_parameters({
            "rsi_period": 14,
            "oversold": 30,
            "overbought": 70,
            "bb_period": 20,
            "bb_std": 2.0,
        })
    """

    def __init__(self) -> None:
        super().__init__()
        self.strategy_name = "MeanReversion"
        self._params = {
            "rsi_period": 14,
            "oversold": 30,
            "overbought": 70,
            "bb_period": 20,
            "bb_std": 2.0,
            "use_rsi": True,
            "use_bb": True,
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.08,
        }
        # 策略状态
        self._close_prices: List[float] = []
        self._current_rsi: float = 50.0
        self._bb_upper: float = 0.0
        self._bb_middle: float = 0.0
        self._bb_lower: float = 0.0
        self._in_position: bool = False

    def on_init(self, context: Context) -> None:
        """策略初始化"""
        self._params.update(context.params)
        self._close_prices = []
        self._in_position = False
        logger.info(
            f"均值回归策略初始化: RSI({self._params['rsi_period']}, "
            f"{self._params['oversold']}/{self._params['overbought']}), "
            f"BB({self._params['bb_period']}, {self._params['bb_std']})"
        )

    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        """K线回调"""
        signals: List[Signal] = []

        # 记录收盘价
        self._close_prices.append(bar.close)

        # 保留足够的历史数据
        max_period = max(self._params["rsi_period"], self._params["bb_period"]) + 1
        if len(self._close_prices) > max_period + 50:
            self._close_prices = self._close_prices[-(max_period + 50):]

        # 数据不足时跳过
        if len(self._close_prices) < max_period:
            return signals

        # 计算技术指标
        self._calculate_indicators()

        # 生成信号
        rsi_signal = self._check_rsi_signal(bar)
        bb_signal = self._check_bb_signal(bar)

        # 组合信号
        buy_signals = 0
        sell_signals = 0

        if self._params["use_rsi"] and rsi_signal:
            if rsi_signal == "buy":
                buy_signals += 1
            else:
                sell_signals += 1

        if self._params["use_bb"] and bb_signal:
            if bb_signal == "buy":
                buy_signals += 1
            else:
                sell_signals += 1

        # 买入条件: 至少一个买入信号且当前无持仓
        if buy_signals > 0 and not self._in_position:
            signal = Signal(
                symbol=bar.symbol,
                direction=SignalDirection.BUY,
                price=bar.close,
                volume=100,
                stop_loss=bar.close * (1 - self._params["stop_loss_pct"]),
                take_profit=bar.close * (1 + self._params["take_profit_pct"]),
                timestamp=bar.datetime,
                strategy_name=self.strategy_name,
                extra={
                    "rsi": self._current_rsi,
                    "bb_upper": self._bb_upper,
                    "bb_lower": self._bb_lower,
                    "buy_signals": buy_signals,
                },
            )
            signals.append(signal)
            self._in_position = True
            logger.info(
                f"[{bar.symbol}] 均值回归买入: 价格={bar.close:.2f}, "
                f"RSI={self._current_rsi:.1f}, 信号数={buy_signals}"
            )

        # 卖出条件: 至少一个卖出信号且当前有持仓
        elif sell_signals > 0 and self._in_position:
            signal = Signal(
                symbol=bar.symbol,
                direction=SignalDirection.SELL,
                price=bar.close,
                volume=100,
                timestamp=bar.datetime,
                strategy_name=self.strategy_name,
                extra={
                    "rsi": self._current_rsi,
                    "bb_upper": self._bb_upper,
                    "bb_lower": self._bb_lower,
                    "sell_signals": sell_signals,
                },
            )
            signals.append(signal)
            self._in_position = False
            logger.info(
                f"[{bar.symbol}] 均值回归卖出: 价格={bar.close:.2f}, "
                f"RSI={self._current_rsi:.1f}, 信号数={sell_signals}"
            )

        return signals

    def _calculate_indicators(self) -> None:
        """计算RSI和布林带指标"""
        closes = np.array(self._close_prices)

        # 计算RSI
        rsi_period = self._params["rsi_period"]
        self._current_rsi = self._compute_rsi(closes, rsi_period)

        # 计算布林带
        bb_period = self._params["bb_period"]
        bb_std = self._params["bb_std"]
        self._bb_middle, self._bb_upper, self._bb_lower = self._compute_bollinger(
            closes, bb_period, bb_std
        )

    @staticmethod
    def _compute_rsi(prices: np.ndarray, period: int = 14) -> float:
        """计算RSI指标

        Args:
            prices: 收盘价序列
            period: RSI周期

        Returns:
            当前RSI值
        """
        if len(prices) < period + 1:
            return 50.0

        deltas = np.diff(prices[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def _compute_bollinger(
        prices: np.ndarray,
        period: int = 20,
        std_mult: float = 2.0,
    ) -> tuple:
        """计算布林带

        Args:
            prices: 收盘价序列
            period: 布林带周期
            std_mult: 标准差倍数

        Returns:
            (中轨, 上轨, 下轨)
        """
        if len(prices) < period:
            return prices[-1], prices[-1], prices[-1]

        recent = prices[-period:]
        middle = np.mean(recent)
        std = np.std(recent)
        upper = middle + std_mult * std
        lower = middle - std_mult * std
        return middle, upper, lower

    def _check_rsi_signal(self, bar: BarData) -> Optional[str]:
        """检查RSI信号

        Args:
            bar: 当前K线

        Returns:
            "buy" / "sell" / None
        """
        oversold = self._params["oversold"]
        overbought = self._params["overbought"]

        if self._current_rsi <= oversold:
            return "buy"
        elif self._current_rsi >= overbought:
            return "sell"
        return None

    def _check_bb_signal(self, bar: BarData) -> Optional[str]:
        """检查布林带信号

        Args:
            bar: 当前K线

        Returns:
            "buy" / "sell" / None
        """
        if bar.close <= self._bb_lower:
            return "buy"
        elif bar.close >= self._bb_upper:
            return "sell"
        return None

    @staticmethod
    def backtest_with_dataframe(
        df: pd.DataFrame,
        rsi_period: int = 14,
        oversold: float = 30,
        overbought: float = 70,
        bb_period: int = 20,
        bb_std: float = 2.0,
        initial_capital: float = 1_000_000.0,
    ) -> Dict[str, Any]:
        """使用DataFrame进行简单回测

        Args:
            df: 包含OHLCV数据的DataFrame
            rsi_period: RSI周期
            oversold: 超卖阈值
            overbought: 超买阈值
            bb_period: 布林带周期
            bb_std: 布林带标准差
            initial_capital: 初始资金

        Returns:
            回测结果字典
        """
        if df.empty:
            return {"error": "数据为空"}

        df = df.copy()
        df = df.sort_values("date").reset_index(drop=True)

        # 计算RSI
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(rsi_period).mean()
        avg_loss = loss.rolling(rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

        # 计算布林带
        df["bb_middle"] = df["close"].rolling(bb_period).mean()
        bb_std_val = df["close"].rolling(bb_period).std()
        df["bb_upper"] = df["bb_middle"] + bb_std * bb_std_val
        df["bb_lower"] = df["bb_middle"] - bb_std * bb_std_val

        # 模拟交易
        position = 0
        cash = initial_capital
        trades: List[Dict[str, Any]] = []
        stop_loss = 0.0
        take_profit = 0.0

        for i in range(max(rsi_period, bb_period), len(df)):
            row = df.iloc[i]
            price = row["close"]
            rsi = row["rsi"]
            bb_lower = row["bb_lower"]
            bb_upper = row["bb_upper"]

            if np.isnan(rsi) or np.isnan(bb_lower) or np.isnan(bb_upper):
                continue

            # 止损止盈检查
            if position > 0:
                if price <= stop_loss:
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
                    cash += position * price
                    trades.append({
                        "date": row["date"],
                        "action": "take_profit_sell",
                        "price": price,
                        "volume": position,
                    })
                    position = 0
                    continue

            # RSI超卖 + 布林带下轨 -> 买入
            if position == 0 and rsi <= oversold and price <= bb_lower:
                volume = int(cash * 0.9 / price / 100) * 100
                if volume >= 100:
                    cash -= volume * price
                    position = volume
                    stop_loss = price * 0.95
                    take_profit = price * 1.08
                    trades.append({
                        "date": row["date"],
                        "action": "buy",
                        "price": price,
                        "volume": volume,
                        "rsi": rsi,
                    })

            # RSI超买 + 布林带上轨 -> 卖出
            elif position > 0 and rsi >= overbought and price >= bb_upper:
                cash += position * price
                trades.append({
                    "date": row["date"],
                    "action": "sell",
                    "price": price,
                    "volume": position,
                    "rsi": rsi,
                })
                position = 0

        final_value = cash + position * df.iloc[-1]["close"]
        total_return = (final_value - initial_capital) / initial_capital

        return {
            "initial_capital": initial_capital,
            "final_value": final_value,
            "total_return": total_return,
            "total_trades": len(trades),
            "trades": trades,
        }
