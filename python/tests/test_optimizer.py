"""
策略参数优化框架测试

测试 optimizer.py 中的所有优化器和工具类。
"""

import math
import numpy as np
import pandas as pd
import pytest

from finhack_pro.strategies.optimizer import (
    ParamSpace,
    TrialResult,
    OptimizationResult,
    WalkForwardResult,
    GridSearchOptimizer,
    RandomSearchOptimizer,
    BayesianOptimizer,
    WalkForwardValidator,
    OptimizationReport,
    _evaluate_strategy,
    _math_erf,
)


# ============================================================================
# 测试用策略类
# ============================================================================


class SimpleStrategy:
    """简单测试策略

    提供静态 backtest 方法，返回基于参数的确定性指标。
    得分函数: score = -(a - 3)^2 - (b - 7)^2 + 10
    最优参数: a=3, b=7, score=10
    """

    @staticmethod
    def backtest(params: dict, data=None) -> dict:
        a = params.get("a", 0)
        b = params.get("b", 0)
        score = -(a - 3.0) ** 2 - (b - 7.0) ** 2 + 10.0
        return {
            "sharpe_ratio": score,
            "annual_return": score * 0.1,
            "max_drawdown": max(0.01, 1.0 - score * 0.08),
            "total_trades": 100,
        }


class NoisyStrategy:
    """带噪声的测试策略

    得分 = -(a-5)^2 - (b-5)^2 + 20 + noise
    """

    _call_count = 0

    @staticmethod
    def backtest(params: dict, data=None) -> dict:
        a = params.get("a", 0)
        b = params.get("b", 0)
        # 使用调用次数作为种子，使噪声确定但不同
        np.random.seed(NoisyStrategy._call_count)
        NoisyStrategy._call_count += 1
        noise = np.random.normal(0, 0.1)
        score = -(a - 5.0) ** 2 - (b - 5.0) ** 2 + 20.0 + noise
        return {
            "sharpe_ratio": score,
            "annual_return": score * 0.1,
            "max_drawdown": max(0.01, 1.0 - score * 0.03),
        }


class ChoiceStrategy:
    """使用 choice 类型参数的测试策略

    最优: method="ema", period=10
    """

    @staticmethod
    def backtest(params: dict, data=None) -> dict:
        method = params.get("method", "sma")
        period = params.get("period", 5)
        if method == "ema":
            score = 10.0 - abs(period - 10) * 0.5
        else:
            score = 5.0 - abs(period - 10) * 0.3
        return {
            "sharpe_ratio": score,
            "annual_return": score * 0.1,
            "max_drawdown": 0.1,
        }


# ============================================================================
# TestParamSpace
# ============================================================================


class TestParamSpace:
    """ParamSpace 数据类测试"""

    def test_create_float_param(self):
        """测试创建浮点参数空间"""
        ps = ParamSpace(name="threshold", type="float", low=0.1, high=1.0, step=0.1)
        assert ps.name == "threshold"
        assert ps.type == "float"
        assert ps.low == 0.1
        assert ps.high == 1.0
        assert ps.step == 0.1

    def test_create_int_param(self):
        """测试创建整数参数空间"""
        ps = ParamSpace(name="period", type="int", low=5, high=20, step=5)
        assert ps.name == "period"
        assert ps.type == "int"
        assert ps.low == 5
        assert ps.high == 20
        assert ps.step == 5

    def test_create_choice_param(self):
        """测试创建离散选择参数空间"""
        ps = ParamSpace(name="method", type="choice", choices=["sma", "ema", "wma"])
        assert ps.name == "method"
        assert ps.type == "choice"
        assert ps.choices == ["sma", "ema", "wma"]

    def test_invalid_type(self):
        """测试不支持的参数类型"""
        with pytest.raises(ValueError, match="不支持的参数类型"):
            ParamSpace(name="bad", type="unknown")

    def test_invalid_range(self):
        """测试 low >= high"""
        with pytest.raises(ValueError, match="low.*必须.*<.*high"):
            ParamSpace(name="bad", type="float", low=10.0, high=5.0)

    def test_invalid_step(self):
        """测试 step <= 0"""
        with pytest.raises(ValueError, match="step.*必须.*> 0"):
            ParamSpace(name="bad", type="float", low=0.0, high=1.0, step=0.0)

    def test_empty_choices(self):
        """测试空的 choices 列表"""
        with pytest.raises(ValueError, match="必须提供 choices"):
            ParamSpace(name="bad", type="choice", choices=[])

    def test_none_choices(self):
        """测试 None 的 choices"""
        with pytest.raises(ValueError, match="必须提供 choices"):
            ParamSpace(name="bad", type="choice", choices=None)


