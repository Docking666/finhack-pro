"""
策略验证框架模块

在策略部署前进行严格验证，防止过拟合。包含:
- Walk-Forward 分析
- Monte Carlo 模拟 (1000次)
- 最低交易次数检查 (>= 100)
- 夏普比率门槛 (>= 0.5)
- 最大回撤限制 (<= 20%)
- Calmar 比率检查
- 与现有策略的相关性检查 (< 0.5)

优化:
- 支持配置文件驱动参数
- 支持不同策略类型的验证配置
"""

from __future__ import annotations

import math
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, ClassVar

import numpy as np
from pydantic import BaseModel, Field

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# 预定义验证配置
VALIDATION_PROFILES: Dict[str, Dict[str, Any]] = {
    "default": {
        "min_trades": 100,
        "min_sharpe": 0.5,
        "max_drawdown": 0.20,
        "min_calmar": 0.3,
        "max_correlation": 0.5,
        "monte_carlo_runs": 1000,
        "walk_forward_windows": 5,
    },
    # 保守型：更严格的验证
    "conservative": {
        "min_trades": 200,
        "min_sharpe": 1.0,
        "max_drawdown": 0.15,
        "min_calmar": 0.5,
        "max_correlation": 0.3,
        "monte_carlo_runs": 2000,
        "walk_forward_windows": 8,
    },
    # 激进型：更宽松的验证
    "aggressive": {
        "min_trades": 50,
        "min_sharpe": 0.3,
        "max_drawdown": 0.30,
        "min_calmar": 0.2,
        "max_correlation": 0.7,
        "monte_carlo_runs": 500,
        "walk_forward_windows": 3,
    },
    # 高频策略：更多交易次数要求
    "high_frequency": {
        "min_trades": 500,
        "min_sharpe": 0.3,
        "max_drawdown": 0.10,
        "min_calmar": 0.2,
        "max_correlation": 0.5,
        "monte_carlo_runs": 2000,
        "walk_forward_windows": 10,
    },
    # 低频策略：更少交易次数要求
    "low_frequency": {
        "min_trades": 30,
        "min_sharpe": 1.0,
        "max_drawdown": 0.25,
        "min_calmar": 0.5,
        "max_correlation": 0.5,
        "monte_carlo_runs": 500,
        "walk_forward_windows": 3,
    },
}


class ValidationResult(BaseModel):
    """策略验证结果
    
    Attributes:
        passed: 是否通过验证
        overall_score: 综合评分 (0-100)
        checks: 各项检查结果
        recommendations: 改进建议
        walk_forward_score: Walk-Forward得分
        monte_carlo_metrics: Monte Carlo模拟指标
        summary: 总结
        profile_used: 使用的验证配置名称
    """
    passed: bool = False
    overall_score: float = 0.0
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    walk_forward_score: float = 0.0
    monte_carlo_metrics: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    profile_used: str = "default"


@dataclass
class _StrategyPerformance:
    """策略历史表现数据"""
    returns: List[float] = field(default_factory=list)
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    annual_return: float = 0.0
    volatility: float = 0.0


