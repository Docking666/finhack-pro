"""
FinHack Pro 策略库

提供多种量化交易策略实现，所有策略继承自BaseStrategy基类。
支持差异化策略框架，实现"机构做广度，个人做深度"的投资理念。
包含信号聚合器和策略验证框架，提升系统鲁棒性。
"""

from finhack_pro.strategies.base import BaseStrategy, Context, Signal
from finhack_pro.strategies.dual_thrust import DualThrustStrategy
from finhack_pro.strategies.mean_reversion import MeanReversionStrategy
from finhack_pro.strategies.momentum import MomentumStrategy
from finhack_pro.strategies.niche_strategy import (
    NicheStrategy,
    NicheStrategyConfig,
    NicheType,
    NicheSignal,
    create_niche_strategy,
)
from finhack_pro.strategies.signal_aggregator import (
    SignalAggregator,
    AggregatedSignal,
)
from finhack_pro.strategies.strategy_validator import (
    StrategyValidator,
    ValidationResult,
)

__all__ = [
    "BaseStrategy",
    "Context",
    "Signal",
    "DualThrustStrategy",
    "MomentumStrategy",
    "MeanReversionStrategy",
    # 差异化策略
    "NicheStrategy",
    "NicheStrategyConfig",
    "NicheType",
    "NicheSignal",
    "create_niche_strategy",
    # 信号聚合
    "SignalAggregator",
    "AggregatedSignal",
    # 策略验证
    "StrategyValidator",
    "ValidationResult",
]