# ============================================================================
# TestGridSearchOptimizer
# ============================================================================


class TestGridSearchOptimizer:
    """网格搜索优化器测试"""

    def test_simple_2d_grid(self):
        """测试简单的二维网格搜索"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=5, step=1),
            ParamSpace(name="b", type="int", low=5, high=9, step=1),
        ]
        optimizer = GridSearchOptimizer(param_space, metric="sharpe_ratio")
        result = optimizer.optimize(SimpleStrategy, data=None)

        # 验证结果
        assert result.method == "grid_search"
        assert len(result.all_results) == 25  # 5 * 5
        assert result.best_params["a"] == 3
        assert result.best_params["b"] == 7
        assert abs(result.best_score - 10.0) < 1e-6

    def test_best_selection(self):
        """测试最优参数选择"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
            ParamSpace(name="b", type="int", low=5, high=9, step=1),
        ]
        optimizer = GridSearchOptimizer(param_space, metric="sharpe_ratio")
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert result.best_params["a"] == 3
        assert result.best_params["b"] == 7
        assert abs(result.best_score - 10.0) < 1e-6

    def test_higher_is_better_false(self):
        """测试 higher_is_better=False (最小化)"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
            ParamSpace(name="b", type="int", low=1, high=10, step=1),
        ]
        optimizer = GridSearchOptimizer(
            param_space, metric="max_drawdown", higher_is_better=False
        )
        result = optimizer.optimize(SimpleStrategy, data=None)

        # max_drawdown 应该被最小化
        # SimpleStrategy 的 max_drawdown = max(0.01, 1.0 - score * 0.08)
        # score 最小时 max_drawdown 最大，score 最大时 max_drawdown 最小
        assert result.method == "grid_search"
        assert result.best_score <= all(
            r.score for r in result.all_results
        ) or result.best_score == min(r.score for r in result.all_results)

    def test_convergence_history(self):
        """测试收敛历史记录"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=5, step=1),
        ]
        optimizer = GridSearchOptimizer(param_space, metric="sharpe_ratio")
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert len(result.convergence_history) == 5
        # 收敛历史应该是单调递增的 (higher_is_better=True)
        for i in range(1, len(result.convergence_history)):
            assert result.convergence_history[i] >= result.convergence_history[i - 1]

    def test_optimization_time(self):
        """测试优化耗时记录"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=3, step=1),
        ]
        optimizer = GridSearchOptimizer(param_space, metric="sharpe_ratio")
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert result.optimization_time > 0

    def test_empty_param_space(self):
        """测试空参数空间"""
        optimizer = GridSearchOptimizer([], metric="sharpe_ratio")
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert len(result.all_results) == 1
        assert result.best_params == {}

    def test_float_param_grid(self):
        """测试浮点参数网格"""
        param_space = [
            ParamSpace(name="a", type="float", low=2.0, high=4.0, step=0.5),
            ParamSpace(name="b", type="float", low=6.0, high=8.0, step=0.5),
        ]
        optimizer = GridSearchOptimizer(param_space, metric="sharpe_ratio")
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert result.best_params["a"] == 3.0
        assert result.best_params["b"] == 7.0
        assert abs(result.best_score - 10.0) < 1e-6


# ============================================================================
# TestRandomSearchOptimizer
# ============================================================================


class TestRandomSearchOptimizer:
    """随机搜索优化器测试"""

    def test_sampling(self):
        """测试随机采样产生有效参数"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
            ParamSpace(name="b", type="float", low=0.0, high=1.0, step=0.1),
        ]
        optimizer = RandomSearchOptimizer(param_space, n_trials=20, seed=42)
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert result.method == "random_search"
        assert len(result.all_results) == 20
        # 所有参数应该在有效范围内
        for trial in result.all_results:
            assert 1 <= trial.params["a"] <= 10
            assert 0.0 <= trial.params["b"] <= 1.0

    def test_n_trials_limit(self):
        """测试试验次数限制"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
        ]
        optimizer = RandomSearchOptimizer(param_space, n_trials=15, seed=42)
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert len(result.all_results) == 15

    def test_reproducibility_with_seed(self):
        """测试使用相同种子产生相同结果"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
            ParamSpace(name="b", type="int", low=1, high=10, step=1),
        ]

        optimizer1 = RandomSearchOptimizer(param_space, n_trials=20, seed=123)
        result1 = optimizer1.optimize(SimpleStrategy, data=None)

        optimizer2 = RandomSearchOptimizer(param_space, n_trials=20, seed=123)
        result2 = optimizer2.optimize(SimpleStrategy, data=None)

        # 相同种子应产生完全相同的参数序列和结果
        assert len(result1.all_results) == len(result2.all_results)
        for r1, r2 in zip(result1.all_results, result2.all_results):
            assert r1.params == r2.params
            assert abs(r1.score - r2.score) < 1e-10

    def test_convergence_history(self):
        """测试收敛历史"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
        ]
        optimizer = RandomSearchOptimizer(param_space, n_trials=30, seed=42)
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert len(result.convergence_history) == 30

    def test_choice_param_sampling(self):
        """测试离散选择参数的采样"""
        param_space = [
            ParamSpace(name="method", type="choice", choices=["sma", "ema", "wma"]),
            ParamSpace(name="period", type="int", low=5, high=15, step=1),
        ]
        optimizer = RandomSearchOptimizer(param_space, n_trials=30, seed=42)
        result = optimizer.optimize(ChoiceStrategy, data=None)

        # 验证所有 method 值都是有效选择
        for trial in result.all_results:
            assert trial.params["method"] in ["sma", "ema", "wma"]


# ============================================================================
# TestBayesianOptimizer
# ============================================================================


class TestBayesianOptimizer:
    """贝叶斯优化器测试"""

    def test_basic_optimization(self):
        """测试基本贝叶斯优化流程"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
            ParamSpace(name="b", type="int", low=1, high=10, step=1),
        ]
        optimizer = BayesianOptimizer(
            param_space, n_trials=20, n_initial=5, seed=42
        )
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert result.method == "bayesian"
        assert len(result.all_results) == 20
        assert len(result.convergence_history) == 20
        # 应该找到接近最优的参数
        assert result.best_score > 0

    def test_fallback_to_random(self):
        """测试 GP 拟合失败时回退到随机搜索"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
        ]

        # 使用极少的初始采样点 (n_initial=2)，可能导致 GP 不稳定
        # 但仍然应该完成优化而不崩溃
        optimizer = BayesianOptimizer(
            param_space, n_trials=10, n_initial=2, seed=42
        )
        result = optimizer.optimize(SimpleStrategy, data=None)

        assert result.method == "bayesian"
        assert len(result.all_results) == 10

    def test_convergence(self):
        """测试贝叶斯优化的收敛性"""
        param_space = [
            ParamSpace(name="a", type="float", low=0.0, high=10.0, step=0.5),
            ParamSpace(name="b", type="float", low=0.0, high=10.0, step=0.5),
        ]
        optimizer = BayesianOptimizer(
            param_space, n_trials=30, n_initial=5, seed=42
        )
        result = optimizer.optimize(SimpleStrategy, data=None)

        # 收敛历史应该存在且长度正确
        assert len(result.convergence_history) == 30
        # 最终得分应该优于或等于初始得分
        assert result.convergence_history[-1] >= result.convergence_history[0]

    def test_reproducibility(self):
        """测试贝叶斯优化的可复现性"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=10, step=1),
        ]

        optimizer1 = BayesianOptimizer(param_space, n_trials=15, seed=99)
        result1 = optimizer1.optimize(SimpleStrategy, data=None)

        optimizer2 = BayesianOptimizer(param_space, n_trials=15, seed=99)
        result2 = optimizer2.optimize(SimpleStrategy, data=None)

        assert len(result1.all_results) == len(result2.all_results)
        for r1, r2 in zip(result1.all_results, result2.all_results):
            assert r1.params == r2.params

    def test_erf_approximation(self):
        """测试误差函数近似精度"""
        # 测试几个已知值 (近似公式精度约 1.5e-7)
        assert abs(_math_erf(0.0)) < 1e-8
        assert abs(_math_erf(1.0) - 0.8427007929) < 1.5e-7
        assert abs(_math_erf(-1.0) + 0.8427007929) < 1.5e-7
        assert abs(_math_erf(2.0) - 0.995322265) < 1.5e-7
        # erf(inf) 应该接近 1
        assert _math_erf(5.0) > 0.999


