"""
风险控制闭环 - Risk Control Closed Loop

提供完整的风险管理系统，集成到回测引擎中，通过回调/钩子模式实现
交易前检查和交易后监控。

Features:
- 交易前风控检查 (Pre-trade Risk Check)
- 交易后风控更新 (Post-trade Risk Update)
- VaR / CVaR 计算
- 最大回撤监控
- 每日亏损限制
- 持仓集中度检查
- 相关性监控
- 止损止盈管理
- 风险状态报告生成
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class ActionType(Enum):
    """风险动作类型"""
    REDUCE_POSITION = "reduce_position"
    CLOSE_ALL = "close_all"
    HALT_TRADING = "halt_trading"
    WARNING = "warning"


class UrgencyLevel(Enum):
    """紧急程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WarningSeverity(Enum):
    """警告严重程度"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WarningType(Enum):
    """警告类型"""
    CONCENTRATION = "concentration"
    CORRELATION = "correlation"
    DRAWDOWN = "drawdown"
    DAILY_LOSS = "daily_loss"
    POSITION_LIMIT = "position_limit"
    VaR_BREACH = "var_breach"


@dataclass
class RiskConfig:
    """风险控制配置"""
    max_position_pct: float = 0.3            # 单标的最大仓位占比
    max_total_position_pct: float = 0.9      # 最大总仓位占比
    max_drawdown_pct: float = 0.15           # 最大回撤限制
    max_daily_loss_pct: float = 0.05         # 每日最大亏损限制
    var_confidence: float = 0.95             # VaR 置信度
    stop_loss_pct: float = 0.05              # 止损百分比
    take_profit_pct: float = 0.15            # 止盈百分比
    max_correlation: float = 0.7             # 最大允许相关性


@dataclass
class RiskAction:
    """风险动作"""
    action_type: str = ""          # reduce_position / close_all / halt_trading / warning
    symbol: str = ""               # 相关标的
    reason: str = ""               # 原因说明
    urgency: str = "low"           # low / medium / high / critical
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskWarning:
    """风险警告"""
    warning_type: str = ""         # concentration / correlation / drawdown / daily_loss
    message: str = ""              # 警告信息
    severity: str = "low"          # low / medium / high / critical
    suggested_action: str = ""     # 建议操作


@dataclass
class RiskCheckResult:
    """风险检查结果"""
    passed: bool = True
    warnings: List[RiskWarning] = field(default_factory=list)
    violations: List[RiskAction] = field(default_factory=list)
    actions_taken: List[RiskAction] = field(default_factory=list)


@dataclass
class Position:
    """持仓信息"""
    symbol: str = ""
    quantity: float = 0.0
    avg_price: float = 0.0
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    weight: float = 0.0           # 占总资产权重


@dataclass
class PortfolioRiskState:
    """组合风险状态"""
    positions: List[Position] = field(default_factory=list)
    total_equity: float = 0.0
    total_position_value: float = 0.0
    cash: float = 0.0
    daily_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    drawdown: float = 0.0
    peak_equity: float = 0.0


class RiskController:
    """风险控制器

    提供完整的风险管理功能，通过回调/钩子模式集成到回测引擎中。

    Usage:
        config = RiskConfig(max_drawdown_pct=0.15, max_daily_loss_pct=0.05)
        controller = RiskController(config)

        # 交易前检查
        result = controller.pre_trade_check(
            symbol="AAPL", direction="buy", price=150.0, volume=100,
            portfolio_state=state
        )

        # 交易后更新
        result = controller.post_trade_update(portfolio_state)

        # 计算 VaR
        var = controller.calculate_var(returns, confidence=0.95)
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        """初始化风险控制器

        Args:
            config: 风险控制配置
        """
        self.config = config or RiskConfig()
        self._trading_halted = False
        self._peak_equity: Optional[float] = None
        self._daily_start_equity: Optional[float] = None
        self._risk_history: List[Dict[str, Any]] = []

    def reset(self) -> None:
        """重置风险控制器状态"""
        self._trading_halted = False
        self._peak_equity = None
        self._daily_start_equity = None
        self._risk_history.clear()

    def pre_trade_check(
        self,
        symbol: str,
        direction: str,
        price: float,
        volume: float,
        portfolio_state: PortfolioRiskState,
    ) -> RiskCheckResult:
        """交易前风险检查

        在执行交易前进行风险检查，判断是否允许交易。

        Args:
            symbol: 标的代码
            direction: 交易方向 (buy/sell)
            price: 交易价格
            volume: 交易数量
            portfolio_state: 当前组合状态

        Returns:
            RiskCheckResult 检查结果
        """
        result = RiskCheckResult(passed=True)

        # 检查是否已暂停交易
        if self._trading_halted:
            result.passed = False
            result.violations.append(RiskAction(
                action_type=ActionType.HALT_TRADING.value,
                symbol=symbol,
                reason="Trading halted due to risk limits breach",
                urgency=UrgencyLevel.CRITICAL.value,
            ))
            return result

        # 买入方向检查
        if direction == "buy":
            trade_value = price * volume

            # 检查单标的仓位限制
            current_position_value = 0.0
            for pos in portfolio_state.positions:
                if pos.symbol == symbol:
                    current_position_value = pos.quantity * pos.current_price
                    break

            new_position_value = current_position_value + trade_value
            if portfolio_state.total_equity > 0:
                new_weight = new_position_value / portfolio_state.total_equity
                if new_weight > self.config.max_position_pct:
                    result.passed = False
                    result.violations.append(RiskAction(
                        action_type=ActionType.REDUCE_POSITION.value,
                        symbol=symbol,
                        reason=(
                            f"Position weight {new_weight:.2%} exceeds "
                            f"limit {self.config.max_position_pct:.2%}"
                        ),
                        urgency=UrgencyLevel.HIGH.value,
                        details={
                            'current_weight': new_weight,
                            'max_weight': self.config.max_position_pct,
                        },
                    ))

            # 检查总仓位限制
            new_total_position = portfolio_state.total_position_value + trade_value
            if portfolio_state.total_equity > 0:
                total_weight = new_total_position / portfolio_state.total_equity
                if total_weight > self.config.max_total_position_pct:
                    result.passed = False
                    result.violations.append(RiskAction(
                        action_type=ActionType.WARNING.value,
                        symbol="",
                        reason=(
                            f"Total position {total_weight:.2%} exceeds "
                            f"limit {self.config.max_total_position_pct:.2%}"
                        ),
                        urgency=UrgencyLevel.MEDIUM.value,
                        details={
                            'total_weight': total_weight,
                            'max_total_weight': self.config.max_total_position_pct,
                        },
                    ))

        # 检查回撤限制
        if portfolio_state.drawdown > self.config.max_drawdown_pct:
            result.passed = False
            result.violations.append(RiskAction(
                action_type=ActionType.CLOSE_ALL.value,
                symbol="",
                reason=(
                    f"Drawdown {portfolio_state.drawdown:.2%} exceeds "
                    f"limit {self.config.max_drawdown_pct:.2%}"
                ),
                urgency=UrgencyLevel.CRITICAL.value,
            ))

        # 检查每日亏损限制
        if portfolio_state.total_equity > 0:
            daily_loss_pct = abs(portfolio_state.daily_pnl) / portfolio_state.total_equity
            if portfolio_state.daily_pnl < 0 and daily_loss_pct > self.config.max_daily_loss_pct:
                result.passed = False
                result.violations.append(RiskAction(
                    action_type=ActionType.HALT_TRADING.value,
                    symbol="",
                    reason=(
                        f"Daily loss {daily_loss_pct:.2%} exceeds "
                        f"limit {self.config.max_daily_loss_pct:.2%}"
                    ),
                    urgency=UrgencyLevel.HIGH.value,
                ))

        # 持仓集中度警告
        concentration_warnings = self.check_concentration(portfolio_state.positions)
        result.warnings.extend(concentration_warnings)

        return result

    def post_trade_update(self, portfolio_state: PortfolioRiskState) -> RiskCheckResult:
        """交易后风险更新

        在交易执行后更新风险状态并进行检查。

        Args:
            portfolio_state: 当前组合状态

        Returns:
            RiskCheckResult 检查结果
        """
        result = RiskCheckResult(passed=True)

        # 更新峰值权益（使用组合状态中的峰值，或内部追踪值）
        effective_peak = portfolio_state.peak_equity or self._peak_equity
        if effective_peak is None or portfolio_state.total_equity > effective_peak:
            effective_peak = portfolio_state.total_equity
        self._peak_equity = effective_peak

        # 检查回撤
        drawdown_action = self.check_drawdown_breach(
            portfolio_state.total_equity, effective_peak
        )
        if drawdown_action is not None:
            result.violations.append(drawdown_action)
            result.passed = False
            if drawdown_action.urgency == UrgencyLevel.CRITICAL.value:
                self._trading_halted = True

        # 检查每日亏损
        daily_action = self.check_daily_loss_limit(
            portfolio_state.daily_pnl, portfolio_state.total_equity
        )
        if daily_action is not None:
            result.violations.append(daily_action)
            result.passed = False
            if daily_action.urgency in (UrgencyLevel.HIGH.value, UrgencyLevel.CRITICAL.value):
                self._trading_halted = True

        # 记录风险历史
        self._risk_history.append({
            'equity': portfolio_state.total_equity,
            'drawdown': portfolio_state.drawdown,
            'daily_pnl': portfolio_state.daily_pnl,
            'positions': len(portfolio_state.positions),
            'passed': result.passed,
        })

        return result

    def calculate_var(
        self,
        returns: Union[pd.Series, np.ndarray, List[float]],
        confidence: float = 0.95,
    ) -> float:
        """计算历史 VaR (Value at Risk)

        Args:
            returns: 收益率序列
            confidence: 置信度 (默认 0.95)

        Returns:
            VaR 值（正数表示损失）
        """
        if isinstance(returns, pd.Series):
            returns = returns.dropna().values
        elif isinstance(returns, list):
            returns = np.array(returns)

        returns = np.array(returns)
        if len(returns) == 0:
            return 0.0

        var = np.percentile(returns, (1 - confidence) * 100)
        return abs(var)

    def calculate_cvar(
        self,
        returns: Union[pd.Series, np.ndarray, List[float]],
        confidence: float = 0.95,
    ) -> float:
        """计算条件 VaR / Expected Shortfall (CVaR)

        Args:
            returns: 收益率序列
            confidence: 置信度 (默认 0.95)

        Returns:
            CVaR 值（正数表示损失）
        """
        if isinstance(returns, pd.Series):
            returns = returns.dropna().values
        elif isinstance(returns, list):
            returns = np.array(returns)

        returns = np.array(returns)
        if len(returns) == 0:
            return 0.0

        var_threshold = np.percentile(returns, (1 - confidence) * 100)
        tail_returns = returns[returns <= var_threshold]

        if len(tail_returns) == 0:
            return abs(var_threshold)

        return abs(tail_returns.mean())

    def calculate_portfolio_correlation(
        self,
        positions: List[Position],
        returns_data: Dict[str, pd.Series],
    ) -> pd.DataFrame:
        """计算组合持仓的相关性矩阵

        Args:
            positions: 持仓列表
            returns_data: 收益率数据 {symbol: pd.Series}

        Returns:
            相关性矩阵 DataFrame
        """
        position_symbols = {p.symbol for p in positions if p.quantity > 0}
        available_symbols = position_symbols & set(returns_data.keys())

        if len(available_symbols) < 2:
            return pd.DataFrame()

        df = pd.DataFrame({s: returns_data[s] for s in available_symbols})
        return df.corr()

    def check_drawdown_breach(
        self,
        current_equity: float,
        peak_equity: float,
    ) -> Optional[RiskAction]:
        """检查回撤是否超限

        Args:
            current_equity: 当前权益
            peak_equity: 峰值权益

        Returns:
            如果回撤超限返回 RiskAction，否则返回 None
        """
        if peak_equity <= 0:
            return None

        drawdown = (peak_equity - current_equity) / peak_equity

        if drawdown > self.config.max_drawdown_pct:
            urgency = UrgencyLevel.CRITICAL.value
            action_type = ActionType.CLOSE_ALL.value
            if drawdown > self.config.max_drawdown_pct * 1.5:
                action_type = ActionType.HALT_TRADING.value

            return RiskAction(
                action_type=action_type,
                symbol="",
                reason=(
                    f"Drawdown {drawdown:.2%} exceeds limit "
                    f"{self.config.max_drawdown_pct:.2%}"
                ),
                urgency=urgency,
                details={
                    'current_drawdown': drawdown,
                    'max_drawdown_limit': self.config.max_drawdown_pct,
                    'peak_equity': peak_equity,
                    'current_equity': current_equity,
                },
            )

        return None

    def check_daily_loss_limit(
        self,
        daily_pnl: float,
        total_equity: float,
    ) -> Optional[RiskAction]:
        """检查每日亏损是否超限

        Args:
            daily_pnl: 当日盈亏
            total_equity: 总权益

        Returns:
            如果亏损超限返回 RiskAction，否则返回 None
        """
        if total_equity <= 0 or daily_pnl >= 0:
            return None

        daily_loss_pct = abs(daily_pnl) / total_equity

        if daily_loss_pct > self.config.max_daily_loss_pct:
            urgency = UrgencyLevel.HIGH.value
            action_type = ActionType.HALT_TRADING.value

            if daily_loss_pct > self.config.max_daily_loss_pct * 2:
                urgency = UrgencyLevel.CRITICAL.value

            return RiskAction(
                action_type=action_type,
                symbol="",
                reason=(
                    f"Daily loss {daily_loss_pct:.2%} exceeds limit "
                    f"{self.config.max_daily_loss_pct:.2%}"
                ),
                urgency=urgency,
                details={
                    'daily_loss_pct': daily_loss_pct,
                    'max_daily_loss_pct': self.config.max_daily_loss_pct,
                    'daily_pnl': daily_pnl,
                },
            )

        return None

    def check_concentration(
        self,
        positions: List[Position],
    ) -> List[RiskWarning]:
        """检查持仓集中度

        Args:
            positions: 持仓列表

        Returns:
            RiskWarning 列表
        """
        warnings: List[RiskWarning] = []

        for pos in positions:
            if pos.quantity <= 0:
                continue

            if pos.weight > self.config.max_position_pct:
                warnings.append(RiskWarning(
                    warning_type=WarningType.CONCENTRATION.value,
                    message=(
                        f"{pos.symbol} concentration {pos.weight:.2%} "
                        f"exceeds limit {self.config.max_position_pct:.2%}"
                    ),
                    severity=WarningSeverity.HIGH.value,
                    suggested_action=f"Reduce {pos.symbol} position",
                ))

        return warnings

    def check_correlation_breach(
        self,
        positions: List[Position],
        returns_data: Dict[str, pd.Series],
    ) -> List[RiskWarning]:
        """检查持仓相关性是否超限

        Args:
            positions: 持仓列表
            returns_data: 收益率数据

        Returns:
            RiskWarning 列表
        """
        warnings: List[RiskWarning] = []

        corr = self.calculate_portfolio_correlation(positions, returns_data)
        if corr.empty:
            return warnings

        symbols = corr.columns.tolist()
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                corr_val = corr.iloc[i, j]
                if not np.isnan(corr_val) and abs(corr_val) > self.config.max_correlation:
                    warnings.append(RiskWarning(
                        warning_type=WarningType.CORRELATION.value,
                        message=(
                            f"Correlation between {symbols[i]} and {symbols[j]} "
                            f"is {corr_val:.2f}, exceeds limit {self.config.max_correlation:.2f}"
                        ),
                        severity=WarningSeverity.MEDIUM.value,
                        suggested_action=(
                            f"Consider reducing position in either "
                            f"{symbols[i]} or {symbols[j]}"
                        ),
                    ))

        return warnings

    def generate_risk_report(self, portfolio_state: PortfolioRiskState) -> dict:
        """生成风险状态报告

        Args:
            portfolio_state: 当前组合状态

        Returns:
            风险状态报告字典
        """
        report = {
            'timestamp': pd.Timestamp.now().isoformat(),
            'trading_halted': self._trading_halted,
            'total_equity': portfolio_state.total_equity,
            'total_position_value': portfolio_state.total_position_value,
            'cash': portfolio_state.cash,
            'daily_pnl': portfolio_state.daily_pnl,
            'unrealized_pnl': portfolio_state.unrealized_pnl,
            'drawdown': portfolio_state.drawdown,
            'peak_equity': portfolio_state.peak_equity,
            'position_count': len(portfolio_state.positions),
            'positions': [],
            'warnings': [],
            'risk_score': 0.0,
        }

        # 计算风险评分 (0-100, 越高越安全)
        risk_score = 100.0

        # 回撤扣分
        if portfolio_state.drawdown > 0:
            dd_penalty = min(
                (portfolio_state.drawdown / self.config.max_drawdown_pct) * 30, 30
            )
            risk_score -= dd_penalty

        # 集中度扣分
        concentration_warnings = self.check_concentration(portfolio_state.positions)
        risk_score -= len(concentration_warnings) * 10
        report['warnings'].extend([
            {'type': w.warning_type, 'message': w.message, 'severity': w.severity}
            for w in concentration_warnings
        ])

        # 仓位占比扣分
        if portfolio_state.total_equity > 0:
            position_pct = portfolio_state.total_position_value / portfolio_state.total_equity
            if position_pct > self.config.max_total_position_pct:
                risk_score -= 15

        risk_score = max(0, min(100, risk_score))
        report['risk_score'] = round(risk_score, 1)

        # 持仓详情
        for pos in portfolio_state.positions:
            report['positions'].append({
                'symbol': pos.symbol,
                'quantity': pos.quantity,
                'avg_price': pos.avg_price,
                'current_price': pos.current_price,
                'pnl': pos.pnl,
                'pnl_pct': pos.pnl_pct,
                'weight': pos.weight,
            })

        return report

    def create_risk_callback(self):
        """创建用于回测引擎的风险回调函数

        返回一个可以传入回测引擎的回调函数，用于在每笔交易前后
        自动执行风险检查。

        Returns:
            回调函数
        """

        def pre_trade_hook(
            symbol: str,
            direction: str,
            price: float,
            volume: float,
            portfolio_state: PortfolioRiskState,
        ) -> RiskCheckResult:
            return self.pre_trade_check(symbol, direction, price, volume, portfolio_state)

        def post_trade_hook(
            portfolio_state: PortfolioRiskState,
        ) -> RiskCheckResult:
            return self.post_trade_update(portfolio_state)

        return {
            'pre_trade': pre_trade_hook,
            'post_trade': post_trade_hook,
        }
