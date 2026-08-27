"""策略工坊闭环测试：WorkshopStrategyAdapter 新旧 API + 安全扫描 + load_strategy 收敛

覆盖：旧 API（self.buy/sell）桥接、新 API（on_bar 返回 Signal 列表）、
危险代码拒绝、runner.load_strategy custom 分支走 adapter 跑通回测。
"""

import pandas as pd
import pytest

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.workshop.strategy_adapter import (
    StrategySecurityError,
    WorkshopStrategyAdapter,
)

LEGACY_CODE = '''
class MyStrategy(BaseStrategy):
    def on_bar(self, bar):
        if len(self.bars) > 5 and not self.position:
            self.buy(bar.close, size=100)
        elif self.position and bar.close > 11.0:
            self.sell(bar.close)
'''

NEW_API_CODE = '''
class MyStrategy(BaseStrategy):
    def on_bar(self, context, bar):
        if bar.close > 10.5:
            return [Signal(symbol=bar.symbol, direction=SignalDirection.BUY, price=bar.close)]
        return []
'''

DANGEROUS_CODE = '''
import os
class MyStrategy(BaseStrategy):
    def on_bar(self, bar):
        os.system("rm -rf /")
        self.buy(bar.close)
'''


def _data():
    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    return pd.DataFrame({
        "date": dates, "open": 10.0, "high": 11.0, "low": 9.5,
        "close": [10.0 + i * 0.02 for i in range(40)], "volume": 100000,
    })


class TestWorkshopStrategyAdapter:
    def test_legacy_api_bridge(self):
        """旧 API（单参 on_bar + self.buy/sell）桥接为新 API 信号"""
        adapter = WorkshopStrategyAdapter(LEGACY_CODE, symbol="600519")
        assert adapter._new_api is False
        # 构造简单 context 桩
        class _Ctx:
            portfolio = type("P", (), {"cash": 100000})()

        result = BacktestRunner().run(
            strategy=adapter, symbol="600519", data=_data(),
            initial_capital=100000, commission_rate=0.0003,
            stamp_tax_rate=0.001, slippage=0.001,
        )
        assert result.equity_curve

    def test_new_api_signals(self):
        """新 API（双参 on_bar 返回 Signal 列表）直接透传"""
        adapter = WorkshopStrategyAdapter(NEW_API_CODE, symbol="600519")
        adapter._load()  # 触发代码加载与 API 形态检测
        assert adapter._new_api is True
        result = BacktestRunner().run(
            strategy=adapter, symbol="600519", data=_data(),
            initial_capital=100000, commission_rate=0.0003,
            stamp_tax_rate=0.001, slippage=0.001,
        )
        assert result.equity_curve

    def test_dangerous_code_rejected(self):
        """含 os.system 的危险代码在 exec 前被 AST 安全扫描拒绝"""
        adapter = WorkshopStrategyAdapter(DANGEROUS_CODE)
        with pytest.raises(StrategySecurityError):
            adapter._load()


class TestLoadStrategyConvergence:
    def test_custom_strategy_via_adapter(self, tmp_path, monkeypatch):
        """load_strategy custom 分支收敛到 WorkshopStrategyAdapter（不再裸 exec）"""
        import finhack_pro.backtest.runner as runner_mod
        # 指向临时生成目录，避免污染真实 data/generated_strategies
        monkeypatch.setattr(runner_mod, "Path", lambda p: (tmp_path / p) if p == "data/generated_strategies" else __import__("pathlib").Path(p))
        gen_dir = tmp_path / "data" / "generated_strategies" / "gen_test"
        gen_dir.mkdir(parents=True)
        (gen_dir / "strategy.py").write_text(LEGACY_CODE, encoding="utf-8")

        strategy = BacktestRunner.load_strategy("gen_test")
        assert isinstance(strategy, WorkshopStrategyAdapter)
        result = BacktestRunner().run(
            strategy=strategy, symbol="600519", data=_data(),
            initial_capital=100000, commission_rate=0.0003,
            stamp_tax_rate=0.001, slippage=0.001,
        )
        assert result.equity_curve