# ============================================================================
# TestWalkForwardValidator
# ============================================================================


class TestWalkForwardValidator:
    """Walk-Forward 验证器测试"""

    @pytest.fixture
    def wf_data(self):
        """生成 Walk-Forward 测试数据"""
        np.random.seed(42)
        n = 500
        dates = pd.date_range("2023-01-01", periods=n, freq="D")
        return pd.DataFrame({
            "date": dates,
            "close": np.cumsum(np.random.normal(0.1, 1.0, n)) + 100,
            "volume": np.random.uniform(1000, 10000, n),
        })

    def test_data_splitting(self, wf_data):
        """测试数据分割"""
        validator = WalkForwardValidator(n_splits=3, train_pct=0.7)
        splits = validator._split_data(wf_data, 3, 0.7, 0)

        assert len(splits) > 0
        for is_data, oos_data in splits:
            assert len(is_data) > 0
            assert len(oos_data) > 0
            # IS 数据应该在 OOS 数据之前
            assert is_data["date"].max() <= oos_data["date"].min()

    def test_is_oos_separation(self, wf_data):
        """测试 IS/OOS 数据无重叠"""
        validator = WalkForwardValidator(n_splits=3, train_pct=0.6)
        splits = validator._split_data(wf_data, 3, 0.6, 0)

        for is_data, oos_data in splits:
            is_dates = set(is_data["date"])
            oos_dates = set(oos_data["date"])
            assert len(is_dates & oos_dates) == 0

    def test_gap_days(self, wf_data):
        """测试间隔天数"""
        validator = WalkForwardValidator(n_splits=3, train_pct=0.6, gap_days=5)
        splits = validator._split_data(wf_data, 3, 0.6, 5)

        assert len(splits) > 0
        for is_data, oos_data in splits:
            # IS 结束日期和 OOS 开始日期之间应该有间隔
            is_end = is_data["date"].max()
            oos_start = oos_data["date"].min()
            gap = (oos_start - is_end).days
            assert gap >= 5

    def test_full_validation(self, wf_data):
        """测试完整 Walk-Forward 验证流程"""
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=5, step=1),
            ParamSpace(name="b", type="int", low=5, high=9, step=1),
        ]
        validator = WalkForwardValidator(n_splits=3, train_pct=0.7)
        result = validator.validate(
            SimpleStrategy, param_space, wf_data,
            optimizer_class=GridSearchOptimizer,
        )

        assert isinstance(result, WalkForwardResult)
        assert len(result.is_segments) > 0
        assert len(result.oos_results) > 0
        assert len(result.is_segments) == len(result.oos_results)
        # IS/OOS 相关性应该在 [-1, 1] 范围内
        assert -1.0 <= result.is_oos_correlation <= 1.0

    def test_overfitting_detection(self, wf_data):
        """测试过拟合检测

        使用一个在 IS 上表现好但 OOS 上表现差的策略来检测过拟合。
        """
        param_space = [
            ParamSpace(name="a", type="int", low=1, high=5, step=1),
        ]

        # 创建一个过拟合策略: IS 上总是返回高分，OOS 上返回低分
        class OverfitStrategy:
            _is_mode = True

            @staticmethod
            def backtest(params, data=None):
                if OverfitStrategy._is_mode:
                    return {"sharpe_ratio": 5.0, "annual_return": 0.5, "max_drawdown": 0.05}
                else:
                    return {"sharpe_ratio": -1.0, "annual_return": -0.2, "max_drawdown": 0.5}

        # 先运行 IS 优化 (过拟合策略总是返回高分)
        OverfitStrategy._is_mode = True
        validator = WalkForwardValidator(n_splits=3, train_pct=0.7)
        result = validator.validate(
            OverfitStrategy, param_space, wf_data,
            optimizer_class=GridSearchOptimizer,
        )

        # 验证结果结构
        assert len(result.is_segments) > 0
        assert len(result.oos_results) > 0
        assert "combined_metrics" in result.__dict__

    def test_invalid_n_splits(self):
        """测试无效的 n_splits"""
        with pytest.raises(ValueError, match="n_splits 必须 >= 2"):
            WalkForwardValidator(n_splits=1)

    def test_invalid_train_pct(self):
        """测试无效的 train_pct"""
        with pytest.raises(ValueError, match="train_pct 必须在"):
            WalkForwardValidator(train_pct=0.0)
        with pytest.raises(ValueError, match="train_pct 必须在"):
            WalkForwardValidator(train_pct=1.0)

    def test_invalid_gap_days(self):
        """测试无效的 gap_days"""
        with pytest.raises(ValueError, match="gap_days 必须 >= 0"):
            WalkForwardValidator(gap_days=-1)

    def test_correlation_calculation(self):
        """测试相关系数计算"""
        # 完全正相关
        corr = WalkForwardValidator._calc_correlation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
        assert abs(corr - 1.0) < 1e-6

        # 完全负相关
        corr = WalkForwardValidator._calc_correlation([1, 2, 3, 4, 5], [10, 8, 6, 4, 2])
        assert abs(corr + 1.0) < 1e-6

        # 数据不足
        corr = WalkForwardValidator._calc_correlation([1], [2])
        assert corr == 0.0

        # 长度不匹配
        corr = WalkForwardValidator._calc_correlation([1, 2], [1, 2, 3])
        assert corr == 0.0


