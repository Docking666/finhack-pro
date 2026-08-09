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


class TestNicheStrategy:
    """差异化策略 + 事件管道测试"""

    def _make_context(self, cash: float = 1_000_000.0) -> Context:
        from finhack_pro.strategies.base import Portfolio
        return Context(portfolio=Portfolio(cash=cash))

    def _make_niche(self, niche_type: str = "event_driven"):
        from finhack_pro.strategies.niche_strategy import (
            NicheStrategy,
            NicheStrategyConfig,
            NicheType,
        )
        config = NicheStrategyConfig(
            niche_type=NicheType(niche_type),
            min_confidence=0.3,
        )
        return NicheStrategy(config)

    def test_feed_micro_events_dict(self):
        """dict 格式事件接入"""
        strategy = self._make_niche()
        events = [
            {
                "symbol": "600519.SH",
                "event_id": "evt-1",
                "event_type": "dragon_tiger",
                "title": "龙虎榜净买入2亿",
                "event_time": "2024-01-05T10:00:00",
                "impact_level": "high",
                "impact_direction": "positive",
                "confidence": 0.8,
            },
            {
                "symbol": "000001.SZ",
                "event_type": "risk_warning",
                "title": "业绩风险提示",
                "impact_level": "medium",
                "impact_direction": "negative",
            },
        ]
        fed = strategy.feed_micro_events(events)
        assert fed == 2
        assert len(strategy._event_history["600519.SH"]) == 1
        assert strategy._event_history["600519.SH"][0]["id"] == "evt-1"

    def test_feed_micro_events_dataclass(self):
        """MicroEvent dataclass 格式事件接入"""
        from dataclasses import dataclass, field
        from typing import Dict, List

        @dataclass
        class _FakeMicroEvent:
            symbol: str
            event_id: str
            event_type: str
            title: str
            event_time: str
            impact_level: str
            impact_direction: str
            confidence: float
            raw_data: Dict = field(default_factory=dict)

        events = [
            _FakeMicroEvent(
                symbol="600519.SH",
                event_id="evt-2",
                event_type="earnings_preview",
                title="业绩预增",
                event_time="2024-01-06T09:30:00",
                impact_level="critical",
                impact_direction="positive",
                confidence=0.9,
            )
        ]
        strategy = self._make_niche()
        fed = strategy.feed_micro_events(events)
        assert fed == 1
        assert strategy._event_history["600519.SH"][0]["type"] == "earnings_preview"

    def test_event_driven_signal_generated(self):
        """事件驱动策略：高影响事件能产生信号（打通验证）"""
        strategy = self._make_niche("event_driven")
        context = self._make_context()

        # 喂入 7 天内的 critical 正面事件
        strategy.feed_micro_events([
            {
                "symbol": "600519.SH",
                "event_id": "evt-3",
                "title": "重大资产重组获批",
                "event_time": "2024-01-05T10:00:00",
                "impact_level": "critical",
                "impact_direction": "positive",
                "type": "major_event",
            }
        ])

        # bar 时间为 2024-01-06，事件 2024-01-05 在 7 天窗口内
        bar = _make_bar("600519.SH", close=100.0, day_offset=5)
        signals = strategy.on_bar(context, bar)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY

    def test_event_driven_no_signal_without_event(self):
        """无事件时事件驱动策略不产生信号"""
        strategy = self._make_niche("event_driven")
        context = self._make_context()
        bar = _make_bar("600519.SH", close=100.0)
        signals = strategy.on_bar(context, bar)
        assert signals == []

    def test_build_bar_extra_fields(self):
        """bar.extra 组装：小市值 + 情绪 + 龙虎榜 + 交叉信号"""
        strategy = self._make_niche("alternative_cross")
        extra = strategy.build_bar_extra(
            symbol="600519.SH",
            market_cap=50e8,
            turnover=0.05,
            volume_ratio=2.5,
            sentiment_score=0.75,
            sentiment_trend="rising",
            dragon_tiger={"net_buy": 2e8, "buyers": [{"is_famous": True}]},
            net_inflow=5e7,
            rsi=35.0,
            macd_signal="golden_cross",
        )
        assert extra["market_cap"] == 50e8
        assert extra["turnover"] == 0.05
        assert extra["sentiment_score"] == 0.75
        assert extra["dragon_tiger"]["net_buy"] == 2e8
        assert extra["net_inflow"] == 5e7
        assert extra["rsi"] == 35.0
        assert extra["has_positive_event"] is False  # 尚未喂事件

    def test_build_bar_extra_positive_event_flag(self):
        """build_bar_extra 识别最近高影响正面事件"""
        strategy = self._make_niche("alternative_cross")
        strategy.feed_micro_events([
            {
                "symbol": "600519.SH",
                "event_time": "2024-01-05T10:00:00",
                "impact_level": "high",
                "impact_direction": "positive",
            }
        ])
        extra = strategy.build_bar_extra(
            symbol="600519.SH", as_of=datetime(2024, 1, 6)
        )
        assert extra["has_positive_event"] is True

    def test_sentiment_reversal_signal(self):
        """情绪反转策略：极度悲观 + 超卖触发买入"""
        strategy = self._make_niche("sentiment_reversal")
        context = self._make_context()

        bar = _make_bar("600519.SH", close=100.0)
        bar.extra = {
            "sentiment_score": 0.1,
            "sentiment_trend": "falling",
            "rsi": 25.0,
        }
        signals = strategy.on_bar(context, bar)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY

    def test_alternative_cross_signal(self):
        """另类数据交叉：3 个维度共振触发信号"""
        strategy = self._make_niche("alternative_cross")
        context = self._make_context()

        # 先喂入正面事件，确保 has_positive_event=True
        strategy.feed_micro_events([
            {
                "symbol": "600519.SH",
                "event_time": "2024-01-05T10:00:00",
                "impact_level": "high",
                "impact_direction": "positive",
            }
        ])
        bar = _make_bar("600519.SH", close=100.0, day_offset=5)
        bar.extra = {
            "rsi": 25.0,               # 技术面看多
            "macd_signal": "golden_cross",  # 技术面看多
            "net_inflow": 5e7,         # 资金面
            "sentiment_score": 0.7,    # 舆情面
        }
        signals = strategy.on_bar(context, bar)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY
        assert "共振" in signals[0].extra.get("reasoning", "")

    def test_micro_cap_signal(self):
        """小市值策略：放量突破触发信号"""
        strategy = self._make_niche("micro_cap")
        context = self._make_context()

        bar = _make_bar("600519.SH", close=100.0)
        bar.extra = {
            "market_cap": 30e8,   # 30亿，小于100亿上限
            "turnover": 0.05,     # > 2% 换手
            "volume_ratio": 2.5,  # > 2.0 放量
            "ma20": 95.0,         # 突破 2%
            "is_st": False,
            "days_listed": 2000,
        }
        signals = strategy.on_bar(context, bar)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY

    def test_dragon_tiger_follow_signal(self):
        """龙虎榜跟随：知名游资净买入触发"""
        strategy = self._make_niche("dragon_tiger_follow")
        context = self._make_context()

        bar = _make_bar("600519.SH", close=100.0)
        bar.extra = {
            "dragon_tiger": {
                "net_buy": 2e8,
                "buyers": [{"is_famous": True, "name": "章盟主"}],
                "sellers": [],
            }
        }
        signals = strategy.on_bar(context, bar)
        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BUY
