"""
FinHack Pro 策略库

提供多种量化交易策略实现，所有策略继承自BaseStrategy基类。
"""

from finhack_pro.strategies.base import BaseStrategy, Context, Signal
from finhack_pro.strategies.dual_thrust import DualThrustStrategy
from finhack_pro.strategies.mean_reversion import MeanReversionStrategy
from finhack_pro.strategies.momentum import MomentumStrategy

__all__ = [
    "BaseStrategy",
    "Context",
    "Signal",
    "DualThrustStrategy",
    "MomentumStrategy",
    "MeanReversionStrategy",
]