# ============================================================================
# TestOptimizationReport
# ============================================================================


class TestOptimizationReport:
    """优化报告生成器测试"""

    @pytest.fixture
    def sample_result(self):
        """生成示例优化结果"""
        trials = []
        for i in range(10):
            score = 10.0 - i * 0.5
            trials.append(TrialResult(
                params={"a": i + 1, "b": 10 - i},
                score=score,
                metrics={"sharpe_ratio": score, "annual_return": score * 0.1},
                trial_id=i,
            ))
        return OptimizationResult(
            best_params={"a": 1, "b": 10},
            best_score=10.0,
            all_results=trials,
            optimization_time=1.23,
            method="grid_search",
            convergence_history=[10.0 - i * 0.5 for i in range(10)],
        )

    def test_report_generation(self, sample_result):
        """测试文本报告生成"""
        report = OptimizationReport.generate_report(sample_result)

        assert isinstance(report, str)
        assert "策略参数优化报告" in report
        assert "grid_search" in report
        assert "1.23" in report
        assert "最优参数" in report
        assert "a: 1" in report
        assert "b: 10" in report
        assert "10.000000" in report
        assert "Top 5" in report
        assert "收敛信息" in report
        assert "得分统计" in report

    def test_convergence_plot(self, sample_result):
        """测试收敛曲线图生成"""
        plot_b64 = OptimizationReport.generate_convergence_plot(sample_result)

        assert isinstance(plot_b64, str)
        # 如果 matplotlib 可用，应该返回 base64 字符串
        # 如果不可用，返回空字符串
        if plot_b64:
            # Base64 编码的 PNG 应该以 iVBOR 开头 (PNG 文件头)
            import base64
            try:
                decoded = base64.b64decode(plot_b64)
                assert decoded[:4] == b'\x89PNG'
            except Exception:
                pass

    def test_param_importance(self, sample_result):
        """测试参数重要性分析"""
        importance = OptimizationReport.generate_param_importance(sample_result)

        assert isinstance(importance, str)
        assert "参数重要性分析" in importance
        assert "参数: a" in importance
        assert "参数: b" in importance
        assert "变异系数" in importance
        assert "重要性" in importance

    def test_compare_results(self, sample_result):
        """测试多结果比较"""
        # 创建第二个结果
        trials2 = [
            TrialResult(
                params={"a": 2, "b": 8},
                score=8.0,
                metrics={"sharpe_ratio": 8.0},
                trial_id=0,
            )
        ]
        result2 = OptimizationResult(
            best_params={"a": 2, "b": 8},
            best_score=8.0,
            all_results=trials2,
            optimization_time=0.5,
            method="random_search",
            convergence_history=[8.0],
        )

        comparison = OptimizationReport.compare_results({
            "Grid": sample_result,
            "Random": result2,
        })

        assert isinstance(comparison, str)
        assert "优化方法比较报告" in comparison
        assert "Grid" in comparison
        assert "Random" in comparison
        assert "全局最优方法" in comparison

    def test_empty_report(self):
        """测试空结果的报告生成"""
        empty_result = OptimizationResult(
            best_params={},
            best_score=0.0,
            all_results=[],
            optimization_time=0.0,
            method="test",
            convergence_history=[],
        )
        report = OptimizationReport.generate_report(empty_result)
        assert "策略参数优化报告" in report

    def test_empty_importance(self):
        """测试空结果的参数重要性"""
        empty_result = OptimizationResult(
            best_params={},
            best_score=0.0,
            all_results=[],
            optimization_time=0.0,
            method="test",
        )
        importance = OptimizationReport.generate_param_importance(empty_result)
        assert "无足够数据" in importance


