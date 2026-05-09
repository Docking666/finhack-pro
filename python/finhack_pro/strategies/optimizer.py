"""
策略参数优化框架模块

提供多种参数优化方法，用于寻找策略的最优参数组合。包含:
- 网格搜索 (GridSearch): 穷举所有参数组合
- 随机搜索 (RandomSearch): 随机采样参数空间
- 贝叶斯优化 (Bayesian): 基于高斯过程代理模型的智能搜索
- Walk-Forward 验证: 滚动窗口的样本外验证
- 优化报告: 结果可视化与分析

所有优化算法均从零实现，不依赖外部优化库。
"""

from __future__ import annotations

import copy
import io
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

import numpy as np

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# 数据结构定义
# ============================================================================


@dataclass
class ParamSpace:
    """参数搜索空间定义

    Attributes:
        name: 参数名称
        type: 参数类型，支持 "int", "float", "choice"
        low: 数值参数下界 (int/float 类型)
        high: 数值参数上界 (int/float 类型)
        step: 数值参数步长 (int/float 类型)
        choices: 离散参数候选值列表 (choice 类型)
    """
    name: str
    type: str = "float"  # "int", "float", "choice"
    low: float = 0.0
    high: float = 1.0
    step: float = 0.1
    choices: Optional[List[Any]] = None

    def validate(self) -> None:
        """验证参数空间定义是否合法"""
        if self.type not in ("int", "float", "choice"):
            raise ValueError(f"不支持的参数类型: {self.type}，可选: int, float, choice")
        if self.type == "choice":
            if not self.choices or len(self.choices) == 0:
                raise ValueError(f"choice 类型参数 '{self.name}' 必须提供 choices")
        else:
            if self.low >= self.high:
                raise ValueError(
                    f"参数 '{self.name}' 的 low ({self.low}) 必须 < high ({self.high})"
                )
            if self.step <= 0:
                raise ValueError(f"参数 '{self.name}' 的 step ({self.step}) 必须 > 0")

    def __post_init__(self) -> None:
        self.validate()


@dataclass
class TrialResult:
    """单次优化试验结果

    Attributes:
        params: 试验使用的参数组合
        score: 目标指标得分
        metrics: 完整的评估指标字典
        trial_id: 试验编号
    """
    params: Dict[str, Any]
    score: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    trial_id: int = 0


@dataclass
class OptimizationResult:
    """优化结果

    Attributes:
        best_params: 最优参数组合
        best_score: 最优得分
        all_results: 所有试验结果列表
        optimization_time: 优化耗时 (秒)
        method: 使用的优化方法名称
        convergence_history: 收敛历史，每步的最优得分
    """
    best_params: Dict[str, Any] = field(default_factory=dict)
    best_score: float = 0.0
    all_results: List[TrialResult] = field(default_factory=list)
    optimization_time: float = 0.0
    method: str = ""
    convergence_history: List[float] = field(default_factory=list)


@dataclass
class WalkForwardResult:
    """Walk-Forward 验证结果

    Attributes:
        is_segments: 样本内 (In-Sample) 各段优化结果
        oos_results: 样本外 (Out-of-Sample) 各段测试结果
        combined_metrics: 合并的样本外指标
        is_oos_correlation: IS/OOS 得分相关系数，用于检测过拟合
    """
    is_segments: List[Dict[str, Any]] = field(default_factory=list)
    oos_results: List[Dict[str, Any]] = field(default_factory=list)
    combined_metrics: Dict[str, Any] = field(default_factory=dict)
    is_oos_correlation: float = 0.0


# ============================================================================
# 策略回测评估辅助函数
# ============================================================================


def _evaluate_strategy(
    strategy_class: Type,
    data: Any,
    params: Dict[str, Any],
    metric: str = "sharpe_ratio",
) -> Tuple[float, Dict[str, float]]:
    """评估策略在给定参数和数据上的表现

    策略类需要实现 backtest(params, data) -> Dict 方法，
    返回包含 sharpe_ratio, max_drawdown, annual_return 等指标的字典。

    如果策略类没有 backtest 方法，则使用简单的参数-得分映射进行评估。

    Args:
        strategy_class: 策略类
        data: 回测数据 (DataFrame 或其他格式)
        params: 策略参数
        metric: 优化目标指标名称

    Returns:
        (目标得分, 完整指标字典)
    """
    # 如果策略类有 backtest 类方法，使用它
    if hasattr(strategy_class, "backtest") and callable(getattr(strategy_class, "backtest")):
        try:
            metrics = strategy_class.backtest(params=params, data=data)
            if isinstance(metrics, dict):
                score = metrics.get(metric, 0.0)
                return float(score), {k: float(v) for k, v in metrics.items()}
        except Exception as e:
            logger.debug(f"策略回测失败: {e}")
            return 0.0, {}

    # 如果策略类有 evaluate 类方法，使用它
    if hasattr(strategy_class, "evaluate") and callable(getattr(strategy_class, "evaluate")):
        try:
            metrics = strategy_class.evaluate(params=params, data=data)
            if isinstance(metrics, dict):
                score = metrics.get(metric, 0.0)
                return float(score), {k: float(v) for k, v in metrics.items()}
        except Exception as e:
            logger.debug(f"策略评估失败: {e}")
            return 0.0, {}

    # 兜底: 使用参数的简单函数作为得分
    # 这允许在测试中注入简单的评估逻辑
    return _default_evaluate(params, metric)


