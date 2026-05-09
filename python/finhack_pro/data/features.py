"""
特征工程模块

提供量化因子计算和特征构建功能。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class FeatureEngineer:
    """特征工程引擎

    提供多维量化因子计算，包括动量因子、波动率因子、
    成交量因子、技术形态因子等。

    Usage:
        engineer = FeatureEngineer()
        df = engineer.build_features(df, windows=[5, 10, 20, 60])
    """

    def __init__(self) -> None:
        """初始化特征工程引擎"""
        self._feature_registry: Dict[str, Any] = {}
        self._register_default_features()

    def _register_default_features(self) -> None:
        """注册默认特征计算函数"""
        self._feature_registry = {
            "return": self._calc_returns,
            "ma": self._calc_moving_average,
            "ema": self._calc_ema,
            "volatility": self._calc_volatility,
            "volume_ratio": self._calc_volume_ratio,
            "turnover_rate": self._calc_turnover_rate,
            "price_position": self._calc_price_position,
            "max_drawdown": self._calc_max_drawdown,
            "bias": self._calc_bias,
            "amplitude": self._calc_amplitude,
        }

    def build_features(
        self,
        df: pd.DataFrame,
        windows: Optional[List[int]] = None,
        feature_names: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """构建特征

        Args:
            df: 包含OHLCV数据的DataFrame
            windows: 计算窗口列表
            feature_names: 指定计算的特征名称列表(为None则计算全部)

        Returns:
            添加了特征列的DataFrame
        """
        if windows is None:
            windows = [5, 10, 20, 60]

        if feature_names is None:
            feature_names = list(self._feature_registry.keys())

        result = df.copy()

        for name in feature_names:
            if name not in self._feature_registry:
                logger.warning(f"未知特征: {name}")
                continue

            calc_func = self._feature_registry[name]
            try:
                result = calc_func(result, windows)
                logger.debug(f"特征计算完成: {name}")
            except Exception as e:
                logger.error(f"特征计算失败 {name}: {e}")

        return result

    def build_features_for_ml(
        self,
        df: pd.DataFrame,
        prediction_horizon: int = 5,
    ) -> tuple:
        """构建ML训练特征和标签

        Args:
            df: OHLCV DataFrame
            prediction_horizon: 预测周期

        Returns:
            (X, y) 特征DataFrame和标签Series
        """
        # 构建特征
        featured = self.build_features(df, windows=[5, 10, 20])

        # 构建标签: 未来N日收益率
        featured["future_return"] = featured["close"].shift(-prediction_horizon) / featured["close"] - 1
        featured["label"] = pd.cut(
            featured["future_return"],
            bins=[-np.inf, -0.02, 0.02, np.inf],
            labels=[0, 1, 2],  # 下跌, 持平, 上涨
        )

        # 去除NaN行
        featured = featured.dropna()

        # 选择特征列
        feature_cols = [c for c in featured.columns if c not in [
            "date", "label", "future_return"
        ]]

        X = featured[feature_cols]
        y = featured["label"].astype(int)

        return X, y

    @staticmethod
    def _calc_returns(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算收益率"""
        for w in windows:
            df[f"return_{w}d"] = df["close"].pct_change(w)
        return df

    @staticmethod
    def _calc_moving_average(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算移动平均"""
        for w in windows:
            df[f"ma_{w}"] = df["close"].rolling(w).mean()
            # 价格与均线的偏离度
            df[f"price_to_ma_{w}"] = df["close"] / df[f"ma_{w}"] - 1
        return df

    @staticmethod
    def _calc_ema(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算指数移动平均"""
        for w in windows:
            df[f"ema_{w}"] = df["close"].ewm(span=w, adjust=False).mean()
        return df

    @staticmethod
    def _calc_volatility(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算波动率"""
        for w in windows:
            df[f"volatility_{w}d"] = df["close"].pct_change().rolling(w).std() * np.sqrt(252)
        return df

    @staticmethod
    def _calc_volume_ratio(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算成交量比"""
        for w in windows:
            vol_ma = df["volume"].rolling(w).mean()
            df[f"volume_ratio_{w}d"] = df["volume"] / vol_ma.replace(0, np.nan)
        return df

    @staticmethod
    def _calc_turnover_rate(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算换手率均值"""
        if "turnover" in df.columns:
            for w in windows:
                df[f"turnover_ma_{w}d"] = df["turnover"].rolling(w).mean()
        return df

    @staticmethod
    def _calc_price_position(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算价格在区间内的位置"""
        for w in windows:
            rolling_high = df["high"].rolling(w).max()
            rolling_low = df["low"].rolling(w).min()
            price_range = rolling_high - rolling_low
            df[f"price_position_{w}d"] = (
                (df["close"] - rolling_low) / price_range.replace(0, np.nan)
            )
        return df

    @staticmethod
    def _calc_max_drawdown(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算滚动最大回撤"""
        for w in windows:
            rolling_max = df["close"].rolling(w).max()
            drawdown = df["close"] / rolling_max - 1
            df[f"max_drawdown_{w}d"] = drawdown.rolling(w).min()
        return df

    @staticmethod
    def _calc_bias(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算乖离率(BIAS)"""
        for w in windows:
            ma = df["close"].rolling(w).mean()
            df[f"bias_{w}d"] = (df["close"] - ma) / ma * 100
        return df

    @staticmethod
    def _calc_amplitude(df: pd.DataFrame, windows: List[int]) -> pd.DataFrame:
        """计算振幅"""
        for w in windows:
            df[f"amplitude_{w}d"] = (
                (df["high"].rolling(w).max() - df["low"].rolling(w).min())
                / df["close"].rolling(w).mean()
                * 100
            )
        return df