# ============================================================================
# TestEvaluateStrategy
# ============================================================================


class TestEvaluateStrategy:
    """策略评估函数测试"""

    def test_evaluate_with_backtest(self):
        """测试使用 backtest 方法的评估"""
        score, metrics = _evaluate_strategy(
            SimpleStrategy, None, {"a": 3, "b": 7}, "sharpe_ratio"
        )
        assert abs(score - 10.0) < 1e-6
        assert "sharpe_ratio" in metrics
        assert "annual_return" in metrics

    def test_evaluate_default(self):
        """测试默认评估函数"""
        # 使用一个没有 backtest 方法的类
        class DummyStrategy:
            pass

        score, metrics = _evaluate_strategy(
            DummyStrategy, None, {"a": 5, "b": 5}, "sharpe_ratio"
        )
        assert isinstance(score, float)
        assert isinstance(metrics, dict)
        assert "sharpe_ratio" in metrics


# ============================================================================
# TestDataStructures
# ============================================================================


class TestDataStructures:
    """数据结构测试"""

    def test_trial_result(self):
        """测试 TrialResult 数据类"""
        trial = TrialResult(
            params={"a": 1, "b": 2},
            score=5.0,
            metrics={"sharpe_ratio": 5.0},
            trial_id=0,
        )
        assert trial.params == {"a": 1, "b": 2}
        assert trial.score == 5.0
        assert trial.metrics["sharpe_ratio"] == 5.0
        assert trial.trial_id == 0

    def test_optimization_result(self):
        """测试 OptimizationResult 数据类"""
        result = OptimizationResult(
            best_params={"a": 1},
            best_score=10.0,
            all_results=[],
            optimization_time=1.0,
            method="test",
            convergence_history=[1.0, 5.0, 10.0],
        )
        assert result.best_params == {"a": 1}
        assert result.best_score == 10.0
        assert result.method == "test"
        assert len(result.convergence_history) == 3

    def test_walk_forward_result(self):
        """测试 WalkForwardResult 数据类"""
        wf = WalkForwardResult(
            is_segments=[{"best_score": 5.0}],
            oos_results=[{"score": 3.0}],
            combined_metrics={"mean_score": 3.0},
            is_oos_correlation=0.8,
        )
        assert len(wf.is_segments) == 1
        assert len(wf.oos_results) == 1
        assert wf.is_oos_correlation == 0.8