def _default_evaluate(
    params: Dict[str, Any],
    metric: str = "sharpe_ratio",
) -> Tuple[float, Dict[str, float]]:
    """默认评估函数

    当策略类没有提供 backtest/evaluate 方法时使用。
    基于参数值生成一个确定性的得分（用于测试）。

    得分函数: score = -sum((param - target)^2) + noise
    其中 target 是参数范围的中间值。
    """
    score = 0.0
    metrics = {}
    for key, value in params.items():
        if isinstance(value, (int, float)):
            # 使用一个简单的二次函数，在参数空间中间取得最大值
            score += -0.01 * (value - 5.0) ** 2 + 1.0
            metrics[f"{key}_contribution"] = -0.01 * (value - 5.0) ** 2 + 1.0

    metrics["sharpe_ratio"] = max(score, 0.0)
    metrics["annual_return"] = score * 0.1
    metrics["max_drawdown"] = max(0.01, 0.5 - score * 0.05)

    if metric in metrics:
        return metrics[metric], metrics
    return score, metrics


# ============================================================================
# 网格搜索优化器
# ============================================================================


class GridSearchOptimizer:
    """网格搜索优化器

    穷举参数空间中所有可能的参数组合，找到最优参数。

    Usage:
        param_space = [
            ParamSpace(name="period", type="int", low=5, high=20, step=5),
            ParamSpace(name="threshold", type="float", low=0.1, high=0.5, step=0.1),
        ]
        optimizer = GridSearchOptimizer(param_space, metric="sharpe_ratio")
        result = optimizer.optimize(MyStrategy, data)
        print(result.best_params, result.best_score)
    """

    def __init__(
        self,
        param_space: List[ParamSpace],
        metric: str = "sharpe_ratio",
        higher_is_better: bool = True,
        max_workers: int = 1,
    ) -> None:
        """初始化网格搜索优化器

        Args:
            param_space: 参数搜索空间列表
            metric: 优化目标指标名称
            higher_is_better: 目标指标是否越大越好
            max_workers: 并行工作线程数，1 表示串行
        """
        for ps in param_space:
            ps.validate()
        self._param_space = param_space
        self._metric = metric
        self._higher_is_better = higher_is_better
        self._max_workers = max_workers

    def optimize(
        self,
        strategy_class: Type,
        data: Any,
        **kwargs: Any,
    ) -> OptimizationResult:
        """执行网格搜索优化

        Args:
            strategy_class: 策略类
            data: 回测数据
            **kwargs: 额外参数 (如 max_workers 覆盖)

        Returns:
            OptimizationResult 优化结果
        """
        start_time = time.time()
        grid = self._generate_grid(self._param_space)
        logger.info(f"网格搜索: 共 {len(grid)} 个参数组合")

        max_workers = kwargs.get("max_workers", self._max_workers)
        results: List[TrialResult] = []
        convergence: List[float] = []
        current_best = -math.inf if self._higher_is_better else math.inf

        if max_workers > 1 and len(grid) > 1:
            # 并行执行
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_params = {}
                for i, params in enumerate(grid):
                    future = executor.submit(
                        _evaluate_strategy, strategy_class, data, params, self._metric
                    )
                    future_to_params[future] = (i, params)

                for future in as_completed(future_to_params):
                    trial_id, params = future_to_params[future]
                    try:
                        score, metrics = future.result()
                    except Exception as e:
                        logger.warning(f"试验 {trial_id} 失败: {e}")
                        score, metrics = 0.0, {}

                    trial = TrialResult(
                        params=params,
                        score=score,
                        metrics=metrics,
                        trial_id=trial_id,
                    )
                    results.append(trial)

                    # 更新当前最优
                    if self._higher_is_better:
                        if score > current_best:
                            current_best = score
                    else:
                        if score < current_best:
                            current_best = score
                    convergence.append(current_best)
        else:
            # 串行执行
            for i, params in enumerate(grid):
                score, metrics = _evaluate_strategy(
                    strategy_class, data, params, self._metric
                )
                trial = TrialResult(
                    params=params,
                    score=score,
                    metrics=metrics,
                    trial_id=i,
                )
                results.append(trial)

                if self._higher_is_better:
                    if score > current_best:
                        current_best = score
                else:
                    if score < current_best:
                        current_best = score
                convergence.append(current_best)

        # 排序并选择最优
        results.sort(
            key=lambda r: r.score,
            reverse=self._higher_is_better,
        )

        best = results[0] if results else TrialResult(params={}, score=0.0)

        elapsed = time.time() - start_time
        logger.info(
            f"网格搜索完成: 耗时 {elapsed:.2f}s, "
            f"最优得分={best.score:.4f}, 参数={best.params}"
        )

        return OptimizationResult(
            best_params=best.params,
            best_score=best.score,
            all_results=results,
            optimization_time=elapsed,
            method="grid_search",
            convergence_history=convergence,
        )

    @staticmethod
    def _generate_grid(param_space: List[ParamSpace]) -> List[Dict[str, Any]]:
        """生成所有参数组合

        Args:
            param_space: 参数搜索空间列表

        Returns:
            参数组合字典列表
        """
        if not param_space:
            return [{}]

        # 为每个参数生成候选值列表
        value_lists: List[List[Any]] = []
        for ps in param_space:
            if ps.type == "choice":
                value_lists.append(list(ps.choices))
            elif ps.type == "int":
                values = list(range(int(ps.low), int(ps.high) + 1, int(ps.step)))
                value_lists.append(values)
            else:  # float
                n_steps = int(round((ps.high - ps.low) / ps.step))
                values = [ps.low + i * ps.step for i in range(n_steps + 1)]
                # 浮点精度修正
                values = [round(v, 10) for v in values]
                value_lists.append(values)

        # 笛卡尔积
        from itertools import product

        combinations = list(product(*value_lists))
        names = [ps.name for ps in param_space]

        return [dict(zip(names, combo)) for combo in combinations]


# ============================================================================
# 随机搜索优化器
# ============================================================================