class StrategyValidator:
    """策略验证器
    
    在策略部署前进行严格验证，防止过拟合。
    
    支持配置方式:
    1. 直接参数: StrategyValidator(min_trades=200, ...)
    2. 预定义配置: StrategyValidator.from_profile("conservative")
    3. 配置文件: StrategyValidator.from_config("config/validation.yaml")
    4. 配置字典: StrategyValidator.from_config_dict({...})
    
    Usage:
        # 使用默认配置
        validator = StrategyValidator()
        
        # 使用预定义配置
        validator = StrategyValidator.from_profile("conservative")
        
        # 自定义配置
        validator = StrategyValidator(
            min_trades=200,
            min_sharpe=1.0,
            max_drawdown=0.15,
        )
        
        result = validator.validate(
            strategy_performance={
                'returns': [0.01, -0.02, 0.03, ...],
                'sharpe_ratio': 1.2,
                'max_drawdown': 0.15,
                'total_trades': 200,
            },
            existing_strategies_returns={
                'momentum': [0.005, -0.01, ...],
            },
        )
        if result.passed:
            print('策略验证通过!')
    """
    
    # 可用配置名称
    AVAILABLE_PROFILES: ClassVar[List[str]] = list(VALIDATION_PROFILES.keys())
    
    def __init__(
        self,
        min_trades: int = 100,
        min_sharpe: float = 0.5,
        max_drawdown: float = 0.20,
        min_calmar: float = 0.3,
        max_correlation: float = 0.5,
        monte_carlo_runs: int = 1000,
        walk_forward_windows: int = 5,
        profile: str = "default",
    ) -> None:
        """初始化策略验证器
        
        Args:
            min_trades: 最低交易次数
            min_sharpe: 最低夏普比率
            max_drawdown: 最大回撤限制
            min_calmar: 最低Calmar比率
            max_correlation: 最大策略相关性
            monte_carlo_runs: Monte Carlo模拟次数
            walk_forward_windows: Walk-Forward窗口数
            profile: 配置名称（用于结果记录）
        """
        self.min_trades = min_trades
        self.min_sharpe = min_sharpe
        self.max_drawdown = max_drawdown
        self.min_calmar = min_calmar
        self.max_correlation = max_correlation
        self.monte_carlo_runs = monte_carlo_runs
        self.walk_forward_windows = walk_forward_windows
        self.profile = profile
    
    @classmethod
    def from_profile(cls, profile_name: str = "default") -> "StrategyValidator":
        """从预定义配置创建验证器
        
        Args:
            profile_name: 配置名称，可选值: default, conservative, aggressive, high_frequency, low_frequency
            
        Returns:
            StrategyValidator 实例
        """
        if profile_name not in VALIDATION_PROFILES:
            logger.warning(f"未知配置 '{profile_name}'，使用默认配置")
            profile_name = "default"
        
        config = VALIDATION_PROFILES[profile_name].copy()
        config["profile"] = profile_name
        return cls(**config)
    
    @classmethod
    def from_config(cls, config_path: str) -> "StrategyValidator":
        """从配置文件创建验证器
        
        Args:
            config_path: 配置文件路径 (YAML格式)
            
        Returns:
            StrategyValidator 实例
        """
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"配置文件不存在: {config_path}，使用默认配置")
            return cls()
        
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        
        return cls.from_config_dict(config)
    
    @classmethod
    def from_config_dict(cls, config: Dict[str, Any]) -> "StrategyValidator":
        """从配置字典创建验证器
        
        Args:
            config: 配置字典，支持字段:
                - profile: 预定义配置名称（可选）
                - min_trades, min_sharpe, max_drawdown, ... （覆盖预定义配置）
            
        Returns:
            StrategyValidator 实例
        """
        # 如果指定了 profile，先加载预定义配置
        profile_name = config.pop("profile", "default")
        
        if profile_name in VALIDATION_PROFILES:
            base_config = VALIDATION_PROFILES[profile_name].copy()
        else:
            base_config = VALIDATION_PROFILES["default"].copy()
        
        # 用自定义配置覆盖
        base_config.update(config)
        base_config["profile"] = profile_name
        
        return cls(**base_config)
    
    def get_config(self) -> Dict[str, Any]:
        """获取当前验证配置"""
        return {
            "profile": self.profile,
            "min_trades": self.min_trades,
            "min_sharpe": self.min_sharpe,
            "max_drawdown": self.max_drawdown,
            "min_calmar": self.min_calmar,
            "max_correlation": self.max_correlation,
            "monte_carlo_runs": self.monte_carlo_runs,
            "walk_forward_windows": self.walk_forward_windows,
        }
        
    def validate(
        self,
        strategy_performance: Dict[str, Any],
        existing_strategies_returns: Optional[Dict[str, List[float]]] = None,
    ) -> ValidationResult:
        """执行完整的策略验证
        
        Args:
            strategy_performance: 策略历史表现数据，包含:
                - returns: 收益率列表
                - sharpe_ratio: 夏普比率 (可选，会自动计算)
                - max_drawdown: 最大回撤 (可选，会自动计算)
                - total_trades: 总交易次数 (可选)
                - annual_return: 年化收益 (可选)
                - volatility: 年化波动率 (可选)
            existing_strategies_returns: 已有策略的收益率字典 (可选)
            
        Returns:
            ValidationResult 验证结果
        """
        logger.info("开始策略验证...")
        
        # 解析输入数据
        perf = self._parse_performance(strategy_performance)
        
        # 执行各项检查
        checks = []
        recommendations = []
        score_components = []
        
        # 检查1: 最低交易次数
        check = self._check_min_trades(perf)
        checks.append(check)
        score_components.append(check["score"])
        if not check["passed"]:
            recommendations.append(
                f"交易次数({perf.total_trades})不足，建议至少{self.min_trades}次。"
                "增加回测时间跨度或降低交易门槛。"
            )
        
        # 检查2: 夏普比率
        check = self._check_sharpe(perf)
        checks.append(check)
        score_components.append(check["score"])
        if not check["passed"]:
            recommendations.append(
                f"夏普比率({perf.sharpe_ratio:.2f})低于门槛({self.min_sharpe})。"
                "考虑优化入场/出场条件，或增加风控规则。"
            )
        
        # 检查3: 最大回撤
        check = self._check_max_drawdown(perf)
        checks.append(check)
        score_components.append(check["score"])
        if not check["passed"]:
            recommendations.append(
                f"最大回撤({perf.max_drawdown:.2%})超过限制({self.max_drawdown:.2%})。"
                "建议收紧止损或降低仓位。"
            )
        
        # 检查4: Calmar比率
        check = self._check_calmar(perf)
        checks.append(check)
        score_components.append(check["score"])
        if not check["passed"]:
            recommendations.append(
                f"Calmar比率({perf.calmar_ratio:.2f})偏低。"
                "风险收益比不佳，需提升收益或控制回撤。"
            )
        
        # 检查5: Walk-Forward分析
        wf_score = self._check_walk_forward(perf)
        checks.append({
            "name": "Walk-Forward分析",
            "passed": wf_score > 0.5,
            "score": min(wf_score * 100, 100),
            "detail": f"WF得分={wf_score:.2f}",
        })
        score_components.append(min(wf_score * 100, 100))
        
        # 检查6: Monte Carlo模拟
        mc_metrics = self._check_monte_carlo(perf)
        mc_passed = mc_metrics.get("profitable_ratio", 0) >= 0.6
        checks.append({
            "name": "Monte Carlo模拟",
            "passed": mc_passed,
            "score": mc_metrics.get("profitable_ratio", 0) * 100,
            "detail": f"盈利占比={mc_metrics.get('profitable_ratio', 0):.2%}",
        })
        score_components.append(mc_metrics.get("profitable_ratio", 0) * 100)
        
        # 检查7: 与现有策略的相关性
        if existing_strategies_returns:
            check = self._check_correlation(perf, existing_strategies_returns)
            checks.append(check)
            score_components.append(check["score"])
            if not check["passed"]:
                recommendations.append(
                    f"与现有策略相关性过高({check['detail']})。"
                    "建议调整策略逻辑以降低相关性。"
                )
        
        # 计算综合评分
        overall_score = sum(score_components) / len(score_components) if score_components else 0
        
        # 判断是否通过 (所有核心检查通过)
        core_checks = checks[:4]  # 交易次数、夏普、回撤、Calmar
        passed = all(c["passed"] for c in core_checks)
        
        # 生成总结
        passed_count = sum(1 for c in checks if c["passed"])
        total_count = len(checks)
        summary = (
            f"策略验证{'通过' if passed else '未通过'}: "
            f"综合评分={overall_score:.1f}/100, "
            f"通过{passed_count}/{total_count}项检查"
        )
        if recommendations:
            summary += f", {len(recommendations)}项改进建议"
        
        logger.info(summary)
        
        return ValidationResult(
            passed=passed,
            overall_score=overall_score,
            checks=checks,
            recommendations=recommendations,
            walk_forward_score=wf_score,
            monte_carlo_metrics=mc_metrics,
            summary=summary,
            profile_used=self.profile,
        )
    
    def _parse_performance(self, data: Dict[str, Any]) -> _StrategyPerformance:
        """解析策略表现数据"""
        returns = data.get("returns", [])
        returns = [float(r) for r in returns]
        
        perf = _StrategyPerformance(returns=returns)
        
        if returns:
            perf.total_trades = data.get("total_trades", len(returns))
            perf.volatility = data.get("volatility", self._calc_volatility(returns))
            perf.annual_return = data.get("annual_return", self._calc_annual_return(returns))
            perf.max_drawdown = data.get("max_drawdown", self._calc_max_drawdown(returns))
            perf.sharpe_ratio = data.get("sharpe_ratio", self._calc_sharpe(returns))
            perf.calmar_ratio = data.get("calmar_ratio", self._calc_calmar(perf))
            perf.win_rate = data.get("win_rate", sum(1 for r in returns if r > 0) / len(returns))
        
        return perf
    
    def _check_min_trades(self, perf: _StrategyPerformance) -> Dict[str, Any]:
        """检查最低交易次数"""
        passed = perf.total_trades >= self.min_trades
        score = min(perf.total_trades / self.min_trades, 1.0) * 100
        return {
            "name": f"最低交易次数(>={self.min_trades})",
            "passed": passed,
            "score": score,
            "detail": f"交易次数={perf.total_trades}",
        }
    
    def _check_sharpe(self, perf: _StrategyPerformance) -> Dict[str, Any]:
        """检查夏普比率"""
        passed = perf.sharpe_ratio >= self.min_sharpe
        score = min(perf.sharpe_ratio / self.min_sharpe, 1.0) * 100
        return {
            "name": f"夏普比率(>={self.min_sharpe})",
            "passed": passed,
            "score": score,
            "detail": f"夏普比率={perf.sharpe_ratio:.2f}",
        }
    
    def _check_max_drawdown(self, perf: _StrategyPerformance) -> Dict[str, Any]:
        """检查最大回撤"""
        passed = perf.max_drawdown <= self.max_drawdown
        score = min((1 - perf.max_drawdown / self.max_drawdown), 1.0) * 100 if self.max_drawdown > 0 else 100
        return {
            "name": f"最大回撤(<={self.max_drawdown:.2%})",
            "passed": passed,
            "score": max(score, 0),
            "detail": f"最大回撤={perf.max_drawdown:.2%}",
        }
    
    def _check_calmar(self, perf: _StrategyPerformance) -> Dict[str, Any]:
        """检查Calmar比率"""
        passed = perf.calmar_ratio >= self.min_calmar
        score = min(perf.calmar_ratio / self.min_calmar, 1.0) * 100 if self.min_calmar > 0 else 100
        return {
            "name": f"Calmar比率(>={self.min_calmar})",
            "passed": passed,
            "score": score,
            "detail": f"Calmar比率={perf.calmar_ratio:.2f}",
        }
    
    def _check_walk_forward(self, perf: _StrategyPerformance) -> float:
        """Walk-Forward分析
        
        将收益率序列分为多个窗口，依次作为训练/测试集，
        验证策略在不同市场环境下的稳定性。
        
        Returns:
            WF得分 (0-1)，>0.5视为通过
        """
        returns = perf.returns
        n = len(returns)
        if n < 50:
            logger.warning("数据量不足，跳过Walk-Forward分析")
            return 0.5
        
        window_size = n // (self.walk_forward_windows + 1)
        if window_size < 10:
            window_size = 10
        
        scores = []
        for i in range(self.walk_forward_windows):
            train_end = window_size * (i + 1)
            test_end = min(train_end + window_size, n)
            
            if test_end <= train_end or test_end > n:
                break
            
            train_returns = returns[:train_end]
            test_returns = returns[train_end:test_end]
            
            # 训练集的均值和标准差
            train_mean = np.mean(train_returns)
            train_std = np.std(train_returns) if np.std(train_returns) > 0 else 1e-8
            
            # 测试集表现
            test_mean = np.mean(test_returns)
            
            # 得分: 测试集收益与训练集收益的一致性
            if train_std > 0:
                score = 1.0 / (1.0 + abs(test_mean - train_mean) / train_std)
            else:
                score = 0.5
            scores.append(score)
        
        if not scores:
            return 0.5
        
        return float(np.mean(scores))
    
    def _check_monte_carlo(self, perf: _StrategyPerformance) -> Dict[str, Any]:
        """Monte Carlo模拟
        
        对收益率序列进行随机有放回重采样，统计盈利占比。
        
        Returns:
            模拟指标字典
        """
        returns = perf.returns
        n = len(returns)
        if n < 10:
            return {"profitable_ratio": 0.5, "avg_return": 0.0}
        
        simulation_results = []
        for _ in range(self.monte_carlo_runs):
            # 随机有放回采样
            sample = np.random.choice(returns, size=n, replace=True)
            cumulative = np.cumprod(1 + np.array(sample))
            total_return = cumulative[-1] - 1
            simulation_results.append(total_return)
        
        results = np.array(simulation_results)
        
        return {
            "profitable_ratio": float(np.mean(results > 0)),
            "avg_return": float(np.mean(results)),
            "std_return": float(np.std(results)),
            "percentile_5": float(np.percentile(results, 5)),
            "percentile_95": float(np.percentile(results, 95)),
            "runs": self.monte_carlo_runs,
        }
    
    def _check_correlation(
        self,
        perf: _StrategyPerformance,
        existing_returns: Dict[str, List[float]],
    ) -> Dict[str, Any]:
        """检查与现有策略的相关性"""
        if not perf.returns:
            return {
                "name": f"策略相关性(<{self.max_correlation})",
                "passed": True,
                "score": 100,
                "detail": "无收益数据",
            }
        
        max_corr = 0.0
        worst_strategy = ""
        
        for name, other_returns in existing_returns.items():
            if not other_returns:
                continue
            
            # 对齐长度
            min_len = min(len(perf.returns), len(other_returns))
            if min_len < 10:
                continue
            
            corr = np.corrcoef(
                perf.returns[:min_len],
                [float(r) for r in other_returns[:min_len]],
            )[0, 1]
            
            if not math.isnan(corr) and abs(corr) > max_corr:
                max_corr = abs(corr)
                worst_strategy = name
        
        passed = max_corr < self.max_correlation
        score = max((1 - max_corr / self.max_correlation), 0) * 100 if self.max_correlation > 0 else 100
        
        return {
            "name": f"策略相关性(<{self.max_correlation})",
            "passed": passed,
            "score": score,
            "detail": f"最大相关性={max_corr:.2f} (vs {worst_strategy})" if worst_strategy else "无对比策略",
        }
    
    # ============================================================
    # 辅助计算方法
    # ============================================================
    
    @staticmethod
    def _calc_volatility(returns: List[float]) -> float:
        """计算年化波动率"""
        if len(returns) < 2:
            return 0.0
        return float(np.std(returns) * np.sqrt(252))
    
    @staticmethod
    def _calc_annual_return(returns: List[float]) -> float:
        """计算年化收益"""
        if not returns:
            return 0.0
        total = float(np.prod(1 + np.array(returns))) - 1
        n_years = len(returns) / 252
        if n_years <= 0:
            return total
        return (1 + total) ** (1 / n_years) - 1
    
    @staticmethod
    def _calc_max_drawdown(returns: List[float]) -> float:
        """计算最大回撤"""
        if not returns:
            return 0.0
        cumulative = np.cumprod(1 + np.array(returns))
        peak = np.maximum.accumulate(cumulative)
        drawdown = (peak - cumulative) / peak
        return float(np.max(drawdown))
    
    @staticmethod
    def _calc_sharpe(returns: List[float], risk_free: float = 0.03) -> float:
        """计算夏普比率"""
        if len(returns) < 2:
            return 0.0
        mean_return = float(np.mean(returns)) * 252
        vol = float(np.std(returns)) * np.sqrt(252)
        if vol == 0:
            return 0.0
        return (mean_return - risk_free) / vol
    
    @staticmethod
    def _calc_calmar(perf: _StrategyPerformance) -> float:
        """计算Calmar比率"""
        if perf.max_drawdown == 0:
            return 0.0
        return perf.annual_return / perf.max_drawdown


__all__ = [
    "StrategyValidator",
    "ValidationResult",
]
