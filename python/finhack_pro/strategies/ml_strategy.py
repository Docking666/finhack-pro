"""
ML增强策略

结合传统技术指标和机器学习模型的增强型策略。
使用特征工程生成因子，通过ML模型预测涨跌概率。
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


class MLStrategy(BaseStrategy):
    """ML增强策略

    策略原理:
    1. 计算多维技术特征(动量、波动率、成交量等)
    2. 使用ML模型预测未来N日涨跌概率
    3. 根据预测概率和置信度生成交易信号
    4. 结合风控规则过滤信号

    参数:
        - prediction_horizon: 预测周期(默认5)
        - buy_threshold: 买入概率阈值(默认0.6)
        - sell_threshold: 卖出概率阈值(默认0.6)
        - feature_window: 特征计算窗口(默认20)
        - model_type: 模型类型(默认"random_forest")
        - stop_loss_pct: 止损百分比(默认0.05)
        - take_profit_pct: 止盈百分比(默认0.10)

    注意:
    - 此策略需要预先训练的ML模型
    - 如果模型未加载，将使用简单的规则作为fallback
    - 支持sklearn模型(pickle格式)

    Usage:
        strategy = MLStrategy()
        strategy.set_parameters({
            "prediction_horizon": 5,
            "buy_threshold": 0.6,
            "model_path": "models/rf_model.pkl",
        })
    """

    def __init__(self) -> None:
        super().__init__()
        self.strategy_name = "ML_Enhanced"
        self._params = {
            "prediction_horizon": 5,
            "buy_threshold": 0.6,
            "sell_threshold": 0.6,
            "feature_window": 20,
            "model_type": "random_forest",
            "model_path": "",
            "stop_loss_pct": 0.05,
            "take_profit_pct": 0.10,
        }
        # 策略状态
        self._close_prices: List[float] = []
        self._high_prices: List[float] = []
        self._low_prices: List[float] = []
        self._volume_series: List[float] = []
        self._model: Optional[Any] = None
        self._model_loaded: bool = False
        self._in_position: bool = False

    def on_init(self, context: Context) -> None:
        """策略初始化"""
        self._params.update(context.params)
        self._close_prices = []
        self._high_prices = []
        self._low_prices = []
        self._volume_series = []
        self._in_position = False

        # 尝试加载ML模型
        model_path = self._params.get("model_path", "")
        if model_path:
            self._load_model(model_path)

        logger.info(
            f"ML增强策略初始化: horizon={self._params['prediction_horizon']}, "
            f"buy_thresh={self._params['buy_threshold']}, "
            f"model_loaded={self._model_loaded}"
        )

    def _load_model(self, model_path: str) -> None:
        """加载预训练的ML模型

        Args:
            model_path: 模型文件路径(pickle格式)
        """
        try:
            import pickle
            with open(model_path, "rb") as f:
                self._model = pickle.load(f)
            self._model_loaded = True
            logger.info(f"ML模型加载成功: {model_path}")
        except FileNotFoundError:
            logger.warning(f"模型文件不存在: {model_path}，使用规则fallback")
        except Exception as e:
            logger.warning(f"模型加载失败: {e}，使用规则fallback")

    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        """K线回调"""
        signals: List[Signal] = []

        # 记录数据
        self._close_prices.append(bar.close)
        self._high_prices.append(bar.high)
        self._low_prices.append(bar.low)
        self._volume_series.append(bar.volume)

        # 保留足够的历史数据
        window = self._params["feature_window"]
        max_len = window + 50
        if len(self._close_prices) > max_len:
            self._close_prices = self._close_prices[-max_len:]
            self._high_prices = self._high_prices[-max_len:]
            self._low_prices = self._low_prices[-max_len:]
            self._volume_series = self._volume_series[-max_len:]

        # 数据不足时跳过
        if len(self._close_prices) < window + 1:
            return signals

        # 计算特征
        features = self._compute_features()

        # 预测
        if self._model_loaded and self._model is not None:
            buy_prob, sell_prob = self._predict_with_model(features)
        else:
            buy_prob, sell_prob = self._predict_with_rules(features)

        # 生成信号
        buy_threshold = self._params["buy_threshold"]
        sell_threshold = self._params["sell_threshold"]

        # 买入信号
        if buy_prob >= buy_threshold and not self._in_position:
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
                    "buy_probability": buy_prob,
                    "sell_probability": sell_prob,
                    "features": features,
                },
            )
            signals.append(signal)
            self._in_position = True
            logger.info(
                f"[{bar.symbol}] ML买入: 价格={bar.close:.2f}, "
                f"买入概率={buy_prob:.2%}"
            )

        # 卖出信号
        elif sell_prob >= sell_threshold and self._in_position:
            signal = Signal(
                symbol=bar.symbol,
                direction=SignalDirection.SELL,
                price=bar.close,
                volume=100,
                timestamp=bar.datetime,
                strategy_name=self.strategy_name,
                extra={
                    "buy_probability": buy_prob,
                    "sell_probability": sell_prob,
                    "features": features,
                },
            )
            signals.append(signal)
            self._in_position = False
            logger.info(
                f"[{bar.symbol}] ML卖出: 价格={bar.close:.2f}, "
                f"卖出概率={sell_prob:.2%}"
            )

        return signals

    def _compute_features(self) -> Dict[str, float]:
        """计算技术特征

        Returns:
            特征字典
        """
        closes = np.array(self._close_prices)
        highs = np.array(self._high_prices)
        lows = np.array(self._low_prices)
        volumes = np.array(self._volume_series)
        window = self._params["feature_window"]

        recent_closes = closes[-window:]
        recent_highs = highs[-window:]
        recent_lows = lows[-window:]
        recent_volumes = volumes[-window:]

        features: Dict[str, float] = {
            # 动量特征
            "return_1d": (closes[-1] / closes[-2] - 1) if len(closes) >= 2 else 0.0,
            "return_5d": (closes[-1] / closes[-6] - 1) if len(closes) >= 6 else 0.0,
            "return_10d": (closes[-1] / closes[-11] - 1) if len(closes) >= 11 else 0.0,
            "return_20d": (closes[-1] / closes[-21] - 1) if len(closes) >= 21 else 0.0,
            # 波动率特征
            "volatility": np.std(np.diff(recent_closes) / recent_closes[:-1]) * np.sqrt(252),
            "atr": np.mean(recent_highs - recent_lows),
            # 均线特征
            "ma5": np.mean(closes[-5:]) if len(closes) >= 5 else closes[-1],
            "ma20": np.mean(recent_closes),
            "price_to_ma5": closes[-1] / (np.mean(closes[-5:]) if len(closes) >= 5 else 1),
            "price_to_ma20": closes[-1] / np.mean(recent_closes),
            # 成交量特征
            "volume_ma": np.mean(recent_volumes),
            "volume_ratio": volumes[-1] / (np.mean(recent_volumes) + 1e-8),
            "volume_trend": np.polyfit(range(len(recent_volumes)), recent_volumes, 1)[0],
            # 价格位置特征
            "price_position": (closes[-1] - np.min(recent_lows)) / (np.max(recent_highs) - np.min(recent_lows) + 1e-8),
            "high_low_range": (np.max(recent_highs) - np.min(recent_lows)) / np.mean(recent_closes),
        }

        return features

    def _predict_with_model(self, features: Dict[str, float]) -> tuple:
        """使用ML模型预测

        Args:
            features: 特征字典

        Returns:
            (买入概率, 卖出概率)
        """
        try:
            feature_array = np.array(list(features.values())).reshape(1, -1)
            proba = self._model.predict_proba(feature_array)[0]

            # 假设模型输出三类: [下跌, 持平, 上涨]
            if len(proba) >= 3:
                sell_prob = float(proba[0])
                buy_prob = float(proba[2])
            else:
                # 二分类: [下跌, 上涨]
                sell_prob = float(proba[0])
                buy_prob = float(proba[1])

            return buy_prob, sell_prob

        except Exception as e:
            logger.warning(f"ML预测失败: {e}，使用规则fallback")
            return self._predict_with_rules(features)

    def _predict_with_rules(self, features: Dict[str, float]) -> tuple:
        """使用简单规则预测(fallback)

        Args:
            features: 特征字典

        Returns:
            (买入概率, 卖出概率)
        """
        buy_score = 0.0
        sell_score = 0.0

        # 动量信号
        if features["return_5d"] > 0.03:
            buy_score += 0.2
        elif features["return_5d"] < -0.03:
            sell_score += 0.2

        # 均线信号
        if features["price_to_ma5"] > 1.02:
            buy_score += 0.15
        elif features["price_to_ma5"] < 0.98:
            sell_score += 0.15

        if features["price_to_ma20"] > 1.05:
            buy_score += 0.15
        elif features["price_to_ma20"] < 0.95:
            sell_score += 0.15

        # 成交量信号
        if features["volume_ratio"] > 1.5 and features["return_1d"] > 0:
            buy_score += 0.1
        elif features["volume_ratio"] > 1.5 and features["return_1d"] < 0:
            sell_score += 0.1

        # 价格位置
        if features["price_position"] < 0.2:
            buy_score += 0.15
        elif features["price_position"] > 0.8:
            sell_score += 0.15

        # 归一化
        total = buy_score + sell_score + 0.01
        buy_prob = buy_score / total
        sell_prob = sell_score / total

        return buy_prob, sell_prob

    @staticmethod
    def prepare_training_data(
        df: pd.DataFrame,
        prediction_horizon: int = 5,
        feature_window: int = 20,
    ) -> tuple:
        """准备ML训练数据

        Args:
            df: 包含OHLCV数据的DataFrame
            prediction_horizon: 预测周期
            feature_window: 特征窗口

        Returns:
            (X, y) 特征矩阵和标签
        """
        df = df.copy().sort_values("date").reset_index(drop=True)

        features_list = []
        labels = []

        for i in range(feature_window, len(df) - prediction_horizon):
            window_df = df.iloc[i - feature_window:i]

            # 计算特征
            closes = window_df["close"].values
            returns = np.diff(closes) / closes[:-1]

            feat = {
                "return_1d": returns[-1] if len(returns) > 0 else 0,
                "return_5d": (closes[-1] / closes[-5] - 1) if len(closes) >= 5 else 0,
                "volatility": np.std(returns) * np.sqrt(252) if len(returns) > 0 else 0,
                "ma_ratio": closes[-1] / np.mean(closes),
                "volume_ratio": window_df["volume"].iloc[-1] / (np.mean(window_df["volume"].values) + 1e-8),
            }
            features_list.append(feat)

            # 标签: 未来N日涨跌
            future_return = (
                df.iloc[i + prediction_horizon]["close"] / df.iloc[i]["close"] - 1
            )
            if future_return > 0.02:
                labels.append(2)  # 上涨
            elif future_return < -0.02:
                labels.append(0)  # 下跌
            else:
                labels.append(1)  # 持平

        X = pd.DataFrame(features_list)
        y = np.array(labels)

        return X, y