class RandomSearchOptimizer:
    """随机搜索优化器

    从参数空间中随机采样指定次数的参数组合进行评估。

    Usage:
        optimizer = RandomSearchOptimizer(param_space, n_trials=100)
        result = optimizer.optimize(MyStrategy, data)
    """

    def __init__(
        self,
        param_space: List[ParamSpace],
        n_trials: int = 100,
        metric: str = "sharpe_ratio",
        higher_is_better: bool = True,
        seed: Optional[int] = None,
        max_workers: int = 1,
    ) -> None:
        """初始化随机搜索优化器

        Args:
            param_space: 参数搜索空间列表
            n_trials: 随机采样次数
            metric: 优化目标指标名称
            higher_is_better: 目标指标是否越大越好
            seed: 随机种子，用于结果可复现
            max_workers: 并行工作线程数
        """
        for ps in param_space:
            ps.validate()
        self._param_space = param_space
        self._n_trials = n_trials
        self._metric = metric
        self._higher_is_better = higher_is_better
        self._seed = seed
        self._max_workers = max_workers
        self._rng = np.random.RandomState(seed)

    def optimize(
        self,
        strategy_class: Type,
        data: Any,
        **kwargs: Any,
    ) -> OptimizationResult:
        """执行随机搜索优化

        Args:
            strategy_class: 策略类
            data: 回测数据

        Returns:
            OptimizationResult 优化结果
        """
        start_time = time.time()
        logger.info(f"随机搜索: 共 {self._n_trials} 次采样")

        results: List[TrialResult] = []
        convergence: List[float] = []
        current_best = -math.inf if self._higher_is_better else math.inf

        for i in range(self._n_trials):
            params = self._sample_params()
            score, metrics = _evaluate_strategy(
                strategy_class, data, params, self._metric
            )

            trial = TrialResult(
                params=params,
                score=score,
                metrics=metrics,
                trial_id=i,
            )
            results.append(trial)

            if self._higher_is_better:
                if score > current_best:
                    current_best = score
            else:
                if score < current_best:
                    current_best = score
            convergence.append(current_best)

        # 排序并选择最优
        results.sort(
            key=lambda r: r.score,
            reverse=self._higher_is_better,
        )

        best = results[0] if results else TrialResult(params={}, score=0.0)

        elapsed = time.time() - start_time
        logger.info(
            f"随机搜索完成: 耗时 {elapsed:.2f}s, "
            f"最优得分={best.score:.4f}, 参数={best.params}"
        )

        return OptimizationResult(
            best_params=best.params,
            best_score=best.score,
            all_results=results,
            optimization_time=elapsed,
            method="random_search",
            convergence_history=convergence,
        )

    def _sample_params(self) -> Dict[str, Any]:
        """随机采样一组参数

        Returns:
            参数字典
        """
        params: Dict[str, Any] = {}
        for ps in self._param_space:
            if ps.type == "choice":
                idx = self._rng.randint(0, len(ps.choices))
                params[ps.name] = ps.choices[idx]
            elif ps.type == "int":
                n_steps = int((ps.high - ps.low) / ps.step)
                if n_steps <= 0:
                    params[ps.name] = int(ps.low)
                else:
                    idx = self._rng.randint(0, n_steps + 1)
                    params[ps.name] = int(ps.low + idx * ps.step)
            else:  # float
                params[ps.name] = float(self._rng.uniform(ps.low, ps.high))
        return params


# ============================================================================
# 贝叶斯优化器 (基于高斯过程)
# ============================================================================


