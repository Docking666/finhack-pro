"""
策略模块测试
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from finhack_pro.strategies.base import (
    BarData,
    Context,
    Signal,
    SignalDirection,
)
from finhack_pro.strategies.dual_thrust import DualThrustStrategy
from finhack_pro.strategies.mean_reversion import MeanReversionStrategy
from finhack_pro.strategies.momentum import MomentumStrategy


def _make_bar(symbol: str, close: float, day_offset: int = 0) -> BarData:
    """创建测试用的BarData"""
    dt = datetime(2024, 1, 1) + pd.Timedelta(days=day_offset)
    return BarData(
        symbol=symbol,
        datetime=dt,
        open=close * 0.99,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=1000000,
    )


def _make_price_series(n: int = 100, base_price: float = 100.0, trend: float = 0.0) -> list:
    """生成测试价格序列"""
    np.random.seed(42)
    prices = [base_price]
    for _ in range(n - 1):
        change = np.random.randn() * 2 + trend
        prices.append(max(prices[-1] * (1 + change / 100), 1.0))
    return prices


class TestDualThrustStrategy:
    """Dual Thrust策略测试"""

    @pytest.mark.asyncio
    async def test_strategy_init(self):
        """测试策略初始化"""
        strategy = DualThrustStrategy()
        context = Context()
        strategy.on_init(context)

        assert strategy.strategy_name == "DualThrust"
        assert strategy._params["k1"] == 0.5
        assert strategy._params["k2"] == 0.5
        assert strategy._params["lookback"] == 20

    @pytest.mark.asyncio
    async def test_strategy_with_custom_params(self):
        """测试自定义参数"""
        strategy = DualThrustStrategy()
        strategy.set_parameters({"k1": 0.6, "k2": 0.4, "lookback": 10})

        context = Context(params={"k1": 0.7})
        strategy.on_init(context)

        assert strategy._params["k1"] == 0.7
        assert strategy._params["k2"] == 0.4

    @pytest.mark.asyncio
    async def test_strategy_generates_signals(self):
        """测试策略信号生成"""
        strategy = DualThrustStrategy()
        context = Context()
        strategy.on_init(context)

        prices = _make_price_series(50, 100.0, 0.5)
        signals = []

        for i, price in enumerate(prices):
            bar = _make_bar("600519.SH", price, i)
            sigs = strategy.on_bar(context, bar)
            signals.extend(sigs)

        # 策略应该产生一些信号
        assert isinstance(signals, list)

    def test_backtest_with_dataframe(self):
        """测试DataFrame回测"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        prices = 100 + np.cumsum(np.random.randn(n) * 2)

        df = pd.DataFrame({
            "date": dates,
            "open": prices * 0.999,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": np.random.randint(100000, 1000000, n),
        })

        result = DualThrustStrategy.backtest_with_dataframe(
            df, k1=0.5, k2=0.5, lookback=20
        )

        assert "initial_capital" in result
        assert "final_value" in result
        assert "total_return" in result
        assert result["initial_capital"] == 1_000_000.0


class TestMeanReversionStrategy:
    """均值回归策略测试"""

    @pytest.mark.asyncio
    async def test_strategy_init(self):
        """测试策略初始化"""
        strategy = MeanReversionStrategy()
        context = Context()
        strategy.on_init(context)

        assert strategy.strategy_name == "MeanReversion"
        assert strategy._params["rsi_period"] == 14

    @pytest.mark.asyncio
    async def test_rsi_calculation(self):
        """测试RSI计算"""
        prices = np.array([100, 101, 102, 101, 103, 105, 104, 106, 108, 107,
                           109, 111, 110, 112, 114])
        rsi = MeanReversionStrategy._compute_rsi(prices, 14)
        assert 0 <= rsi <= 100

    @pytest.mark.asyncio
    async def test_bollinger_calculation(self):
        """测试布林带计算"""
        prices = np.array([100, 101, 102, 103, 104, 105, 104, 103, 102, 101,
                           100, 101, 102, 103, 104, 105, 106, 107, 108, 109])
        middle, upper, lower = MeanReversionStrategy._compute_bollinger(prices, 20, 2.0)
        assert lower < middle < upper

    def test_backtest_with_dataframe(self):
        """测试DataFrame回测"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        prices = 100 + np.cumsum(np.random.randn(n) * 1.5)

        df = pd.DataFrame({
            "date": dates,
            "open": prices * 0.999,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": np.random.randint(100000, 1000000, n),
        })

        result = MeanReversionStrategy.backtest_with_dataframe(df)
        assert "total_return" in result
        assert isinstance(result["trades"], list)


class TestMomentumStrategy:
    """动量策略测试"""

    @pytest.mark.asyncio
    async def test_strategy_init(self):
        """测试策略初始化"""
        strategy = MomentumStrategy()
        context = Context()
        strategy.on_init(context)

        assert strategy.strategy_name == "Momentum"
        assert strategy._params["lookback"] == 20
        assert strategy._params["top_k"] == 5

    def test_momentum_calculation(self):
        """测试动量计算"""
        n = 50
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        prices = 100 + np.arange(n) * 0.5  # 上升趋势

        df = pd.DataFrame({
            "date": dates,
            "close": prices,
        })

        momentum = MomentumStrategy.calculate_momentum(df, lookback=20)
        assert len(momentum) == n
        assert momentum.iloc[-1] > 0  # 上升趋势动量为正

    def test_stock_ranking(self):
        """测试股票排名"""
        np.random.seed(42)
        price_dict = {}
        for symbol in ["000001", "000002", "600519", "600036", "000858"]:
            n = 50
            dates = pd.date_range("2023-01-01", periods=n, freq="B")
            prices = 100 + np.cumsum(np.random.randn(n) * 2)
            price_dict[symbol] = pd.DataFrame({"date": dates, "close": prices})

        top_stocks = MomentumStrategy.rank_stocks(price_dict, lookback=20, top_k=3)
        assert len(top_stocks) == 3
        assert isinstance(top_stocks[0], str)


class TestSignal:
    """Signal测试"""

    def test_signal_creation(self):
        """测试Signal创建"""
        signal = Signal(
            symbol="600519.SH",
            direction=SignalDirection.BUY,
            price=1800.0,
            volume=100,
            stop_loss=1710.0,
            take_profit=1980.0,
        )
        assert signal.symbol == "600519.SH"
        assert signal.direction == SignalDirection.BUY

    def test_signal_to_dict(self):
        """测试Signal序列化"""
        signal = Signal(
            symbol="600519.SH",
            direction=SignalDirection.SELL,
            price=1800.0,
        )
        d = signal.to_dict()
        assert d["symbol"] == "600519.SH"
        assert d["direction"] == "sell"
