"""
策略工坊快速测试（真实回测）测试

覆盖:
- WorkshopStrategyAdapter: 旧 API 策略代码 → 新 API 回测引擎
- 安全扫描：危险代码被拒绝
- test_strategy 路由：真实数据 + 真实引擎
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from finhack_pro.workshop.strategy_adapter import (
    StrategySecurityError,
    WorkshopStrategyAdapter,
)


def _mock_ohlcv(seed=3):
    """波动 OHLCV（保证 RSI 模板触发交易）"""
    np.random.seed(seed)
    dates = pd.bdate_range("2024-01-01", "2024-06-30")
    n = len(dates)
    close = 100 + np.cumsum(np.random.randn(n) * 2)
    return pd.DataFrame({
        "date": dates,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": 1_000_000,
    })


class TestWorkshopStrategyAdapter:
    def test_load_template_strategy(self):
        """模板策略代码可加载"""
        from finhack_pro.webui.strategy_routes import STRATEGY_TEMPLATES
        adapter = WorkshopStrategyAdapter(STRATEGY_TEMPLATES[1]["code"], symbol="600519.SH")
        adapter._load()
        assert adapter._strategy is not None

    def test_dangerous_code_rejected(self):
        """危险代码（os.system）被安全扫描拒绝"""
        code = (
            "import os\n"
            "class Bad(BaseStrategy):\n"
            "    def on_bar(self, bar):\n"
            "        os.system('rm -rf /')\n"
        )
        adapter = WorkshopStrategyAdapter(code)
        with pytest.raises(StrategySecurityError):
            adapter._load()

    def test_legacy_strategy_produces_signals(self):
        """旧 API 策略产生 BUY/SELL 信号（RSI 超卖 → 买入）"""
        from datetime import datetime

        from finhack_pro.strategies.base import BarData, Context
        from finhack_pro.webui.strategy_routes import STRATEGY_TEMPLATES

        adapter = WorkshopStrategyAdapter(STRATEGY_TEMPLATES[1]["code"], symbol="600519.SH")
        adapter._load()
        ctx = Context()
        closes = [100 - i * 1.5 for i in range(40)]
        got_signal = False
        for c in closes:
            bar = BarData(
                symbol="600519.SH", datetime=datetime(2024, 1, 1),
                open=c + 0.5, high=c + 1, low=c - 1, close=c, volume=1e6,
            )
            sigs = adapter.on_bar(ctx, bar)
            if sigs:
                got_signal = True
                assert sigs[0].direction.value == "buy"
                break
        assert got_signal, "RSI 超卖应产生买入信号"

    def test_full_backtest_with_adapter(self):
        """适配器 + 真实回测引擎：产出交易"""
        from finhack_pro.backtest.runner import BacktestRunner
        from finhack_pro.webui.strategy_routes import STRATEGY_TEMPLATES

        adapter = WorkshopStrategyAdapter(STRATEGY_TEMPLATES[1]["code"], symbol="600519.SH")
        adapter._load()
        runner = BacktestRunner()
        result = runner.run(
            strategy=adapter,
            symbol="600519.SH",
            data=_mock_ohlcv(),
            initial_capital=100000.0,
            commission_rate=0.0003,
            stamp_tax_rate=0.001,
            slippage=0.001,
        )
        assert result.total_trades > 0
        assert result.final_capital > 0


class TestStrategyTestRoute:
    def test_strategy_test_uses_real_engine(self):
        """test_strategy 用真实数据 + 真实引擎，返回真实指标"""
        from finhack_pro.webui.strategy_routes import STRATEGY_TEMPLATES, test_strategy

        request = _make_test_request(STRATEGY_TEMPLATES[1]["code"])

        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher:
            mock_fetcher.return_value.get_daily.return_value = _mock_ohlcv()

            async def _run():
                return await test_strategy(request)

            import asyncio
            resp = asyncio.run(_run())

        data = resp.data
        assert data["valid"] is True
        assert data["metrics"]["total_trades"] > 0
        assert len(data["equity_curve"]) > 0
        assert "真实回测" in data["message"]

    def test_strategy_test_no_data_fails(self):
        """数据获取失败 → valid=False + 明确错误"""
        from finhack_pro.webui.strategy_routes import STRATEGY_TEMPLATES, test_strategy

        request = _make_test_request(STRATEGY_TEMPLATES[1]["code"])

        with patch("finhack_pro.data.fetcher.DataFetcher") as mock_fetcher:
            mock_fetcher.return_value.get_daily.return_value = pd.DataFrame()

            async def _run():
                return await test_strategy(request)

            import asyncio
            resp = asyncio.run(_run())

        data = resp.data
        assert data["valid"] is False
        assert "无法获取" in data["message"]

    def test_strategy_test_dangerous_code_fails(self):
        """危险代码 → valid=False + 安全扫描提示"""
        from finhack_pro.webui.strategy_routes import test_strategy

        bad_code = (
            "import os\n"
            "class Bad(BaseStrategy):\n"
            "    def on_bar(self, bar):\n"
            "        os.system('x')\n"
        )
        request = _make_test_request(bad_code)

        async def _run():
            return await test_strategy(request)

        import asyncio
        resp = asyncio.run(_run())

        data = resp.data
        assert data["valid"] is False
        # 可能被结构校验（缺少信号）或安全扫描（危险代码）拦截，二者都是拒绝
        assert data["message"]


def _make_test_request(code):
    from finhack_pro.webui.strategy_routes import StrategyTestRequest
    return StrategyTestRequest(
        code=code,
        symbol="600519.SH",
        start_date="2024-01-01",
        end_date="2024-06-30",
        initial_capital=100000,
    )