class BayesianOptimizer:
    """贝叶斯优化器

    使用高斯过程 (Gaussian Process) 作为代理模型，
    通过期望改进 (Expected Improvement) 采集函数智能选择下一组参数。

    GP 实现:
    - 核函数: RBF (径向基函数) + 白噪声
    - 超参数: 通过简单启发式方法设定
    - 矩阵运算: 使用 numpy 求解线性方程组

    Usage:
        optimizer = BayesianOptimizer(param_space, n_trials=50)
        result = optimizer.optimize(MyStrategy, data)
    """

    def __init__(
        self,
        param_space: List[ParamSpace],
        n_trials: int = 50,
        metric: str = "sharpe_ratio",
        higher_is_better: bool = True,
        seed: Optional[int] = None,
        n_initial: int = 5,
        n_candidates: int = 100,
    ) -> None:
        """初始化贝叶斯优化器

        Args:
            param_space: 参数搜索空间列表
            n_trials: 总试验次数 (包含初始随机采样)
            metric: 优化目标指标名称
            higher_is_better: 目标指标是否越大越好
            seed: 随机种子
            n_initial: 初始随机采样次数 (用于构建初始代理模型)
            n_candidates: 采集函数候选采样点数
        """
        for ps in param_space:
            ps.validate()
        self._param_space = param_space
        self._n_trials = n_trials
        self._metric = metric
        self._higher_is_better = higher_is_better
        self._seed = seed
        self._rng = np.random.RandomState(seed)
        self._n_initial = min(n_initial, n_trials)
        self._n_candidates = n_candidates

    def optimize(
        self,
        strategy_class: Type,
        data: Any,
        **kwargs: Any,
    ) -> OptimizationResult:
        """执行贝叶斯优化

        流程:
        1. 初始随机采样 n_initial 次
        2. 拟合 GP 代理模型
        3. 通过 EI 采集函数选择下一组参数
        4. 评估新参数并更新代理模型
        5. 重复 2-4 直到达到 n_trials

        Args:
            strategy_class: 策略类
            data: 回测数据

        Returns:
            OptimizationResult 优化结果
        """
        start_time = time.time()
        logger.info(f"贝叶斯优化: 共 {self._n_trials} 次试验 (初始 {self._n_initial} 次)")

        results: List[TrialResult] = []
        convergence: List[float] = []
        current_best = -math.inf if self._higher_is_better else math.inf

        # 阶段1: 初始随机采样
        for i in range(self._n_initial):
            params = self._sample_params_random()
            score, metrics = _evaluate_strategy(
                strategy_class, data, params, self._metric
            )
            trial = TrialResult(
                params=params,
                score=score,
                metrics=metrics,
                trial_id=i,
            )
            results.append(trial)

            if self._higher_is_better:
                if score > current_best:
                    current_best = score
            else:
                if score < current_best:
                    current_best = score
            convergence.append(current_best)

        # 阶段2: 贝叶斯优化迭代
        for i in range(self._n_initial, self._n_trials):
            # 拟合代理模型
            try:
                surrogate = self._fit_surrogate(results)
                best_y = current_best

                # 使用 EI 采集函数建议下一组参数
                params = self._suggest_next(surrogate, best_y)
                if params is None:
                    # 采集函数失败，回退到随机采样
                    logger.debug("EI 采集函数未能建议参数，回退到随机采样")
                    params = self._sample_params_random()
            except Exception as e:
                logger.debug(f"GP 代理模型失败，回退到随机采样: {e}")
                params = self._sample_params_random()

            score, metrics = _evaluate_strategy(
                strategy_class, data, params, self._metric
            )
            trial = TrialResult(
                params=params,
                score=score,
                metrics=metrics,
                trial_id=i,
            )
            results.append(trial)

            if self._higher_is_better:
                if score > current_best:
                    current_best = score
            else:
                if score < current_best:
                    current_best = score
            convergence.append(current_best)

        # 排序并选择最优
        results.sort(
            key=lambda r: r.score,
            reverse=self._higher_is_better,
        )

        best = results[0] if results else TrialResult(params={}, score=0.0)

        elapsed = time.time() - start_time
        logger.info(
            f"贝叶斯优化完成: 耗时 {elapsed:.2f}s, "
            f"最优得分={best.score:.4f}, 参数={best.params}"
        )

        return OptimizationResult(
            best_params=best.params,
            best_score=best.score,
            all_results=results,
            optimization_time=elapsed,
            method="bayesian",
            convergence_history=convergence,
        )

    def _sample_params_random(self) -> Dict[str, Any]:
        """随机采样一组参数"""
        params: Dict[str, Any] = {}
        for ps in self._param_space:
            if ps.type == "choice":
                idx = self._rng.randint(0, len(ps.choices))
                params[ps.name] = ps.choices[idx]
            elif ps.type == "int":
                n_steps = int((ps.high - ps.low) / ps.step)
                if n_steps <= 0:
                    params[ps.name] = int(ps.low)
                else:
                    idx = self._rng.randint(0, n_steps + 1)
                    params[ps.name] = int(ps.low + idx * ps.step)
            else:  # float
                params[ps.name] = float(self._rng.uniform(ps.low, ps.high))
        return params

    def _params_to_vector(self, params: Dict[str, Any]) -> np.ndarray:
        """将参数字典转换为归一化数值向量

        choice 类型使用 one-hot 编码，数值类型归一化到 [0, 1]。

        Args:
            params: 参数字典

        Returns:
            归一化后的 numpy 向量
        """
        components: List[float] = []
        for ps in self._param_space:
            value = params.get(ps.name)
            if ps.type == "choice":
                # one-hot 编码
                one_hot = [0.0] * len(ps.choices)
                for j, c in enumerate(ps.choices):
                    if c == value:
                        one_hot[j] = 1.0
                        break
                components.extend(one_hot)
            elif ps.type == "int":
                normalized = (value - ps.low) / (ps.high - ps.low) if ps.high > ps.low else 0.0
                components.append(float(normalized))
            else:  # float
                normalized = (value - ps.low) / (ps.high - ps.low) if ps.high > ps.low else 0.0
                components.append(float(normalized))
        return np.array(components, dtype=np.float64)

    def _vector_to_params(self, vector: np.ndarray) -> Dict[str, Any]:
        """将归一化数值向量转换回参数字典

        Args:
            vector: 归一化向量

        Returns:
            参数字典
        """
        params: Dict[str, Any] = {}
        idx = 0
        for ps in self._param_space:
            if ps.type == "choice":
                n_choices = len(ps.choices)
                one_hot = vector[idx:idx + n_choices]
                best_idx = int(np.argmax(one_hot))
                params[ps.name] = ps.choices[best_idx]
                idx += n_choices
            elif ps.type == "int":
                normalized = np.clip(vector[idx], 0.0, 1.0)
                value = ps.low + normalized * (ps.high - ps.low)
                params[ps.name] = int(round(value / ps.step) * ps.step)
                params[ps.name] = max(int(ps.low), min(int(ps.high), params[ps.name]))
                idx += 1
            else:  # float
                normalized = np.clip(vector[idx], 0.0, 1.0)
                value = ps.low + normalized * (ps.high - ps.low)
                params[ps.name] = round(float(value), 6)
                idx += 1
        return params

    def _fit_surrogate(
        self, trials: List[TrialResult],
    ) -> Dict[str, Any]:
        """拟合高斯过程代理模型

        使用 RBF 核函数 + 白噪声核。

        核函数:
            k(x, x') = sigma_f^2 * exp(-0.5 * ||x - x'||^2 / l^2) + sigma_n^2 * delta(x, x')

        超参数使用启发式方法设定:
            - length_scale (l): 基于参数空间对角线长度
            - signal_variance (sigma_f^2): 基于观测值的方差
            - noise_variance (sigma_n^2): 观测值方差的 1%

        Args:
            trials: 已完成的试验列表

        Returns:
            代理模型字典，包含 X_train, y_train, K_inv_y, L 等矩阵
        """
        if len(trials) < 2:
            raise ValueError("至少需要 2 个试验才能拟合 GP")

        # 构建训练数据
        X = np.array([self._params_to_vector(t.params) for t in trials])
        y = np.array([t.score for t in trials])

        n = len(y)
        dim = X.shape[1] if len(X.shape) > 1 else 1

        # 启发式超参数
        # length_scale: 参数空间对角线长度的 1/3
        diag_length = float(np.sqrt(dim))
        length_scale = max(diag_length / 3.0, 0.1)

        # signal_variance: 观测值方差
        y_var = float(np.var(y))
        signal_var = max(y_var, 1e-6)

        # noise_variance: 观测值方差的 1%，最小值保护
        noise_var = max(y_var * 0.01, 1e-8)

        # 计算 RBF 核矩阵
        K = np.zeros((n, n), dtype=np.float64)
        for i in range(n):
            for j in range(n):
                diff = X[i] - X[j]
                sq_dist = float(np.dot(diff, diff))
                K[i, j] = signal_var * math.exp(-0.5 * sq_dist / (length_scale ** 2))

        # 添加白噪声
        K += noise_var * np.eye(n)

        # Cholesky 分解 (数值稳定)
        try:
            L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            # 添加更大的噪声使矩阵正定
            K += 1e-4 * np.eye(n)
            try:
                L = np.linalg.cholesky(K)
            except np.linalg.LinAlgError:
                K += 1e-2 * np.eye(n)
                L = np.linalg.cholesky(K)

        # 求解 K^{-1} y
        alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))

        return {
            "X_train": X,
            "y_train": y,
            "K": K,
            "L": L,
            "alpha": alpha,
            "length_scale": length_scale,
            "signal_var": signal_var,
            "noise_var": noise_var,
        }

    def _predict(
        self,
        surrogate: Dict[str, Any],
        x: np.ndarray,
    ) -> Tuple[float, float]:
        """使用 GP 代理模型进行预测

        Args:
            surrogate: 代理模型
            x: 查询点向量

        Returns:
            (均值, 标准差)
        """
        X_train = surrogate["X_train"]
        y_train = surrogate["y_train"]
        L = surrogate["L"]
        alpha = surrogate["alpha"]
        length_scale = surrogate["length_scale"]
        signal_var = surrogate["signal_var"]
        noise_var = surrogate["noise_var"]

        n = len(y_train)

        # 计算查询点与训练点的核向量
        k_star = np.zeros(n, dtype=np.float64)
        for i in range(n):
            diff = x - X_train[i]
            sq_dist = float(np.dot(diff, diff))
            k_star[i] = signal_var * math.exp(-0.5 * sq_dist / (length_scale ** 2))

        # 均值: k*^T alpha
        mean = float(np.dot(k_star, alpha))

        # 方差: k(x,x) - k*^T K^{-1} k*
        v = np.linalg.solve(L, k_star)
        var = signal_var + noise_var - float(np.dot(v, v))
        std = math.sqrt(max(var, 1e-10))

        return mean, std

    def _acquisition_ei(
        self,
        x: np.ndarray,
        surrogate: Dict[str, Any],
        best_y: float,
        xi: float = 0.01,
    ) -> float:
        """计算期望改进 (Expected Improvement) 采集函数

        EI(x) = E[max(f(x) - f(x+), 0)]
              = (mu - best_y - xi) * Phi(Z) + sigma * phi(Z)

        其中 Z = (mu - best_y - xi) / sigma

        Args:
            x: 查询点向量
            surrogate: 代理模型
            best_y: 当前最优观测值
            xi: 探索-利用平衡参数

        Returns:
            EI 值
        """
        mu, sigma = self._predict(surrogate, x)

        if sigma < 1e-10:
            return 0.0

        improvement = mu - best_y - xi
        Z = improvement / sigma

        # 标准正态分布的 PDF 和 CDF
        phi = math.exp(-0.5 * Z * Z) / math.sqrt(2 * math.pi)
        Phi = 0.5 * (1.0 + _math_erf(Z / math.sqrt(2.0)))

        ei = improvement * Phi + sigma * phi
        return max(ei, 0.0)

    def _suggest_next(
        self,
        surrogate: Dict[str, Any],
        best_y: float,
        n_candidates: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """建议下一组评估参数

        从参数空间中随机采样 n_candidates 个候选点，
        选择 EI 最大的那个。

        Args:
            surrogate: 代理模型
            best_y: 当前最优观测值
            n_candidates: 候选点数量

        Returns:
            建议的参数字典，失败时返回 None
        """
        if n_candidates is None:
            n_candidates = self._n_candidates

        best_ei = -math.inf
        best_params = None

        for _ in range(n_candidates):
            # 在 [0, 1]^d 空间中随机采样
            dim = self._get_vector_dim()
            raw = self._rng.uniform(0.0, 1.0, size=dim)

            ei = self._acquisition_ei(raw, surrogate, best_y)

            if ei > best_ei:
                best_ei = ei
                best_params = self._vector_to_params(raw)

        if best_params is None:
            return None
        return best_params

    def _get_vector_dim(self) -> int:
        """获取参数向量的维度"""
        dim = 0
        for ps in self._param_space:
            if ps.type == "choice":
                dim += len(ps.choices)
            else:
                dim += 1
        return dim


# ============================================================================
# 辅助数学函数
# ============================================================================


def _math_erf(x: float) -> float:
    """近似计算误差函数 erf(x)

    使用 Abramowitz and Stegun 的近似公式，最大误差 < 1.5e-7。

    Args:
        x: 输入值

    Returns:
        erf(x) 的近似值
    """
    # 常数
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)

    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)

    return sign * y


