"""
技术指标计算模块

使用ta库计算常用技术指标，包括RSI、MACD、布林带、均线、ATR、OBV等。
提供统一的接口和独立函数。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class TechnicalIndicator:
    """技术指标计算器

    使用ta库计算常用技术指标，提供统一的计算接口。

    Usage:
        ti = TechnicalIndicator()
        df = ti.add_all_indicators(df)
        # 或单独计算
        df = ti.add_rsi(df, period=14)
        df = ti.add_macd(df)
    """

    def __init__(self) -> None:
        """初始化技术指标计算器"""
        self._indicators: Dict[str, Any] = {}

    def add_all_indicators(
        self,
        df: pd.DataFrame,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
        ma_periods: Optional[List[int]] = None,
        atr_period: int = 14,
    ) -> pd.DataFrame:
        """添加所有常用技术指标

        Args:
            df: 包含OHLCV数据的DataFrame
            rsi_period: RSI周期
            macd_fast: MACD快线周期
            macd_slow: MACD慢线周期
            macd_signal: MACD信号线周期
            bb_period: 布林带周期
            bb_std: 布林带标准差倍数
            ma_periods: 均线周期列表
            atr_period: ATR周期

        Returns:
            添加了技术指标的DataFrame
        """
        if ma_periods is None:
            ma_periods = [5, 10, 20, 60, 120]

        result = df.copy()

        # RSI
        result = self.add_rsi(result, period=rsi_period)

        # MACD
        result = self.add_macd(result, fast=macd_fast, slow=macd_slow, signal=macd_signal)

        # 布林带
        result = self.add_bollinger_bands(result, period=bb_period, std_dev=bb_std)

        # 均线
        result = self.add_ma(result, periods=ma_periods)

        # ATR
        result = self.add_atr(result, period=atr_period)

        # OBV
        result = self.add_obv(result)

        # KDJ
        result = self.add_kdj(result)

        # Williams %R
        result = self.add_williams_r(result)

        logger.info(f"技术指标计算完成: {len(result)}条记录")
        return result

    @staticmethod
    def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """计算RSI(相对强弱指标)

        Args:
            df: OHLCV DataFrame
            period: RSI周期

        Returns:
            添加了RSI列的DataFrame
        """
        try:
            import ta
            df["rsi"] = ta.momentum.rsi(df["close"], window=period)
        except ImportError:
            # 手动计算RSI
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            df["rsi"] = 100.0 - (100.0 / (1.0 + rs))

        return df

    @staticmethod
    def add_macd(
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> pd.DataFrame:
        """计算MACD(指数平滑异同移动平均线)

        Args:
            df: OHLCV DataFrame
            fast: 快线EMA周期
            slow: 慢线EMA周期
            signal: 信号线EMA周期

        Returns:
            添加了MACD列的DataFrame
        """
        try:
            import ta
            macd_obj = ta.trend.MACD(
                df["close"],
                window_slow=slow,
                window_fast=fast,
                window_sign=signal,
            )
            df["macd"] = macd_obj.macd()
            df["macd_signal"] = macd_obj.macd_signal()
            df["macd_hist"] = macd_obj.macd_diff()
        except ImportError:
            # 手动计算MACD
            ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
            ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
            df["macd"] = ema_fast - ema_slow
            df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
            df["macd_hist"] = df["macd"] - df["macd_signal"]

        return df

    @staticmethod
    def add_bollinger_bands(
        df: pd.DataFrame,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> pd.DataFrame:
        """计算布林带(Bollinger Bands)

        Args:
            df: OHLCV DataFrame
            period: 布林带周期
            std_dev: 标准差倍数

        Returns:
            添加了布林带列的DataFrame
        """
        try:
            import ta
            bb = ta.volatility.BollingerBands(
                df["close"],
                window=period,
                window_dev=std_dev,
            )
            df["bb_upper"] = bb.bollinger_hband()
            df["bb_middle"] = bb.bollinger_mavg()
            df["bb_lower"] = bb.bollinger_lband()
            df["bb_width"] = bb.bollinger_wband()
        except ImportError:
            # 手动计算布林带
            df["bb_middle"] = df["close"].rolling(period).mean()
            bb_std = df["close"].rolling(period).std()
            df["bb_upper"] = df["bb_middle"] + std_dev * bb_std
            df["bb_lower"] = df["bb_middle"] - std_dev * bb_std
            df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_middle"]

        return df

    @staticmethod
    def add_ma(df: pd.DataFrame, periods: Optional[List[int]] = None) -> pd.DataFrame:
        """计算移动平均线

        Args:
            df: OHLCV DataFrame
            periods: 均线周期列表

        Returns:
            添加了均线列的DataFrame
        """
        if periods is None:
            periods = [5, 10, 20, 60, 120]

        for period in periods:
            df[f"ma_{period}"] = df["close"].rolling(period).mean()

        return df

    @staticmethod
    def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """计算ATR(真实波动幅度)

        Args:
            df: OHLCV DataFrame
            period: ATR周期

        Returns:
            添加了ATR列的DataFrame
        """
        try:
            import ta
            df["atr"] = ta.volatility.average_true_range(
                df["high"], df["low"], df["close"], window=period
            )
        except ImportError:
            # 手动计算ATR
            high_low = df["high"] - df["low"]
            high_close = (df["high"] - df["close"].shift()).abs()
            low_close = (df["low"] - df["close"].shift()).abs()
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df["atr"] = tr.rolling(period).mean()

        return df

    @staticmethod
    def add_obv(df: pd.DataFrame) -> pd.DataFrame:
        """计算OBV(能量潮)

        Args:
            df: OHLCV DataFrame

        Returns:
            添加了OBV列的DataFrame
        """
        try:
            import ta
            df["obv"] = ta.volume.on_balance_volume(df["close"], df["volume"])
        except ImportError:
            # 手动计算OBV
            direction = np.sign(df["close"].diff())
            df["obv"] = (direction * df["volume"]).cumsum()

        return df

    @staticmethod
    def add_kdj(df: pd.DataFrame, period: int = 9) -> pd.DataFrame:
        """计算KDJ指标

        Args:
            df: OHLCV DataFrame
            period: KDJ周期

        Returns:
            添加了KDJ列的DataFrame
        """
        low_min = df["low"].rolling(period).min()
        high_max = df["high"].rolling(period).max()

        rsv = (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan) * 100

        # K和D使用EMA平滑
        df["k"] = rsv.ewm(com=2, adjust=False).mean()
        df["d"] = df["k"].ewm(com=2, adjust=False).mean()
        df["j"] = 3 * df["k"] - 2 * df["d"]

        return df

    @staticmethod
    def add_williams_r(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """计算Williams %R

        Args:
            df: OHLCV DataFrame
            period: 周期

        Returns:
            添加了Williams %R列的DataFrame
        """
        high_max = df["high"].rolling(period).max()
        low_min = df["low"].rolling(period).min()
        df["williams_r"] = (
            (high_max - df["close"]) / (high_max - low_min).replace(0, np.nan) * -100
        )
        return df

    @staticmethod
    def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
        """计算VWAP(成交量加权平均价)

        Args:
            df: OHLCV DataFrame

        Returns:
            添加了VWAP列的DataFrame
        """
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        df["vwap"] = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()
        return df

    def get_indicators_summary(self, df: pd.DataFrame) -> Dict[str, Any]:
        """获取最新技术指标摘要

        Args:
            df: 包含技术指标的DataFrame

        Returns:
            技术指标摘要字典
        """
        if df.empty:
            return {}

        latest = df.iloc[-1]
        summary: Dict[str, Any] = {}

        # RSI
        if "rsi" in df.columns:
            rsi = latest["rsi"]
            summary["rsi"] = round(float(rsi), 2)
            if rsi > 70:
                summary["rsi_signal"] = "overbought"
            elif rsi < 30:
                summary["rsi_signal"] = "oversold"
            else:
                summary["rsi_signal"] = "neutral"

        # MACD
        if "macd" in df.columns:
            summary["macd"] = round(float(latest["macd"]), 4)
            summary["macd_signal"] = round(float(latest.get("macd_signal", 0)), 4)
            summary["macd_hist"] = round(float(latest.get("macd_hist", 0)), 4)
            summary["macd_cross"] = (
                "bullish" if latest["macd"] > latest.get("macd_signal", 0) else "bearish"
            )

        # 布林带
        if "bb_upper" in df.columns:
            summary["bb_upper"] = round(float(latest["bb_upper"]), 2)
            summary["bb_middle"] = round(float(latest["bb_middle"]), 2)
            summary["bb_lower"] = round(float(latest["bb_lower"]), 2)
            price = latest["close"]
            bb_position = (price - latest["bb_lower"]) / (
                latest["bb_upper"] - latest["bb_lower"] + 1e-8
            )
            summary["bb_position"] = round(float(bb_position), 4)

        # 均线
        ma_cols = [c for c in df.columns if c.startswith("ma_")]
        for col in sorted(ma_cols):
            summary[col] = round(float(latest[col]), 2)

        # ATR
        if "atr" in df.columns:
            summary["atr"] = round(float(latest["atr"]), 4)

        return summary
