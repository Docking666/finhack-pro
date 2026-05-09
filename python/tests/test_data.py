"""
数据模块测试
"""

import numpy as np
import pandas as pd
import pytest

from finhack_pro.data.features import FeatureEngineer
from finhack_pro.data.technical import TechnicalIndicator
from finhack_pro.utils.helpers import (
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    calculate_win_rate,
    format_number,
    format_percent,
    generate_order_id,
    normalize_symbol,
)


def _make_test_df(n: int = 100) -> pd.DataFrame:
    """创建测试用DataFrame"""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    prices = 100 + np.cumsum(np.random.randn(n) * 2)

    return pd.DataFrame({
        "date": dates,
        "open": prices * (1 + np.random.randn(n) * 0.005),
        "high": prices * (1 + np.abs(np.random.randn(n) * 0.01)),
        "low": prices * (1 - np.abs(np.random.randn(n) * 0.01)),
        "close": prices,
        "volume": np.random.randint(100000, 1000000, n).astype(float),
    })


class TestTechnicalIndicator:
    """技术指标测试"""

    def test_add_rsi(self):
        """测试RSI计算"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        result = ti.add_rsi(df, period=14)

        assert "rsi" in result.columns
        # RSI应该在0-100之间
        valid_rsi = result["rsi"].dropna()
        assert (valid_rsi >= 0).all() and (valid_rsi <= 100).all()

    def test_add_macd(self):
        """测试MACD计算"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        result = ti.add_macd(df)

        assert "macd" in result.columns
        assert "macd_signal" in result.columns
        assert "macd_hist" in result.columns

    def test_add_bollinger_bands(self):
        """测试布林带计算"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        result = ti.add_bollinger_bands(df, period=20, std_dev=2.0)

        assert "bb_upper" in result.columns
        assert "bb_middle" in result.columns
        assert "bb_lower" in result.columns

        # 上轨 > 中轨 > 下轨
        valid = result.dropna(subset=["bb_upper", "bb_middle", "bb_lower"])
        assert (valid["bb_upper"] >= valid["bb_middle"]).all()
        assert (valid["bb_middle"] >= valid["bb_lower"]).all()

    def test_add_ma(self):
        """测试均线计算"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        result = ti.add_ma(df, periods=[5, 10, 20])

        assert "ma_5" in result.columns
        assert "ma_10" in result.columns
        assert "ma_20" in result.columns

    def test_add_atr(self):
        """测试ATR计算"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        result = ti.add_atr(df, period=14)

        assert "atr" in result.columns
        valid_atr = result["atr"].dropna()
        assert (valid_atr >= 0).all()

    def test_add_obv(self):
        """测试OBV计算"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        result = ti.add_obv(df)

        assert "obv" in result.columns

    def test_add_all_indicators(self):
        """测试添加所有指标"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        result = ti.add_all_indicators(df)

        expected_cols = ["rsi", "macd", "bb_upper", "ma_5", "atr", "obv"]
        for col in expected_cols:
            assert col in result.columns

    def test_get_indicators_summary(self):
        """测试指标摘要"""
        df = _make_test_df(100)
        ti = TechnicalIndicator()
        df = ti.add_all_indicators(df)
        summary = ti.get_indicators_summary(df)

        assert "rsi" in summary
        assert "macd" in summary
        assert "bb_upper" in summary


class TestFeatureEngineer:
    """特征工程测试"""

    def test_build_features(self):
        """测试特征构建"""
        df = _make_test_df(100)
        engineer = FeatureEngineer()
        result = engineer.build_features(df, windows=[5, 10, 20])

        assert "return_5d" in result.columns
        assert "ma_5" in result.columns
        assert "volatility_5d" in result.columns

    def test_build_features_for_ml(self):
        """测试ML特征构建"""
        df = _make_test_df(100)
        engineer = FeatureEngineer()
        X, y = engineer.build_features_for_ml(df, prediction_horizon=5)

        assert X is not None
        assert y is not None
        assert len(X) == len(y)


class TestHelpers:
    """辅助函数测试"""

    def test_generate_order_id(self):
        """测试订单ID生成"""
        order_id = generate_order_id()
        assert order_id.startswith("ORD_")
        assert len(order_id) > 10

        order_id2 = generate_order_id("TRD")
        assert order_id2.startswith("TRD_")

    def test_format_number(self):
        """测试数字格式化"""
        assert format_number(1234567.89) == "1,234,567.89"
        assert format_number(1001.5, 0) == "1,002"

    def test_format_percent(self):
        """测试百分比格式化"""
        assert format_percent(0.05) == "5.00%"
        assert format_percent(0.1234, 1) == "12.3%"

    def test_calculate_sharpe_ratio(self):
        """测试夏普比率计算"""
        returns = [0.01, -0.005, 0.02, 0.015, -0.01, 0.008, 0.012, -0.003, 0.018, 0.005]
        sharpe = calculate_sharpe_ratio(returns)
        assert isinstance(sharpe, float)

        # 空列表
        assert calculate_sharpe_ratio([]) == 0.0

    def test_calculate_max_drawdown(self):
        """测试最大回撤计算"""
        equity = [100, 105, 110, 108, 95, 100, 115, 120, 110, 105]
        mdd = calculate_max_drawdown(equity)
        assert 0 < mdd < 1

        # 单调递增
        assert calculate_max_drawdown([100, 110, 120, 130]) == 0.0

    def test_calculate_win_rate(self):
        """测试胜率计算"""
        trades = [
            {"pnl": 100},
            {"pnl": -50},
            {"pnl": 200},
            {"pnl": -30},
            {"pnl": 150},
        ]
        wr = calculate_win_rate(trades)
        assert wr == 0.6

    def test_normalize_symbol(self):
        """测试标的代码标准化"""
        assert normalize_symbol("600519.SH") == "600519"
        assert normalize_symbol("000001.SZ") == "000001"
        assert normalize_symbol("600519") == "600519"
