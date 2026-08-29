"""组合策略（CompositeStrategy）测试

覆盖：多策略信号收集、聚合器+滤波管道输出、子策略异常隔离、空策略校验、
与 runner 的集成（多策略回测跑通）。
"""

import pytest

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.strategies.base import BarData, BaseStrategy, Signal, SignalDirection
from finhack_pro.strategies.composite_strategy import CompositeStrategy
from finhack_pro.strategies.signal_aggregator import SignalAggregator


class _FakeSubStrategy(BaseStrategy):
    """可编程子策略：on_bar 返回预设信号或抛异常"""

    def __init__(self, name: str, signals_fn=None, raise_on_bar: bool = False):
        super().__init__()
        self._name = name
        self._signals_fn = signals_fn or (lambda bar: [])
        self._raise_on_bar = raise_on_bar
        self.on_init_called = False

    def on_init(self, context) -> None:
        self.on_init_called = True

    def on_bar(self, context, bar):
        if self._raise_on_bar:
            raise RuntimeError("boom")
        return self._signals_fn(bar)


def _bar(close=10.0):
    from datetime import datetime
    return BarData(
        symbol="600519", datetime=datetime(2026, 1, 5),
        open=9.9, high=10.2, low=9.8, close=close, volume=10000,
    )


def _buy_signal(symbol="600519"):
    return Signal(symbol=symbol, direction=SignalDirection.BUY, price=10.0, strategy_name="fake")


class TestCompositeStrategy:
    def test_requires_at_least_one_strategy(self):
        with pytest.raises(ValueError):
            CompositeStrategy(strategies=[])

    def test_empty_signals_return_empty(self):
        sub = _FakeSubStrategy("quiet")
        comp = CompositeStrategy(strategies=[sub])
        assert comp.on_bar(None, _bar()) == []

    def test_aggregates_two_buy_signals_into_buy(self):
        """两子策略同向 BUY → 聚合输出 BUY（加权投票不抵消）"""
        sub1 = _FakeSubStrategy("mom", signals_fn=lambda bar: [_buy_signal()])
        sub2 = _FakeSubStrategy("rev", signals_fn=lambda bar: [_buy_signal()])
        comp = CompositeStrategy(strategies=[sub1, sub2])

        outs = comp.on_bar(None, _bar())
        assert len(outs) == 1
        assert outs[0].direction == SignalDirection.BUY
        assert outs[0].symbol == "600519"
        assert "aggregated_confidence" in outs[0].extra

    def test_sub_strategy_exception_does_not_block(self):
        """子策略抛异常 → 日志告警，其余策略信号仍正常聚合"""
        bad = _FakeSubStrategy("bad", raise_on_bar=True)
        good = _FakeSubStrategy("good", signals_fn=lambda bar: [_buy_signal()])
        comp = CompositeStrategy(strategies=[bad, good])

        outs = comp.on_bar(None, _bar())
        assert len(outs) == 1
        assert outs[0].direction == SignalDirection.BUY

    def test_on_init_fans_out_to_subs(self):
        sub = _FakeSubStrategy("a")
        comp = CompositeStrategy(strategies=[sub])
        comp.on_init(None)
        assert sub.on_init_called is True

    def test_aggregator_default_pipeline_built(self):
        """未显式传 aggregator → 内部构建默认滤波管道（4 个默认滤波器）"""
        comp = CompositeStrategy(strategies=[_FakeSubStrategy("a")])
        pipeline = comp.aggregator._filter_pipeline
        assert pipeline is not None
        assert len(pipeline.filters) >= 4


class TestCompositeRunnerIntegration:
    def test_multi_strategy_backtest_runs(self):
        """多策略组合走真实 runner：跑通 + 产出权益曲线"""
        import pandas as pd

        dates = pd.date_range("2026-01-01", periods=60, freq="B")
        data = pd.DataFrame({
            "date": dates,
            "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
            "volume": 100000,
        })
        data["close"] = [10.0 + i * 0.05 for i in range(len(data))]

        sub1 = _FakeSubStrategy("buyer", signals_fn=lambda bar: [_buy_signal()])
        sub2 = _FakeSubStrategy("holder", signals_fn=lambda bar: [])
        comp = CompositeStrategy(strategies=[sub1, sub2])

        runner = BacktestRunner()
        result = runner.run(
            strategy=comp,
            symbol="600519",
            data=data,
            initial_capital=1_000_000,
            commission_rate=0.0003,
            stamp_tax_rate=0.001,
            slippage=0.001,
        )
        assert result.equity_curve, "多策略回测应产出权益曲线"
        assert len(result.equity_curve) == len(data)