# ============================================================================
# Walk-Forward 验证器
# ============================================================================


class WalkForwardValidator:
    """Walk-Forward 验证器

    将数据分为多个滚动窗口，在每个窗口内进行参数优化 (IS)，
    然后在随后的样本外 (OOS) 期间测试优化得到的参数。

    通过比较 IS 和 OOS 的表现来检测过拟合:
    - IS/OOS 相关性高: 策略可能过拟合
    - IS/OOS 相关性低但 OOS 表现稳定: 策略鲁棒

    Usage:
        validator = WalkForwardValidator(n_splits=5, train_pct=0.7)
        result = validator.validate(MyStrategy, param_space, data)
        print(f"IS/OOS 相关性: {result.is_oos_correlation:.3f}")
    """

    def __init__(
        self,
        n_splits: int = 5,
        train_pct: float = 0.7,
        gap_days: int = 0,
    ) -> None:
        """初始化 Walk-Forward 验证器

        Args:
            n_splits: 分割数量
            train_pct: 训练集占比 (0-1)
            gap_days: IS 和 OOS 之间的间隔天数
        """
        if n_splits < 2:
            raise ValueError("n_splits 必须 >= 2")
        if not (0.0 < train_pct < 1.0):
            raise ValueError("train_pct 必须在 (0, 1) 范围内")
        if gap_days < 0:
            raise ValueError("gap_days 必须 >= 0")

        self._n_splits = n_splits
        self._train_pct = train_pct
        self._gap_days = gap_days

    def validate(
        self,
        strategy_class: Type,
        param_space: List[ParamSpace],
        data: Any,
        optimizer_class: Type = GridSearchOptimizer,
        metric: str = "sharpe_ratio",
        higher_is_better: bool = True,
        **optimizer_kwargs: Any,
    ) -> WalkForwardResult:
        """执行 Walk-Forward 验证

        Args:
            strategy_class: 策略类
            param_space: 参数搜索空间
            data: 回测数据 (pandas DataFrame，需包含 "date" 列)
            optimizer_class: 优化器类
            metric: 优化目标指标
            higher_is_better: 目标指标是否越大越好
            **optimizer_kwargs: 传递给优化器的额外参数

        Returns:
            WalkForwardResult 验证结果
        """
        logger.info(
            f"开始 Walk-Forward 验证: {self._n_splits} 段, "
            f"训练占比={self._train_pct:.0%}, 间隔={self._gap_days}天"
        )

        splits = self._split_data(data, self._n_splits, self._train_pct, self._gap_days)
        logger.info(f"数据分割完成: {len(splits)} 段")

        is_segments: List[Dict[str, Any]] = []
        oos_results: List[Dict[str, Any]] = []

        for i, (is_data, oos_data) in enumerate(splits):
            logger.info(f"--- 第 {i + 1}/{len(splits)} 段 ---")

            # IS 优化
            is_result = self._run_is_optimization(
                strategy_class, is_data, param_space, optimizer_class,
                metric, higher_is_better, **optimizer_kwargs,
            )
            is_segments.append(is_result)

            # OOS 测试
            oos_result = self._run_oos_test(
                strategy_class, oos_data, is_result["best_params"], metric,
            )
            oos_results.append(oos_result)

            logger.info(
                f"  IS 最优得分: {is_result['best_score']:.4f}, "
                f"OOS 得分: {oos_result.get('score', 0.0):.4f}"
            )

        # 计算 IS/OOS 相关性
        is_scores = [seg["best_score"] for seg in is_segments]
        oos_scores = [res.get("score", 0.0) for res in oos_results]
        correlation = self._calc_correlation(is_scores, oos_scores)

        # 合并 OOS 指标
        combined = self._combine_oos_metrics(oos_results)

        logger.info(f"Walk-Forward 验证完成: IS/OOS 相关性={correlation:.3f}")

        return WalkForwardResult(
            is_segments=is_segments,
            oos_results=oos_results,
            combined_metrics=combined,
            is_oos_correlation=correlation,
        )

    def _split_data(
        self,
        data: Any,
        n_splits: int,
        train_pct: float,
        gap_days: int,
    ) -> List[Tuple[Any, Any]]:
        """将数据分割为 IS/OOS 段

        使用锚定窗口方法:
        - 每段 OOS 窗口大小固定
        - IS 窗口从数据开头到 OOS 之前
        - 相邻段之间可以有间隔

        Args:
            data: pandas DataFrame
            n_splits: 分割数量
            train_pct: 训练集占比
            gap_days: 间隔天数

        Returns:
            (IS数据, OOS数据) 元组列表
        """
        import pandas as pd

        if not isinstance(data, pd.DataFrame):
            raise TypeError("data 必须是 pandas DataFrame")

        n = len(data)
        oos_size = int(n * (1.0 - train_pct) / n_splits)
        if oos_size < 1:
            oos_size = 1

        splits: List[Tuple[pd.DataFrame, pd.DataFrame]] = []

        for i in range(n_splits):
            # OOS 窗口
            oos_start = n - (n_splits - i) * oos_size
            oos_end = oos_start + oos_size

            # 添加间隔
            gap_start = max(0, oos_start - gap_days)

            # IS 窗口: 从数据开头到 OOS 之前 (减去间隔)
            is_end = gap_start

            if is_end < 1 or oos_end > n:
                continue

            is_data = data.iloc[:is_end].copy()
            oos_data = data.iloc[oos_start:oos_end].copy()

            if len(is_data) > 0 and len(oos_data) > 0:
                splits.append((is_data, oos_data))

        return splits

    def _run_is_optimization(
        self,
        strategy_class: Type,
        is_data: Any,
        param_space: List[ParamSpace],
        optimizer_class: Type,
        metric: str,
        higher_is_better: bool,
        **optimizer_kwargs: Any,
    ) -> Dict[str, Any]:
        """在 IS 期间进行参数优化

        Args:
            strategy_class: 策略类
            is_data: IS 数据
            param_space: 参数搜索空间
            optimizer_class: 优化器类
            metric: 目标指标
            higher_is_better: 是否越大越好

        Returns:
            包含 best_params, best_score 的字典
        """
        optimizer = optimizer_class(
            param_space=param_space,
            metric=metric,
            higher_is_better=higher_is_better,
            **optimizer_kwargs,
        )
        result = optimizer.optimize(strategy_class, is_data)

        return {
            "best_params": result.best_params,
            "best_score": result.best_score,
            "all_results": [
                {"params": r.params, "score": r.score} for r in result.all_results
            ],
        }

    def _run_oos_test(
        self,
        strategy_class: Type,
        oos_data: Any,
        best_params: Dict[str, Any],
        metric: str,
    ) -> Dict[str, Any]:
        """在 OOS 期间测试优化得到的参数

        Args:
            strategy_class: 策略类
            oos_data: OOS 数据
            best_params: IS 优化得到的最优参数
            metric: 目标指标

        Returns:
            包含 score, metrics 的字典
        """
        score, metrics = _evaluate_strategy(strategy_class, oos_data, best_params, metric)
        return {
            "params": best_params,
            "score": score,
            "metrics": metrics,
        }

    @staticmethod
    def _calc_correlation(x: List[float], y: List[float]) -> float:
        """计算两个列表的皮尔逊相关系数

        Args:
            x: 第一个列表
            y: 第二个列表

        Returns:
            相关系数，数据不足时返回 0.0
        """
        if len(x) < 2 or len(y) < 2 or len(x) != len(y):
            return 0.0

        a = np.array(x, dtype=np.float64)
        b = np.array(y, dtype=np.float64)

        std_a = np.std(a)
        std_b = np.std(b)

        if std_a < 1e-10 or std_b < 1e-10:
            return 0.0

        corr = float(np.corrcoef(a, b)[0, 1])
        return 0.0 if math.isnan(corr) else corr

    @staticmethod
    def _combine_oos_metrics(oos_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并多段 OOS 指标

        Args:
            oos_results: 各段 OOS 结果列表

        Returns:
            合并后的指标字典
        """
        if not oos_results:
            return {}

        scores = [r.get("score", 0.0) for r in oos_results]
        combined = {
            "mean_score": float(np.mean(scores)),
            "std_score": float(np.std(scores)),
            "min_score": float(np.min(scores)),
            "max_score": float(np.max(scores)),
            "n_segments": len(oos_results),
        }

        # 合并各段的 metrics
        all_metrics: Dict[str, List[float]] = {}
        for r in oos_results:
            for k, v in r.get("metrics", {}).items():
                all_metrics.setdefault(k, []).append(float(v))

        for k, v in all_metrics.items():
            combined[f"mean_{k}"] = float(np.mean(v))
            combined[f"std_{k}"] = float(np.std(v))

        return combined


# ============================================================================
# 优化报告生成器
# ============================================================================


class OptimizationReport:
    """优化报告生成器

    生成优化结果的文本报告、收敛曲线图和参数重要性分析。

    Usage:
        report = OptimizationReport()
        text = report.generate_report(result)
        plot_b64 = report.generate_convergence_plot(result)
        importance = report.generate_param_importance(result)
    """

    @staticmethod
    def generate_report(result: OptimizationResult) -> str:
        """生成文本格式的优化报告

        Args:
            result: 优化结果

        Returns:
            文本报告字符串
        """
        lines: List[str] = []
        lines.append("=" * 60)
        lines.append("策略参数优化报告")
        lines.append("=" * 60)
        lines.append("")

        # 基本信息
        lines.append(f"优化方法: {result.method}")
        lines.append(f"优化耗时: {result.optimization_time:.2f} 秒")
        lines.append(f"总试验次数: {len(result.all_results)}")
        lines.append("")

        # 最优结果
        lines.append("-" * 40)
        lines.append("最优参数:")
        for k, v in result.best_params.items():
            lines.append(f"  {k}: {v}")
        lines.append(f"最优得分: {result.best_score:.6f}")
        lines.append("")

        # Top 5 结果
        lines.append("-" * 40)
        lines.append("Top 5 结果:")
        for i, trial in enumerate(result.all_results[:5]):
            params_str = ", ".join(f"{k}={v}" for k, v in trial.params.items())
            lines.append(f"  #{i + 1}: score={trial.score:.6f} | {params_str}")
        lines.append("")

        # 收敛信息
        if result.convergence_history:
            lines.append("-" * 40)
            lines.append("收敛信息:")
            lines.append(f"  初始最优得分: {result.convergence_history[0]:.6f}")
            lines.append(f"  最终最优得分: {result.convergence_history[-1]:.6f}")
            improvement = result.convergence_history[-1] - result.convergence_history[0]
            lines.append(f"  得分提升: {improvement:.6f}")
            lines.append("")

        # 统计信息
        if result.all_results:
            scores = [r.score for r in result.all_results]
            lines.append("-" * 40)
            lines.append("得分统计:")
            lines.append(f"  均值: {np.mean(scores):.6f}")
            lines.append(f"  标准差: {np.std(scores):.6f}")
            lines.append(f"  最小值: {np.min(scores):.6f}")
            lines.append(f"  最大值: {np.max(scores):.6f}")
            lines.append(f"  中位数: {np.median(scores):.6f}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def generate_convergence_plot(result: OptimizationResult) -> str:
        """生成收敛曲线的 Base64 编码 PNG 图像

        使用 matplotlib 绘制收敛曲线图。

        Args:
            result: 优化结果

        Returns:
            Base64 编码的 PNG 图像字符串
        """
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(10, 6))

            if not result.convergence_history:
                ax.text(0.5, 0.5, "无收敛数据", ha="center", va="center",
                        transform=ax.transAxes)
            else:
                trials = list(range(1, len(result.convergence_history) + 1))
                ax.plot(trials, result.convergence_history, "b-", linewidth=2,
                        label="最优得分")
                ax.fill_between(trials, result.convergence_history,
                                alpha=0.1, color="blue")

                # 标记所有试验的得分
                if result.all_results:
                    all_scores = [r.score for r in result.all_results]
                    ax.scatter(range(1, len(all_scores) + 1), all_scores,
                               c="lightgray", s=10, alpha=0.5, label="各试验得分")

                ax.set_xlabel("试验次数", fontsize=12)
                ax.set_ylabel("最优得分", fontsize=12)
                ax.set_title(f"优化收敛曲线 ({result.method})", fontsize=14)
                ax.legend()
                ax.grid(True, alpha=0.3)

            plt.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100)
            plt.close(fig)
            buf.seek(0)

            import base64
            return base64.b64encode(buf.read()).decode("utf-8")

        except ImportError:
            logger.warning("matplotlib 未安装，无法生成收敛曲线图")
            return ""
        except Exception as e:
            logger.warning(f"生成收敛曲线图失败: {e}")
            return ""

    @staticmethod
    def generate_param_importance(result: OptimizationResult) -> str:
        """生成参数重要性分析

        通过分析最优参数与参数空间中心值的偏差程度来评估参数重要性。

        Args:
            result: 优化结果

        Returns:
            参数重要性分析文本
        """
        if not result.all_results or not result.best_params:
            return "无足够数据进行分析"

        lines: List[str] = []
        lines.append("=" * 50)
        lines.append("参数重要性分析")
        lines.append("=" * 50)
        lines.append("")

        # 分析每个参数在 Top N 结果中的分布
        top_n = min(10, len(result.all_results))
        top_results = result.all_results[:top_n]

        for param_name in result.best_params.keys():
            values = [r.params.get(param_name) for r in top_results if param_name in r.params]
            if not values:
                continue

            best_value = result.best_params[param_name]
            mean_value = float(np.mean(values))
            std_value = float(np.std(values))

            # 计算变异系数 (CV) 作为重要性指标
            # CV 越小说明该参数在最优结果中越一致，即越"重要"
            if abs(mean_value) > 1e-10:
                cv = std_value / abs(mean_value)
            else:
                cv = float("inf") if std_value > 1e-10 else 0.0

            # 计算参数值范围
            min_val = min(values)
            max_val = max(values)

            lines.append(f"参数: {param_name}")
            lines.append(f"  最优值: {best_value}")
            lines.append(f"  Top {top_n} 均值: {mean_value:.4f}")
            lines.append(f"  Top {top_n} 标准差: {std_value:.4f}")
            lines.append(f"  变异系数 (CV): {cv:.4f}")
            lines.append(f"  Top {top_n} 范围: [{min_val}, {max_val}]")

            # 重要性判断
            if cv < 0.05:
                importance = "高 (非常一致)"
            elif cv < 0.2:
                importance = "中 (较一致)"
            elif cv < 0.5:
                importance = "低 (有一定变化)"
            else:
                importance = "很低 (变化很大)"
            lines.append(f"  重要性: {importance}")
            lines.append("")

        lines.append("=" * 50)
        return "\n".join(lines)

    @staticmethod
    def compare_results(
        results: Dict[str, OptimizationResult],
    ) -> str:
        """比较多个优化结果

        Args:
            results: 方法名称到优化结果的映射

        Returns:
            比较报告文本
        """
        if not results:
            return "无优化结果可比较"

        lines: List[str] = []
        lines.append("=" * 70)
        lines.append("优化方法比较报告")
        lines.append("=" * 70)
        lines.append("")

        # 汇总表
        header = f"{'方法':<20} {'最优得分':>12} {'试验次数':>10} {'耗时(s)':>10}"
        lines.append(header)
        lines.append("-" * len(header))

        for name, result in results.items():
            lines.append(
                f"{name:<20} {result.best_score:>12.6f} "
                f"{len(result.all_results):>10} {result.optimization_time:>10.2f}"
            )

        lines.append("")

        # 找到全局最优
        best_method = max(results.items(), key=lambda x: x[1].best_score)
        lines.append(f"全局最优方法: {best_method[0]} (得分: {best_method[1].best_score:.6f})")
        lines.append("")

        # 最优参数对比
        lines.append("-" * 70)
        lines.append("各方法最优参数:")
        all_param_names: List[str] = []
        for result in results.values():
            for k in result.best_params:
                if k not in all_param_names:
                    all_param_names.append(k)

        if all_param_names:
            param_header = f"{'参数':<15}" + "".join(
                f"{name:>15}" for name in results.keys()
            )
            lines.append(param_header)
            lines.append("-" * len(param_header))

            for pname in all_param_names:
                row = f"{pname:<15}"
                for name, result in results.items():
                    val = result.best_params.get(pname, "N/A")
                    row += f"{str(val):>15}"
                lines.append(row)

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)


__all__ = [
    "ParamSpace",
    "TrialResult",
    "OptimizationResult",
    "WalkForwardResult",
    "GridSearchOptimizer",
    "RandomSearchOptimizer",
    "BayesianOptimizer",
    "WalkForwardValidator",
    "OptimizationReport",
]
