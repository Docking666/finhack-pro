"""
组合回测与风险控制模块测试

Tests for portfolio, report, and risk_control modules.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from finhack_pro.backtest.portfolio import (
    AllocationMethod,
    IndividualResult,
    PortfolioAllocation,
    PortfolioBacktestConfig,
    PortfolioBacktestResult,
    PortfolioEngine,
    PortfolioMetrics,
    PortfolioRebalanceResult,
    RebalanceFreq,
)
from finhack_pro.backtest.report import BacktestReport, ReportConfig
from finhack_pro.backtest.risk_control import (
    ActionType,
    PortfolioRiskState,
    Position,
    RiskAction,
    RiskCheckResult,
    RiskConfig,
    RiskController,
    RiskWarning,
    UrgencyLevel,
    WarningSeverity,
    WarningType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_data(
    n_days: int = 252,
    start_price: float = 100.0,
    annual_return: float = 0.10,
    annual_vol: float = 0.20,
    seed: int = 42,
) -> pd.DataFrame:
    """生成模拟价格数据"""
    np.random.seed(seed)
    dates = pd.date_range(end="2024-12-31", periods=n_days, freq="B")
    daily_ret = (annual_return / 252) + (annual_vol / np.sqrt(252)) * np.random.randn(n_days)
    prices = start_price * np.cumprod(1 + daily_ret)
    return pd.DataFrame({
        'date': dates,
        'open': prices * (1 + np.random.uniform(-0.005, 0.005, n_days)),
        'high': prices * (1 + np.abs(np.random.uniform(0, 0.02, n_days))),
        'low': prices * (1 - np.abs(np.random.uniform(0, 0.02, n_days))),
        'close': prices,
        'volume': np.random.uniform(1e6, 5e6, n_days),
    })


def _make_multi_data(
    symbols: list = None,
    n_days: int = 252,
) -> dict:
    """生成多标的数据"""
    if symbols is None:
        symbols = ["AAPL", "MSFT", "GOOG"]
    seeds = [42, 43, 44]
    data = {}
    for i, symbol in enumerate(symbols):
        data[symbol] = _make_price_data(
            n_days=n_days,
            start_price=100.0 + i * 50,
            seed=seeds[i % len(seeds)],
        )
    return data


# ===========================================================================
# TestPortfolioEngine
# ===========================================================================

class TestPortfolioEngine:
    """组合回测引擎测试"""

    def test_equal_weight_allocation(self):
        """测试等权重分配"""
        config = PortfolioBacktestConfig(
            symbols=["AAPL", "MSFT", "GOOG"],
            allocation_method="equal",
            initial_capital=1_000_000,
        )
        engine = PortfolioEngine(config)
        result = engine._equal_weight_allocation(config.symbols, 1_000_000)

        assert len(result) == 3
        for symbol, value in result.items():
            assert abs(value - 1_000_000 / 3) < 0.01

    def test_risk_parity_allocation(self):
        """测试风险平价分配"""
        config = PortfolioBacktestConfig(
            symbols=["AAPL", "MSFT", "GOOG"],
            allocation_method="risk_parity",
            initial_capital=1_000_000,
        )
        engine = PortfolioEngine(config)

        # 创建不同波动率的收益率数据
        returns = {
            "AAPL": pd.Series(np.random.normal(0.001, 0.02, 100)),
            "MSFT": pd.Series(np.random.normal(0.001, 0.03, 100)),
            "GOOG": pd.Series(np.random.normal(0.001, 0.01, 100)),
        }
        result = engine._risk_parity_allocation(returns, 1_000_000)

        assert len(result) == 3
        # 低波动标的应分配更多
        assert result["GOOG"] > result["MSFT"]  # GOOG vol=0.01 < MSFT vol=0.03
        # 总分配应接近总资金
        total = sum(result.values())
        assert abs(total - 1_000_000) < 1.0

    def test_rebalance(self):
        """测试再平衡逻辑"""
        config = PortfolioBacktestConfig(
            symbols=["AAPL", "MSFT"],
            allocation_method="equal",
            initial_capital=1_000_000,
            commission_rate=0.0,
            slippage=0.0,
        )
        engine = PortfolioEngine(config)

        current_values = {"AAPL": 600_000, "MSFT": 400_000}
        target_values = {"AAPL": 500_000, "MSFT": 500_000}
        prices = {"AAPL": 150.0, "MSFT": 250.0}

        result = engine._rebalance(
            current_values, target_values, prices,
            cash=0, commission_rate=0.0, slippage=0.0,
        )

        # 应该有卖出 AAPL 和买入 MSFT 的交易
        assert len(result['trades']) >= 2
        # 换手率应大于 0
        assert result['turnover'] > 0

    def test_full_run(self):
        """测试完整组合回测"""
        config = PortfolioBacktestConfig(
            symbols=["AAPL", "MSFT", "GOOG"],
            initial_capital=1_000_000,
            rebalance_freq="monthly",
            allocation_method="equal",
            commission_rate=0.0003,
            slippage=0.001,
        )
        engine = PortfolioEngine(config)
        data = _make_multi_data()

        result = engine.run(data)

        assert isinstance(result, PortfolioBacktestResult)
        assert result.metrics is not None
        assert isinstance(result.metrics, PortfolioMetrics)
        assert not result.equity_curve.empty
        assert len(result.trades) > 0
        assert len(result.rebalance_history) > 0
        assert result.correlation_matrix is not None
        assert result.config is config

    def test_correlation_analysis(self):
        """测试相关性分析"""
        returns = {
            "A": pd.Series(np.random.normal(0.001, 0.02, 100)),
            "B": pd.Series(np.random.normal(0.001, 0.02, 100)),
        }
        # 创建高相关序列
        returns["C"] = returns["A"] * 0.9 + pd.Series(np.random.normal(0, 0.005, 100))

        result = PortfolioEngine.analyze_correlation(returns, threshold=0.7)

        assert 'correlation_matrix' in result
        assert 'high_corr_pairs' in result
        assert isinstance(result['correlation_matrix'], pd.DataFrame)
        assert isinstance(result['high_corr_pairs'], list)

    def test_custom_weights(self):
        """测试自定义权重分配"""
        config = PortfolioBacktestConfig(
            symbols=["AAPL", "MSFT", "GOOG"],
            allocation_method="custom",
            custom_weights={"AAPL": 0.5, "MSFT": 0.3, "GOOG": 0.2},
            initial_capital=1_000_000,
        )
        engine = PortfolioEngine(config)
        data = _make_multi_data()
        result = engine.run(data)

        assert result.metrics is not None
        assert not result.equity_curve.empty

    def test_invalid_config(self):
        """测试无效配置"""
        # 空标的列表 - validation happens in PortfolioEngine.__init__
        with pytest.raises(ValueError, match="symbols"):
            PortfolioEngine(PortfolioBacktestConfig(symbols=[]))

        # 自定义权重总和不为 1
        with pytest.raises(ValueError, match="自定义权重总和"):
            PortfolioEngine(PortfolioBacktestConfig(
                symbols=["AAPL"],
                allocation_method="custom",
                custom_weights={"AAPL": 0.5},
            ))

    def test_adjustment_factors(self):
        """测试调整因子（公司行为）"""
        config = PortfolioBacktestConfig(
            symbols=["AAPL"],
            initial_capital=1_000_000,
            rebalance_freq="monthly",
            allocation_method="equal",
        )
        engine = PortfolioEngine(config)
        data = {"AAPL": _make_price_data(n_days=60)}

        # 创建调整因子
        dates = data["AAPL"]["date"]
        adj_factor = pd.Series(1.0, index=pd.to_datetime(dates))
        # 在某一天模拟拆股 (2:1)
        adj_factor.iloc[30] = 2.0

        result = engine.run(data, adjustment_factors={"AAPL": adj_factor})
        assert result.metrics is not None

    def test_weekly_rebalance(self):
        """测试周度再平衡"""
        config = PortfolioBacktestConfig(
            symbols=["AAPL", "MSFT"],
            initial_capital=1_000_000,
            rebalance_freq="weekly",
            allocation_method="equal",
        )
        engine = PortfolioEngine(config)
        data = _make_multi_data(symbols=["AAPL", "MSFT"], n_days=60)
        result = engine.run(data)

        assert result.metrics is not None
        # 周度再平衡应产生更多再平衡事件
        assert len(result.rebalance_history) > 0


# ===========================================================================
# TestBacktestReport
# ===========================================================================

class TestBacktestReport:
    """回测报告测试"""

    def _make_sample_result(self) -> PortfolioBacktestResult:
        """创建示例回测结果"""
        dates = pd.date_range("2024-01-01", periods=252, freq="B")
        equity = 1_000_000 * np.cumprod(1 + np.random.normal(0.0005, 0.01, 252))

        equity_df = pd.DataFrame({
            'date': dates,
            'equity': equity,
            'cash': equity * 0.1,
            'position_value': equity * 0.9,
        })
        equity_df.set_index('date', inplace=True)

        trades = [
            {
                'date': '2024-01-15',
                'symbol': 'AAPL',
                'side': 'buy',
                'shares': 100,
                'price': 150.0,
                'value': 15000.0,
                'commission': 4.5,
            },
            {
                'date': '2024-02-15',
                'symbol': 'AAPL',
                'side': 'sell',
                'shares': 100,
                'price': 160.0,
                'value': 16000.0,
                'commission': 4.8,
            },
        ]

        metrics = PortfolioMetrics(
            total_return=0.15,
            annual_return=0.15,
            sharpe_ratio=1.5,
            max_drawdown=0.08,
            calmar_ratio=1.875,
            sortino_ratio=2.0,
            volatility=0.12,
            win_rate=0.6,
            profit_loss_ratio=1.8,
            total_trades=10,
            turnover=0.5,
        )

        individual = [
            IndividualResult(
                symbol="AAPL", total_return=0.18, annual_return=0.18,
                volatility=0.15, sharpe_ratio=1.6, max_drawdown=0.06,
            ),
            IndividualResult(
                symbol="MSFT", total_return=0.12, annual_return=0.12,
                volatility=0.10, sharpe_ratio=1.3, max_drawdown=0.09,
            ),
        ]

        corr = pd.DataFrame({
            "AAPL": [1.0, 0.6],
            "MSFT": [0.6, 1.0],
        }, index=["AAPL", "MSFT"])

        return PortfolioBacktestResult(
            equity_curve=equity_df,
            trades=trades,
            metrics=metrics,
            individual_results=individual,
            rebalance_history=[{'date': '2024-01-02', 'trades': 3, 'turnover': 0.8}],
            correlation_matrix=corr,
        )

    def test_summary_generation(self):
        """测试摘要生成"""
        result = self._make_sample_result()
        report = BacktestReport(result)
        summary = report.generate_summary()

        assert 'title' in summary
        assert 'metrics' in summary
        assert 'total_trades' in summary
        assert summary['total_trades'] == 2
        assert 'total_return' in summary

    def test_html_report_creation(self):
        """测试 HTML 报告生成"""
        result = self._make_sample_result()
        with tempfile.TemporaryDirectory() as tmpdir:
            report = BacktestReport(result, output_dir=tmpdir)
            html_path = report.generate_html_report()

            assert os.path.exists(html_path)
            with open(html_path, 'r', encoding='utf-8') as f:
                content = f.read()

            assert '<!DOCTYPE html>' in content
            assert 'Equity Curve' in content
            assert 'Drawdown' in content
            assert 'Monthly Returns' in content
            assert 'Key Metrics' in content
            assert 'Sharpe Ratio' in content
            assert 'AAPL' in content

    def test_dark_theme(self):
        """测试暗色主题"""
        result = self._make_sample_result()
        config = ReportConfig(theme="dark", title="Dark Report")
        report = BacktestReport(result, config=config)

        with tempfile.TemporaryDirectory() as tmpdir:
            report.output_dir = tmpdir
            html_path = report.generate_html_report()

            with open(html_path, 'r', encoding='utf-8') as f:
                content = f.read()

            assert '#1a1a2e' in content  # dark bg color

    def test_light_theme(self):
        """测试亮色主题"""
        result = self._make_sample_result()
        config = ReportConfig(theme="light", title="Light Report")
        report = BacktestReport(result, config=config)

        with tempfile.TemporaryDirectory() as tmpdir:
            report.output_dir = tmpdir
            html_path = report.generate_html_report()

            with open(html_path, 'r', encoding='utf-8') as f:
                content = f.read()

            assert '#f5f5f5' in content  # light bg color

    def test_metrics_table(self):
        """测试指标表格渲染"""
        result = self._make_sample_result()
        report = BacktestReport(result)
        html = report._render_metrics_table()

        assert 'Total Return' in html
        assert 'Sharpe Ratio' in html
        assert 'Max Drawdown' in html
        assert '15.00%' in html  # total_return

    def test_dict_result(self):
        """测试字典类型结果"""
        result_dict = {
            'metrics': {
                'total_return': 0.10,
                'sharpe_ratio': 1.2,
                'max_drawdown': 0.05,
            },
            'trades': [
                {'date': '2024-01-01', 'symbol': 'AAPL', 'side': 'buy',
                 'shares': 100, 'price': 150.0, 'value': 15000.0},
            ],
            'equity_curve': pd.DataFrame({
                'date': pd.date_range('2024-01-01', periods=10),
                'equity': np.linspace(1_000_000, 1_100_000, 10),
            }),
        }
        report = BacktestReport(result_dict)
        summary = report.generate_summary()

        assert summary['metrics']['total_return'] == 0.10
        assert summary['total_trades'] == 1

    def test_empty_result(self):
        """测试空结果"""
        report = BacktestReport(PortfolioBacktestResult())
        summary = report.generate_summary()
        assert summary['total_trades'] == 0

    def test_trade_list_rendering(self):
        """测试交易列表渲染"""
        result = self._make_sample_result()
        report = BacktestReport(result)
        html = report._render_trade_list()

        assert 'AAPL' in html
        assert 'BUY' in html
        assert 'SELL' in html

    def test_export_to_pdf_no_weasyprint(self):
        """测试 PDF 导出（无 weasyprint）"""
        result = self._make_sample_result()
        report = BacktestReport(result)
        pdf_path = report.export_to_pdf()

        # weasyprint 可能不可用，应返回空字符串而不报错
        assert isinstance(pdf_path, str)


# ===========================================================================
# TestRiskController
# ===========================================================================

class TestRiskController:
    """风险控制器测试"""

    def _make_portfolio_state(
        self,
        total_equity: float = 1_000_000,
        position_value: float = 800_000,
        cash: float = 200_000,
        daily_pnl: float = 0,
        drawdown: float = 0.0,
    ) -> PortfolioRiskState:
        """创建示例组合状态"""
        positions = [
            Position(
                symbol="AAPL", quantity=1000, avg_price=150.0,
                current_price=160.0, pnl=10000, pnl_pct=0.0667,
                weight=0.4,
            ),
            Position(
                symbol="MSFT", quantity=500, avg_price=250.0,
                current_price=240.0, pnl=-5000, pnl_pct=-0.04,
                weight=0.3,
            ),
        ]
        return PortfolioRiskState(
            positions=positions,
            total_equity=total_equity,
            total_position_value=position_value,
            cash=cash,
            daily_pnl=daily_pnl,
            unrealized_pnl=5000,
            drawdown=drawdown,
            peak_equity=total_equity / (1 - drawdown) if drawdown > 0 else total_equity,
        )

    def test_pre_trade_check_pass(self):
        """测试交易前检查通过"""
        config = RiskConfig()
        controller = RiskController(config)
        state = self._make_portfolio_state()

        result = controller.pre_trade_check(
            symbol="GOOG", direction="buy", price=100.0, volume=100,
            portfolio_state=state,
        )

        assert result.passed is True
        assert len(result.violations) == 0

    def test_pre_trade_check_position_limit(self):
        """测试单标的仓位超限"""
        config = RiskConfig(max_position_pct=0.3)
        controller = RiskController(config)
        state = self._make_portfolio_state()

        # AAPL 已占 40%，再买入会超限
        result = controller.pre_trade_check(
            symbol="AAPL", direction="buy", price=160.0, volume=1000,
            portfolio_state=state,
        )

        assert result.passed is False
        assert len(result.violations) > 0
        assert any("exceeds" in v.reason for v in result.violations)

    def test_pre_trade_check_total_position_limit(self):
        """测试总仓位超限"""
        config = RiskConfig(max_total_position_pct=0.85)
        controller = RiskController(config)
        state = self._make_portfolio_state(position_value=820_000)

        result = controller.pre_trade_check(
            symbol="GOOG", direction="buy", price=100.0, volume=10000,
            portfolio_state=state,
        )

        assert result.passed is False

    def test_var_calculation(self):
        """测试 VaR 计算"""
        controller = RiskController()
        returns = np.random.normal(0.001, 0.02, 1000)

        var_95 = controller.calculate_var(returns, confidence=0.95)
        var_99 = controller.calculate_var(returns, confidence=0.99)

        assert var_95 > 0
        assert var_99 > var_95  # 更高置信度 = 更大 VaR

    def test_cvar_calculation(self):
        """测试 CVaR 计算"""
        controller = RiskController()
        returns = np.random.normal(0.001, 0.02, 1000)

        cvar = controller.calculate_cvar(returns, confidence=0.95)
        var = controller.calculate_var(returns, confidence=0.95)

        assert cvar > 0
        assert cvar >= var  # CVaR >= VaR

    def test_var_with_series(self):
        """测试使用 pd.Series 的 VaR 计算"""
        controller = RiskController()
        returns = pd.Series(np.random.normal(0.001, 0.02, 500))

        var = controller.calculate_var(returns)
        assert var > 0

    def test_var_empty_data(self):
        """测试空数据的 VaR 计算"""
        controller = RiskController()
        assert controller.calculate_var([]) == 0.0
        assert controller.calculate_cvar(pd.Series()) == 0.0

    def test_drawdown_breach(self):
        """测试回撤超限"""
        config = RiskConfig(max_drawdown_pct=0.15)
        controller = RiskController(config)

        # 回撤 20% 超过 15% 限制
        action = controller.check_drawdown_breach(800_000, 1_000_000)

        assert action is not None
        assert action.action_type in (ActionType.CLOSE_ALL.value, ActionType.HALT_TRADING.value)
        assert "Drawdown" in action.reason

    def test_drawdown_no_breach(self):
        """测试回撤未超限"""
        config = RiskConfig(max_drawdown_pct=0.15)
        controller = RiskController(config)

        action = controller.check_drawdown_breach(900_000, 1_000_000)
        assert action is None

    def test_daily_loss_limit(self):
        """测试每日亏损限制"""
        config = RiskConfig(max_daily_loss_pct=0.05)
        controller = RiskController(config)

        # 日亏损 6% 超过 5% 限制
        action = controller.check_daily_loss_limit(-60_000, 1_000_000)

        assert action is not None
        assert action.action_type == ActionType.HALT_TRADING.value
        assert "Daily loss" in action.reason

    def test_daily_loss_no_breach(self):
        """测试每日亏损未超限"""
        config = RiskConfig(max_daily_loss_pct=0.05)
        controller = RiskController(config)

        action = controller.check_daily_loss_limit(-30_000, 1_000_000)
        assert action is None

    def test_daily_profit_no_trigger(self):
        """测试盈利不触发限制"""
        config = RiskConfig(max_daily_loss_pct=0.05)
        controller = RiskController(config)

        action = controller.check_daily_loss_limit(10_000, 1_000_000)
        assert action is None

    def test_concentration_check(self):
        """测试持仓集中度检查"""
        config = RiskConfig(max_position_pct=0.3)
        controller = RiskController(config)

        positions = [
            Position(symbol="AAPL", quantity=100, avg_price=150,
                     current_price=160, weight=0.5),
            Position(symbol="MSFT", quantity=100, avg_price=250,
                     current_price=240, weight=0.2),
        ]

        warnings = controller.check_concentration(positions)

        assert len(warnings) > 0
        assert any("AAPL" in w.message for w in warnings)
        assert warnings[0].severity == WarningSeverity.HIGH.value

    def test_concentration_no_warning(self):
        """测试集中度正常"""
        config = RiskConfig(max_position_pct=0.5)
        controller = RiskController(config)

        positions = [
            Position(symbol="AAPL", quantity=100, avg_price=150,
                     current_price=160, weight=0.3),
            Position(symbol="MSFT", quantity=100, avg_price=250,
                     current_price=240, weight=0.2),
        ]

        warnings = controller.check_concentration(positions)
        assert len(warnings) == 0

    def test_post_trade_update_normal(self):
        """测试交易后正常更新"""
        controller = RiskController()
        state = self._make_portfolio_state(drawdown=0.02)

        result = controller.post_trade_update(state)

        assert result.passed is True
        assert len(result.violations) == 0

    def test_post_trade_update_drawdown_breach(self):
        """测试交易后回撤超限"""
        config = RiskConfig(max_drawdown_pct=0.10)
        controller = RiskController(config)
        state = self._make_portfolio_state(
            total_equity=850_000,
            drawdown=0.15,
        )

        result = controller.post_trade_update(state)

        assert result.passed is False
        assert len(result.violations) > 0
        assert controller._trading_halted is True

    def test_post_trade_update_daily_loss(self):
        """测试交易后日亏损超限"""
        config = RiskConfig(max_daily_loss_pct=0.05)
        controller = RiskController(config)
        state = self._make_portfolio_state(daily_pnl=-60_000)

        result = controller.post_trade_update(state)

        assert result.passed is False
        assert controller._trading_halted is True

    def test_trading_halted(self):
        """测试交易暂停后拒绝新交易"""
        controller = RiskController()
        controller._trading_halted = True

        state = self._make_portfolio_state()
        result = controller.pre_trade_check(
            symbol="AAPL", direction="buy", price=150.0, volume=100,
            portfolio_state=state,
        )

        assert result.passed is False
        assert any(v.action_type == ActionType.HALT_TRADING.value for v in result.violations)

    def test_reset(self):
        """测试重置"""
        controller = RiskController()
        controller._trading_halted = True
        controller._peak_equity = 1_000_000

        controller.reset()

        assert controller._trading_halted is False
        assert controller._peak_equity is None

    def test_risk_report(self):
        """测试风险报告生成"""
        config = RiskConfig(max_position_pct=0.3)
        controller = RiskController(config)
        state = self._make_portfolio_state()

        report = controller.generate_risk_report(state)

        assert 'total_equity' in report
        assert 'risk_score' in report
        assert 'positions' in report
        assert 'warnings' in report
        assert 0 <= report['risk_score'] <= 100
        assert len(report['positions']) == 2

    def test_risk_callback(self):
        """测试风险回调创建"""
        controller = RiskController()
        callbacks = controller.create_risk_callback()

        assert 'pre_trade' in callbacks
        assert 'post_trade' in callbacks

        state = self._make_portfolio_state()
        result = callbacks['pre_trade'](
            symbol="GOOG", direction="buy", price=100.0, volume=100,
            portfolio_state=state,
        )
        assert isinstance(result, RiskCheckResult)

    def test_correlation_breach(self):
        """测试相关性超限检查"""
        config = RiskConfig(max_correlation=0.5)
        controller = RiskController(config)

        positions = [
            Position(symbol="A", quantity=100, avg_price=100, current_price=100),
            Position(symbol="B", quantity=100, avg_price=100, current_price=100),
        ]

        returns_data = {
            "A": pd.Series(np.random.normal(0.001, 0.02, 100)),
            "B": pd.Series(np.random.normal(0.001, 0.02, 100)),
        }
        # 创建高相关
        returns_data["B"] = returns_data["A"] * 0.95 + pd.Series(
            np.random.normal(0, 0.002, 100)
        )

        warnings = controller.check_correlation_breach(positions, returns_data)
        assert len(warnings) > 0
        assert any(w.warning_type == WarningType.CORRELATION.value for w in warnings)

    def test_portfolio_correlation(self):
        """测试组合相关性计算"""
        controller = RiskController()

        positions = [
            Position(symbol="A", quantity=100, avg_price=100, current_price=100),
            Position(symbol="B", quantity=100, avg_price=100, current_price=100),
        ]

        returns_data = {
            "A": pd.Series(np.random.normal(0.001, 0.02, 100)),
            "B": pd.Series(np.random.normal(0.001, 0.02, 100)),
        }

        corr = controller.calculate_portfolio_correlation(positions, returns_data)
        assert isinstance(corr, pd.DataFrame)
        assert corr.shape == (2, 2)

    def test_drawdown_with_zero_peak(self):
        """测试零峰值时的回撤检查"""
        controller = RiskController()
        action = controller.check_drawdown_breach(100, 0)
        assert action is None

    def test_daily_loss_with_zero_equity(self):
        """测试零权益时的日亏损检查"""
        controller = RiskController()
        action = controller.check_daily_loss_limit(-1000, 0)
        assert action is None
