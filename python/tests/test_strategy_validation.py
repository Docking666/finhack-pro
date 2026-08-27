"""策略验证框架接入测试：BacktestResult.validation 字段 + StrategyValidator 集成"""

import pytest

from finhack_pro.strategies.strategy_validator import StrategyValidator
from finhack_pro.webui.models import BacktestResult


class TestStrategyValidationIntegration:
    def test_validator_profiles_exist(self):
        """5 种预定义验证档位均可加载"""
        for profile in ("default", "conservative", "aggressive", "high_frequency", "low_frequency"):
            v = StrategyValidator.from_profile(profile)
            assert v.profile == profile

    def test_validate_returns_report_structure(self):
        """validate 输出 ValidationResult 结构（passed/score/checks/recommendations）"""
        perf = {
            "returns": [0.01, -0.005, 0.02, 0.0, 0.015, -0.01, 0.008, 0.012, -0.003, 0.005] * 20,
            "sharpe_ratio": 1.2,
            "max_drawdown": 0.08,
            "total_trades": 150,
            "annual_return": 0.25,
        }
        v = StrategyValidator.from_profile("default")
        result = v.validate(perf)
        assert hasattr(result, "passed")
        assert hasattr(result, "overall_score")
        assert isinstance(result.checks, list)
        assert len(result.checks) >= 4  # 核心检查（交易次数/夏普/回撤/Calmar）
        assert hasattr(result, "summary")
        assert hasattr(result, "profile_used")

    def test_backtest_result_validation_field(self):
        """BacktestResult 支持 validation 字段（可选，向后兼容）"""
        r = BacktestResult(task_id="t1")
        assert r.validation is None
        r2 = BacktestResult(task_id="t2", validation={"passed": True, "overall_score": 80.0})
        assert r2.validation["passed"] is True
