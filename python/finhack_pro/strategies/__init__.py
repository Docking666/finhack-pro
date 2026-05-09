"""
FinHack Pro 策略库

提供多种量化交易策略实现，所有策略继承自BaseStrategy基类。
支持差异化策略框架，实现"机构做广度，个人做深度"的投资理念。
包含信号聚合器、策略验证框架和信号滤波模块，提升系统鲁棒性。
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
    AggregatedDirection,
)
from finhack_pro.strategies.signal_filters import (
    SignalFilterPipeline,
    SignalType,
    RawSignal,
    FilteredSignal,
    KalmanFilterFusion,
    AdaptiveWeightedAverage,
    KAMAFilter,
    FRAMAFilter,
    AnomalyDetector,
    TransformerAttentionFusion,
    ParticleFilter,
    create_default_pipeline,
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
    "AggregatedDirection",
    # 信号滤波
    "SignalFilterPipeline",
    "SignalType",
    "RawSignal",
    "FilteredSignal",
    "KalmanFilterFusion",
    "AdaptiveWeightedAverage",
    "KAMAFilter",
    "FRAMAFilter",
    "AnomalyDetector",
    "TransformerAttentionFusion",
    "ParticleFilter",
    "create_default_pipeline",
    # 策略验证
    "StrategyValidator",
    "ValidationResult",
]
