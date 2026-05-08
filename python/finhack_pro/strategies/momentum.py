"""
动量策略

基于N日收益率排名的截面动量选股策略。
选择动量最强的前K只股票买入，定期调仓。
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


class MomentumStrategy(BaseStrategy):
    """截面动量选股策略

    策略原理:
    1. 计算所有候选股票的N日收益率
    2. 按收益率排名，选择前K只
    3. 每隔rebalance_days天调仓
    4. 买入动量最强的，卖出不在名单中的

    参数:
        - lookback: 动量计算周期(默认20个交易日)
        - top_k: 买入前K只(默认5)
        - rebalance_days: 调仓周期(默认5个交易日)
        - stop_loss_pct: 止损百分比(默认0.05)

    Usage:
        strategy = MomentumStrategy()
        strategy.set_parameters({
            "lookback": 20,
            "top_k": 5,
            "rebalance_days": 5,
        })
    """

    def __init__(self) -> None:
        super().__init__()
        self.strategy_name = "Momentum"
        self._params = {
            "lookback": 20,
            "top_k": 5,
            "rebalance_days": 5,
            "stop_loss_pct": 0.05,
        }
        # 策略状态
        self._price_history: Dict[str, List[float]] = {}
        self._current_holdings: set = set()
        self._days_since_rebalance: int = 0
        self._candidate_symbols: List[str] = []

    def on_init(self, context: Context) -> None:
        """策略初始化"""
        self._params.update(context.params)
        self._price_history = {}
        self._current_holdings = set()
        self._days_since_rebalance = self._params["rebalance_days"]  # 首日即调仓
        self._candidate_symbols = context.config.get("symbols", [])
        logger.info(
            f"动量策略初始化: lookback={self._params['lookback']}, "
            f"top_k={self._params['top_k']}, "
            f"rebalance_days={self._params['rebalance_days']}"
        )

    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        """K线回调"""
        signals: List[Signal] = []

        # 记录价格
        if bar.symbol not in self._price_history:
            self._price_history[bar.symbol] = []
        self._price_history[bar.symbol].append(bar.close)

        # 保留足够的历史数据
        lookback = self._params["lookback"]
        if len(self._price_history[bar.symbol]) > lookback + 1:
            self._price_history[bar.symbol] = self._price_history[bar.symbol][
                -(lookback + 1):
            ]

        # 检查是否需要调仓
        self._days_since_rebalance += 1
        if self._days_since_rebalance < self._params["rebalance_days"]:
            return signals

        # 只在第一个收到数据的标的触发调仓
        if bar.symbol != (self._candidate_symbols[0] if self._candidate_symbols else bar.symbol):
            return signals

        self._days_since_rebalance = 0
        signals = self._rebalance(context)

        return signals

    def _rebalance(self, context: Context) -> List[Signal]:
        """执行调仓逻辑

        Args:
            context: 策略上下文

        Returns:
            交易信号列表
        """
        signals: List[Signal] = []
        lookback = self._params["lookback"]
        top_k = self._params["top_k"]

        # 计算各标的的动量(N日收益率)
        momentum_scores: Dict[str, float] = {}
        for symbol, prices in self._price_history.items():
            if len(prices) < lookback + 1:
                continue
            past_price = prices[-(lookback + 1)]
            current_price = prices[-1]
            if past_price > 0:
                momentum_scores[symbol] = (
                    (current_price - past_price) / past_price
                )

        if not momentum_scores:
            return signals

        # 按动量排名
        sorted_symbols = sorted(
            momentum_scores.keys(),
            key=lambda s: momentum_scores[s],
            reverse=True,
        )

        # 选择前K只
        target_holdings = set(sorted_symbols[:top_k])

        logger.info(
            f"动量排名: {[(s, f'{momentum_scores[s]:.2%}') for s in sorted_symbols[:top_k]]}"
        )

        # 卖出不在目标持仓中的
        for symbol in self._current_holdings:
            if symbol not in target_holdings:
                prices = self._price_history.get(symbol, [])
                current_price = prices[-1] if prices else 0.0
                if current_price > 0:
                    signal = Signal(
                        symbol=symbol,
                        direction=SignalDirection.SELL,
                        price=current_price,
                        volume=100,
                        timestamp=context.current_time,
                        strategy_name=self.strategy_name,
                        extra={"reason": "not_in_top_k"},
                    )
                    signals.append(signal)
                    logger.info(f"动量调仓卖出: {symbol}")

        # 买入新进入目标持仓的
        for symbol in target_holdings:
            if symbol not in self._current_holdings:
                prices = self._price_history.get(symbol, [])
                current_price = prices[-1] if prices else 0.0
                if current_price > 0:
                    signal = Signal(
                        symbol=symbol,
                        direction=SignalDirection.BUY,
                        price=current_price,
                        volume=100,
                        stop_loss=current_price * (1 - self._params["stop_loss_pct"]),
                        timestamp=context.current_time,
                        strategy_name=self.strategy_name,
                        extra={
                            "reason": "momentum_rank",
                            "momentum_score": momentum_scores[symbol],
                            "rank": sorted_symbols.index(symbol) + 1,
                        },
                    )
                    signals.append(signal)
                    logger.info(
                        f"动量调仓买入: {symbol}, "
                        f"动量={momentum_scores[symbol]:.2%}"
                    )

        self._current_holdings = target_holdings
        return signals

    @staticmethod
    def calculate_momentum(
        df: pd.DataFrame,
        lookback: int = 20,
    ) -> pd.Series:
        """计算动量(N日收益率)

        Args:
            df: 包含close列的DataFrame
            lookback: 回看天数

        Returns:
            动量序列
        """
        return df["close"].pct_change(lookback)

    @staticmethod
    def rank_stocks(
        price_dict: Dict[str, pd.DataFrame],
        lookback: int = 20,
        top_k: int = 5,
    ) -> List[str]:
        """对多只股票进行动量排名

        Args:
            price_dict: {symbol: DataFrame} 字典
            lookback: 回看天数
            top_k: 选择前K只

        Returns:
            排名前K的标的列表
        """
        scores: Dict[str, float] = {}
        for symbol, df in price_dict.items():
            if len(df) < lookback + 1:
                continue
            momentum = df["close"].iloc[-1] / df["close"].iloc[-(lookback + 1)] - 1
            scores[symbol] = momentum

        sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [s[0] for s in sorted_stocks[:top_k]]
