"""
策略验证器模块

在策略部署前进行全面的验证评估，包括:
- Walk-Forward 前向分析
- Monte Carlo 蒙特卡洛模拟 (1000次)
- 最低交易次数检查 (>= 100)
- 夏普比率阈值检查 (>= 0.5)
- 最大回撤限制检查 (<= 20%)
- Calmar 比率检查
- 与已有策略的相关性检查 (< 0.5)

输出结构化的 ValidationResult，包含通过/失败状态、各项评分和改进建议。
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, Field

from finhack_pro.strategies.base import BaseStrategy, Context, BarData, Signal
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 输出模型
# ---------------------------------------------------------------------------


class ValidationStatus(str, Enum):
    """验证状态"""
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"


class CheckResult(BaseModel):
    """单项检查结果

    Attributes:
        name: 检查项名称
        status: 通过/失败/警告
        value: 实际值
        threshold: 阈值
        message: 说明信息
    """
    name: str
    status: ValidationStatus
    value: float = 0.0
    threshold: float = 0.0
    message: str = ""


class ValidationResult(BaseModel):
    """策略验证结果

    Attributes:
        strategy_name: 策略名称
        passed: 是否通过验证 (所有核心检查通过)
        overall_score: 综合评分 (0-100)
        checks: 各项检查结果列表
        recommendations: 改进建议列表
        walk_forward_score: Walk-Forward 分析得分
        monte_carlo_metrics: Monte Carlo 模拟统计
        summary: 总结说明
    """
    strategy_name: str
    passed: bool = False
    overall_score: float = Field(ge=0.0, le=100.0, default=0.0)
    checks: List[CheckResult] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    walk_forward_score: float = 0.0
    monte_carlo_metrics: Dict[str, float] = Field(default_factory=dict)
    summary: str = ""


# ---------------------------------------------------------------------------
# 策略验证器
# ---------------------------------------------------------------------------


class StrategyValidator:
    """策略验证器

    在策略部署前进行全面的质量评估，确保策略满足最低标准。

    验证流程:
    1. 基础回测: 运行策略获取交易记录和权益曲线
    2. Walk-Forward 分析: 滚动窗口前向验证
    3. Monte Carlo 模拟: 1000次随机重采样评估稳健性
    4. 各项指标检查: 交易次数、夏普比率、最大回撤、Calmar比率
    5. 相关性检查: 与已有策略的收益相关性

    Usage:
        validator = StrategyValidator(
            min_trades=100,
            min_sharpe=0.5,
            max_drawdown=0.20,
            max_correlation=0.5,
        )
        result = validator.validate(strategy, bars, existing_strategies)
        if result.passed:
            deploy(strategy)
        else:
            print(result.recommendations)
    """

    def __init__(
        self,
        min_trades: int = 100,
        min_sharpe: float = 0.5,
        max_drawdown: float = 0.20,
        min_calmar: float = 0.3,
        max_correlation: float = 0.5,
        monte_carlo_runs: int = 1000,
        walk_forward_train_ratio: float = 0.7,
        walk_forward_n_splits: int = 5,
    ) -> None:
        """初始化策略验证器

        Args:
            min_trades: 最低交易次数阈值
            min_sharpe: 最低夏普比率阈值
            max_drawdown: 最大回撤限制 (如 0.20 表示 20%)
            min_calmar: 最低 Calmar 比率阈值
            max_correlation: 与已有策略的最大允许相关系数
            monte_carlo_runs: Monte Carlo 模拟次数
            walk_forward_train_ratio: Walk-Forward 训练集比例
            walk_forward_n_splits: Walk-Forward 分割次数
        """
        self._min_trades = min_trades
        self._min_sharpe = min_sharpe
        self._max_drawdown = max_drawdown
        self._min_calmar = min_calmar
        self._max_correlation = max_correlation
        self._monte_carlo_runs = monte_carlo_runs
        self._walk_forward_train_ratio = walk_forward_train_ratio
        self._walk_forward_n_splits = walk_forward_n_splits

        logger.info(
            f"策略验证器初始化: min_trades={self._min_trades}, "
            f"min_sharpe={self._min_sharpe}, max_drawdown={self._max_drawdown}, "
            f"min_calmar={self._min_calmar}, max_corr={self._max_correlation}, "
            f"mc_runs={self._monte_carlo_runs}"
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def validate(
        self,
        strategy: BaseStrategy,
        bars: List[BarData],
        existing_equity_curves: Optional[Dict[str, List[float]]] = None,
    ) -> ValidationResult:
        """验证策略是否满足部署标准

        Args:
            strategy: 待验证的策略实例
            bars: 历史K线数据
            existing_equity_curves: 已有策略的权益曲线字典
                {策略名称: 权益曲线列表}

        Returns:
            ValidationResult 验证结果
        """
        strategy_name = strategy.strategy_name
        logger.info(f"开始验证策略: {strategy_name}, K线数量: {len(bars)}")

        # 第一步: 运行基础回测
        trades, equity_curve = self._run_backtest(strategy, bars)
        logger.info(f"基础回测完成: 交易次数={len(trades)}")

        checks: List[CheckResult] = []
        recommendations: List[str] = []
        score_components: Dict[str, float] = {}

        # 第二步: Walk-Forward 分析
        wf_score, wf_check = self._check_walk_forward(strategy, bars)
        checks.append(wf_check)
        score_components["walk_forward"] = wf_score
        if wf_check.status == ValidationStatus.FAIL:
            recommendations.append(
                f"Walk-Forward 分析得分偏低 ({wf_score:.1%})，"
                "策略可能存在过拟合，建议简化参数或增加样本外数据"
            )

        # 第三步: Monte Carlo 模拟
        mc_metrics, mc_check = self._check_monte_carlo(equity_curve)
        checks.append(mc_check)
        score_components["monte_carlo"] = mc_metrics.get("pass_rate", 0.0)
        if mc_check.status == ValidationStatus.FAIL:
            recommendations.append(
                "Monte Carlo 模拟通过率不足，策略稳健性存疑，"
                "建议优化止损机制或降低仓位"
            )

        # 第四步: 各项指标检查
        # 4a. 交易次数
        trade_check = self._check_trade_count(trades)
        checks.append(trade_check)
        score_components["trade_count"] = trade_check.value
        if trade_check.status == ValidationStatus.FAIL:
            recommendations.append(
                f"交易次数不足 ({len(trades)} < {self._min_trades})，"
                "建议延长回测周期或降低信号触发阈值"
            )

        # 4b. 夏普比率
        sharpe = self._compute_sharpe(equity_curve)
        sharpe_check = self._check_sharpe(sharpe)
        checks.append(sharpe_check)
        score_components["sharpe"] = min(sharpe / max(self._min_sharpe, 1e-6), 1.0)
        if sharpe_check.status == ValidationStatus.FAIL:
            recommendations.append(
                f"夏普比率不达标 ({sharpe:.2f} < {self._min_sharpe})，"
                "建议优化入场/出场条件以提高风险调整后收益"
            )

        # 4c. 最大回撤
        max_dd = self._compute_max_drawdown(equity_curve)
        dd_check = self._check_drawdown(max_dd)
        checks.append(dd_check)
        score_components["drawdown"] = 1.0 - min(max_dd / max(self._max_drawdown, 1e-6), 1.0)
        if dd_check.status == ValidationStatus.FAIL:
            recommendations.append(
                f"最大回撤超标 ({max_dd:.1%} > {self._max_drawdown:.0%})，"
                "建议加强止损策略或降低单笔仓位"
            )

        # 4d. Calmar 比率
        annual_return = self._compute_annual_return(equity_curve, bars)
        calmar = annual_return / max_dd if max_dd > 1e-10 else 0.0
        calmar_check = self._check_calmar(calmar)
        checks.append(calmar_check)
        score_components["calmar"] = min(calmar / max(self._min_calmar, 1e-6), 1.0)
        if calmar_check.status == ValidationStatus.FAIL:
            recommendations.append(
                f"Calmar 比率偏低 ({calmar:.2f} < {self._min_calmar})，"
                "收益不足以补偿回撤风险，建议优化风险控制"
            )

        # 第五步: 相关性检查
        if existing_equity_curves:
            corr_check, corr_details = self._check_correlation(
                equity_curve, existing_equity_curves
            )
            checks.append(corr_check)
            score_components["correlation"] = 1.0 - min(
                corr_check.value / max(self._max_correlation, 1e-6), 1.0
            )
            if corr_check.status == ValidationStatus.FAIL:
                for name, corr_val in corr_details.items():
                    if corr_val >= self._max_correlation:
                        recommendations.append(
                            f"与策略 '{name}' 的收益相关性过高 "
                            f"({corr_val:.3f} >= {self._max_correlation})，"
                            "建议调整策略逻辑以增加差异化"
                        )

        # 计算综合评分
        overall_score = self._compute_overall_score(score_components)

        # 判断是否通过
        core_checks = checks[:6]  # 核心检查项
        passed = all(c.status != ValidationStatus.FAIL for c in core_checks)

        # 生成总结
        summary = self._build_summary(
            strategy_name=strategy_name,
            passed=passed,
            overall_score=overall_score,
            checks=checks,
            trade_count=len(trades),
            sharpe=sharpe,
            max_dd=max_dd,
            calmar=calmar,
        )

        result = ValidationResult(
            strategy_name=strategy_name,
            passed=passed,
            overall_score=round(overall_score, 2),
            checks=checks,
            recommendations=recommendations,
            walk_forward_score=round(wf_score, 4),
            monte_carlo_metrics=mc_metrics,
            summary=summary,
        )

        status_str = "通过" if passed else "未通过"
        logger.info(
            f"策略验证完成: {strategy_name} -> {status_str}, "
            f"综合评分={overall_score:.1f}"
        )

        return result

    # ------------------------------------------------------------------
    # 基础回测
    # ------------------------------------------------------------------

    def _run_backtest(
        self,
        strategy: BaseStrategy,
        bars: List[BarData],
        initial_capital: float = 1_000_000.0,
    ) -> Tuple[List[Dict[str, Any]], List[float]]:
        """运行基础回测，返回交易记录和权益曲线

        Args:
            strategy: 策略实例
            bars: K线数据
            initial_capital: 初始资金

        Returns:
            (交易记录列表, 权益曲线列表)
        """
        context = Context()
        strategy.on_init(context)

        trades: List[Dict[str, Any]] = []
        equity_curve: List[float] = [initial_capital]
        cash = initial_capital
        position: Dict[str, Dict[str, Any]] = {}  # symbol -> {volume, cost}

        for bar in bars:
            signals = strategy.on_bar(context, bar)

            for sig in signals:
                trade = {
                    "symbol": sig.symbol,
                    "direction": sig.direction.value if hasattr(sig.direction, 'value') else str(sig.direction),
                    "price": sig.price,
                    "volume": sig.volume,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "strategy": sig.strategy_name,
                    "timestamp": bar.datetime,
                }
                trades.append(trade)

                # 简化的权益更新
                if sig.direction.value == "buy" if hasattr(sig.direction, 'value') else str(sig.direction) == "buy":
                    cost = sig.price * sig.volume
                    if cost <= cash:
                        cash -= cost
                        pos = position.get(sig.symbol, {"volume": 0, "cost": 0.0})
                        total_cost = pos["cost"] * pos["volume"] + cost
                        total_volume = pos["volume"] + sig.volume
                        position[sig.symbol] = {
                            "volume": total_volume,
                            "cost": total_cost / max(total_volume, 1),
                        }
                elif sig.direction.value == "sell" if hasattr(sig.direction, 'value') else str(sig.direction) == "sell":
                    pos = position.get(sig.symbol, {"volume": 0, "cost": 0.0})
                    sell_volume = min(sig.volume, pos["volume"])
                    if sell_volume > 0:
                        cash += sig.price * sell_volume
                        pos["volume"] -= sell_volume
                        if pos["volume"] <= 0:
                            position.pop(sig.symbol, None)

            # 计算当前权益
            positions_value = 0.0
            for sym, pos in position.items():
                # 使用最新bar的价格估算持仓价值
                if bar.symbol == sym:
                    positions_value += pos["volume"] * bar.close
                else:
                    positions_value += pos["volume"] * pos["cost"]  # 回退使用成本价

            equity = cash + positions_value
            equity_curve.append(equity)

        return trades, equity_curve

    # ------------------------------------------------------------------
    # Walk-Forward 分析
    # ------------------------------------------------------------------

    def _check_walk_forward(
        self,
        strategy: BaseStrategy,
        bars: List[BarData],
    ) -> Tuple[float, CheckResult]:
        """Walk-Forward 前向分析

        将数据分割为多个训练/测试窗口，在训练集上初始化策略，
        在测试集上评估表现。最终得分为所有测试窗口表现的均值。

        Returns:
            (Walk-Forward 得分 0-1, 检查结果)
        """
        if len(bars) < 50:
            return 0.0, CheckResult(
                name="Walk-Forward 分析",
                status=ValidationStatus.WARNING,
                value=0.0,
                threshold=0.5,
                message=f"K线数据不足 ({len(bars)} 条)，无法进行有效的 Walk-Forward 分析",
            )

        n_splits = min(self._walk_forward_n_splits, max(1, len(bars) // 50))
        train_size = int(len(bars) * self._walk_forward_train_ratio)
        test_size = (len(bars) - train_size) // n_splits

        if test_size < 20:
            return 0.0, CheckResult(
                name="Walk-Forward 分析",
                status=ValidationStatus.WARNING,
                value=0.0,
                threshold=0.5,
                message="数据量不足以分割多个窗口，跳过 Walk-Forward 分析",
            )

        split_returns: List[float] = []

        for i in range(n_splits):
            train_end = train_size + i * test_size
            test_start = train_end
            test_end = min(test_start + test_size, len(bars))

            if test_end - test_start < 10:
                continue

            # 训练集初始化
            context = Context()
            strategy.on_init(context)

            # 在训练集上运行 (让策略积累状态)
            for bar in bars[:train_end]:
                strategy.on_bar(context, bar)

            # 在测试集上评估
            test_signals = 0
            profitable_signals = 0

            for bar in bars[test_start:test_end]:
                signals = strategy.on_bar(context, bar)
                test_signals += len(signals)
                # 简化: 假设买入信号在后续上涨为盈利
                for sig in signals:
                    direction = sig.direction.value if hasattr(sig.direction, 'value') else str(sig.direction)
                    if direction == "buy" and test_end < len(bars):
                        # 检查未来几根bar的价格变化
                        future_idx = min(test_end, bars.index(bar) + 5) if bar in bars else test_end
                        if future_idx < len(bars) and bars[future_idx].close > sig.price:
                            profitable_signals += 1

            if test_signals > 0:
                split_return = profitable_signals / test_signals
                split_returns.append(split_return)

        if not split_returns:
            return 0.0, CheckResult(
                name="Walk-Forward 分析",
                status=ValidationStatus.WARNING,
                value=0.0,
                threshold=0.5,
                message="Walk-Forward 分析未能产生有效结果",
            )

        wf_score = float(np.mean(split_returns))
        wf_std = float(np.std(split_returns))

        # 得分 > 0.5 且标准差 < 0.3 视为通过
        if wf_score >= 0.5 and wf_std < 0.3:
            status = ValidationStatus.PASS
        elif wf_score >= 0.3:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAIL

        return wf_score, CheckResult(
            name="Walk-Forward 分析",
            status=status,
            value=round(wf_score, 4),
            threshold=0.5,
            message=f"Walk-Forward 得分: {wf_score:.1%} (标准差: {wf_std:.1%}), "
                    f"分割数: {n_splits}",
        )

    # ------------------------------------------------------------------
    # Monte Carlo 模拟
    # ------------------------------------------------------------------

    def _check_monte_carlo(
        self,
        equity_curve: List[float],
    ) -> Tuple[Dict[str, float], CheckResult]:
        """Monte Carlo 蒙特卡洛模拟

        对交易收益进行随机重采样 (1000次)，评估策略的稳健性。

        统计指标:
        - pass_rate: 盈利模拟占比
        - mean_return: 平均最终收益
        - std_return: 收益标准差
        - worst_case: 最差 5% 分位
        - best_case: 最佳 5% 分位

        Returns:
            (Monte Carlo 统计字典, 检查结果)
        """
        if len(equity_curve) < 10:
            return {}, CheckResult(
                name="Monte Carlo 模拟",
                status=ValidationStatus.WARNING,
                value=0.0,
                threshold=0.6,
                message="权益曲线数据不足，无法进行 Monte Carlo 模拟",
            )

        # 计算日收益率序列
        returns = self._compute_returns(equity_curve)

        if len(returns) < 5:
            return {}, CheckResult(
                name="Monte Carlo 模拟",
                status=ValidationStatus.WARNING,
                value=0.0,
                threshold=0.6,
                message="收益率序列过短，无法进行 Monte Carlo 模拟",
            )

        n_simulations = self._monte_carlo_runs
        n_periods = len(returns)
        final_returns: List[float] = []

        rng = np.random.default_rng(42)

        for _ in range(n_simulations):
            # 随机有放回采样收益率
            sampled_returns = rng.choice(returns, size=n_periods, replace=True)
            # 计算累积收益
            cumulative = np.prod(1.0 + sampled_returns) - 1.0
            final_returns.append(cumulative)

        final_returns = np.array(final_returns)

        # 统计指标
        pass_rate = float(np.mean(final_returns > 0))
        mean_return = float(np.mean(final_returns))
        std_return = float(np.std(final_returns))
        worst_case = float(np.percentile(final_returns, 5))
        best_case = float(np.percentile(final_returns, 95))
        median_return = float(np.median(final_returns))

        metrics: Dict[str, float] = {
            "pass_rate": round(pass_rate, 4),
            "mean_return": round(mean_return, 4),
            "std_return": round(std_return, 4),
            "worst_case_5pct": round(worst_case, 4),
            "best_case_95pct": round(best_case, 4),
            "median_return": round(median_return, 4),
            "n_simulations": float(n_simulations),
        }

        # 通过标准: 盈利模拟占比 >= 60%
        if pass_rate >= 0.6:
            status = ValidationStatus.PASS
        elif pass_rate >= 0.5:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAIL

        return metrics, CheckResult(
            name="Monte Carlo 模拟",
            status=status,
            value=round(pass_rate, 4),
            threshold=0.6,
            message=(
                f"模拟 {n_simulations} 次, 盈利占比: {pass_rate:.1%}, "
                f"平均收益: {mean_return:.1%}, 中位数: {median_return:.1%}, "
                f"最差5%: {worst_case:.1%}, 最佳5%: {best_case:.1%}"
            ),
        )

    # ------------------------------------------------------------------
    # 各项指标检查
    # ------------------------------------------------------------------

    def _check_trade_count(self, trades: List[Dict[str, Any]]) -> CheckResult:
        """检查交易次数是否满足最低要求"""
        count = len(trades)
        if count >= self._min_trades:
            status = ValidationStatus.PASS
        elif count >= self._min_trades * 0.5:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAIL

        return CheckResult(
            name="交易次数",
            status=status,
            value=float(count),
            threshold=float(self._min_trades),
            message=f"交易次数: {count}, 最低要求: {self._min_trades}",
        )

    def _check_sharpe(self, sharpe: float) -> CheckResult:
        """检查夏普比率是否满足阈值"""
        if sharpe >= self._min_sharpe:
            status = ValidationStatus.PASS
        elif sharpe >= self._min_sharpe * 0.7:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAIL

        return CheckResult(
            name="夏普比率",
            status=status,
            value=round(sharpe, 4),
            threshold=self._min_sharpe,
            message=f"夏普比率: {sharpe:.2f}, 最低要求: {self._min_sharpe:.2f}",
        )

    def _check_drawdown(self, max_dd: float) -> CheckResult:
        """检查最大回撤是否在限制范围内"""
        if max_dd <= self._max_drawdown:
            status = ValidationStatus.PASS
        elif max_dd <= self._max_drawdown * 1.3:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAIL

        return CheckResult(
            name="最大回撤",
            status=status,
            value=round(max_dd, 4),
            threshold=self._max_drawdown,
            message=f"最大回撤: {max_dd:.1%}, 限制: {self._max_drawdown:.0%}",
        )

    def _check_calmar(self, calmar: float) -> CheckResult:
        """检查 Calmar 比率是否满足阈值"""
        if calmar >= self._min_calmar:
            status = ValidationStatus.PASS
        elif calmar >= self._min_calmar * 0.5:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAIL

        return CheckResult(
            name="Calmar 比率",
            status=status,
            value=round(calmar, 4),
            threshold=self._min_calmar,
            message=f"Calmar 比率: {calmar:.2f}, 最低要求: {self._min_calmar:.2f}",
        )

    def _check_correlation(
        self,
        equity_curve: List[float],
        existing_curves: Dict[str, List[float]],
    ) -> Tuple[CheckResult, Dict[str, float]]:
        """检查与已有策略的收益相关性

        Args:
            equity_curve: 当前策略的权益曲线
            existing_curves: 已有策略的权益曲线字典

        Returns:
            (检查结果, 各策略相关系数详情)
        """
        if not existing_curves:
            return CheckResult(
                name="策略相关性",
                status=ValidationStatus.PASS,
                value=0.0,
                threshold=self._max_correlation,
                message="无已有策略，跳过相关性检查",
            ), {}

        new_returns = self._compute_returns(equity_curve)
        if len(new_returns) < 10:
            return CheckResult(
                name="策略相关性",
                status=ValidationStatus.WARNING,
                value=0.0,
                threshold=self._max_correlation,
                message="收益率序列过短，无法计算相关性",
            ), {}

        correlations: Dict[str, float] = {}
        max_corr = 0.0
        max_corr_name = ""

        for name, curve in existing_curves.items():
            existing_returns = self._compute_returns(curve)
            if len(existing_returns) < 10:
                continue

            # 对齐长度
            min_len = min(len(new_returns), len(existing_returns))
            corr = float(np.corrcoef(
                new_returns[:min_len],
                existing_returns[:min_len],
            )[0, 1])

            # 处理 NaN
            if math.isnan(corr):
                corr = 0.0

            correlations[name] = corr

            if abs(corr) > abs(max_corr):
                max_corr = corr
                max_corr_name = name

        if not correlations:
            return CheckResult(
                name="策略相关性",
                status=ValidationStatus.PASS,
                value=0.0,
                threshold=self._max_correlation,
                message="无有效对比数据",
            ), {}

        if abs(max_corr) < self._max_correlation:
            status = ValidationStatus.PASS
        elif abs(max_corr) < self._max_correlation * 1.2:
            status = ValidationStatus.WARNING
        else:
            status = ValidationStatus.FAIL

        corr_summary = ", ".join(
            f"{name}={corr:.3f}" for name, corr in correlations.items()
        )

        return CheckResult(
            name="策略相关性",
            status=status,
            value=round(abs(max_corr), 4),
            threshold=self._max_correlation,
            message=f"最高相关: {max_corr_name} ({max_corr:.3f}), 全部: [{corr_summary}]",
        ), correlations

    # ------------------------------------------------------------------
    # 指标计算工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_returns(equity_curve: List[float]) -> np.ndarray:
        """计算日收益率序列

        Args:
            equity_curve: 权益曲线

        Returns:
            收益率数组
        """
        if len(equity_curve) < 2:
            return np.array([])

        arr = np.array(equity_curve, dtype=np.float64)
        returns = np.diff(arr) / np.maximum(np.abs(arr[:-1]), 1e-10)
        return returns

    def _compute_sharpe(
        self,
        equity_curve: List[float],
        risk_free_rate: float = 0.03,
        periods_per_year: int = 252,
    ) -> float:
        """计算年化夏普比率

        Args:
            equity_curve: 权益曲线
            risk_free_rate: 无风险利率 (年化)
            periods_per_year: 每年交易天数

        Returns:
            年化夏普比率
        """
        returns = self._compute_returns(equity_curve)
        if len(returns) < 2:
            return 0.0

        # 过滤极端值
        returns = np.clip(returns, -1.0, 1.0)

        mean_return = float(np.mean(returns))
        std_return = float(np.std(returns))

        if std_return < 1e-10:
            return 0.0

        # 年化
        annual_mean = mean_return * periods_per_year
        annual_std = std_return * math.sqrt(periods_per_year)
        daily_rf = risk_free_rate / periods_per_year

        sharpe = (annual_mean - risk_free_rate) / annual_std
        return sharpe

    @staticmethod
    def _compute_max_drawdown(equity_curve: List[float]) -> float:
        """计算最大回撤

        Args:
            equity_curve: 权益曲线

        Returns:
            最大回撤比例 (正数，如 0.15 表示 15%)
        """
        if len(equity_curve) < 2:
            return 0.0

        arr = np.array(equity_curve, dtype=np.float64)
        peak = np.maximum.accumulate(arr)
        drawdown = (peak - arr) / np.maximum(peak, 1e-10)

        return float(np.max(drawdown))

    @staticmethod
    def _compute_annual_return(
        equity_curve: List[float],
        bars: List[BarData],
    ) -> float:
        """计算年化收益率

        Args:
            equity_curve: 权益曲线
            bars: K线数据 (用于计算时间跨度)

        Returns:
            年化收益率
        """
        if len(equity_curve) < 2 or not bars:
            return 0.0

        initial = equity_curve[0]
        final = equity_curve[-1]

        if initial <= 0:
            return 0.0

        total_return = (final - initial) / initial

        # 估算时间跨度 (天数)
        first_dt = bars[0].datetime
        last_dt = bars[-1].datetime
        total_days = max((last_dt - first_dt).days, 1)

        # 年化
        years = total_days / 365.25
        if years < 1e-6:
            return 0.0

        annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0
        return annual_return

    # ------------------------------------------------------------------
    # 综合评分与总结
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_overall_score(components: Dict[str, float]) -> float:
        """计算综合评分 (0-100)

        各组件权重:
        - walk_forward: 20%
        - monte_carlo: 15%
        - trade_count: 10%
        - sharpe: 20%
        - drawdown: 20%
        - calmar: 10%
        - correlation: 5% (如有)

        每个组件值应在 [0, 1] 范围内。
        """
        weights = {
            "walk_forward": 0.20,
            "monte_carlo": 0.15,
            "trade_count": 0.10,
            "sharpe": 0.20,
            "drawdown": 0.20,
            "calmar": 0.10,
            "correlation": 0.05,
        }

        total_weight = 0.0
        weighted_sum = 0.0

        for key, weight in weights.items():
            if key in components:
                value = max(0.0, min(1.0, components[key]))
                weighted_sum += value * weight
                total_weight += weight

        if total_weight < 1e-10:
            return 0.0

        # 归一化到 0-100
        score = (weighted_sum / total_weight) * 100.0
        return max(0.0, min(100.0, score))

    @staticmethod
    def _build_summary(
        strategy_name: str,
        passed: bool,
        overall_score: float,
        checks: List[CheckResult],
        trade_count: int,
        sharpe: float,
        max_dd: float,
        calmar: float,
    ) -> str:
        """构建验证总结

        生成人类可读的中文验证报告摘要。
        """
        status_str = "通过" if passed else "未通过"
        lines = [
            f"策略 '{strategy_name}' 验证{status_str}",
            f"综合评分: {overall_score:.1f}/100",
            f"核心指标: 交易次数={trade_count}, 夏普比率={sharpe:.2f}, "
            f"最大回撤={max_dd:.1%}, Calmar比率={calmar:.2f}",
            "",
            "各项检查:",
        ]

        for check in checks:
            status_cn = {
                ValidationStatus.PASS: "通过",
                ValidationStatus.FAIL: "未通过",
                ValidationStatus.WARNING: "警告",
            }
            lines.append(f"  [{status_cn[check.status]}] {check.name}: {check.message}")

        if not passed:
            lines.append("")
            lines.append("建议: 请根据上述未通过项进行策略优化后重新验证。")

        return "\n".join(lines)


__all__ = [
    "StrategyValidator",
    "ValidationResult",
    "ValidationStatus",
    "CheckResult",
]
