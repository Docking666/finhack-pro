"""组合策略 - Composite Strategy

将多个子策略 + 信号聚合器（SignalAggregator，含信号滤波管道）组合为单一策略，
供回测 runner 逐 bar 调用。runner.run() 接口完全不变（现有测试零冲击）。

设计要点：
- on_bar 在 runner 逐 bar 循环内执行 → 卡尔曼/KAMA/FRAMA 等**逐 bar 时序滤波器**
  的状态要求得到满足（服务层循环外聚合会破坏滤波语义）
- 子策略信号收集 → SignalAggregator.aggregate（含 L2 正则/去重/加权投票/置信度校准）
  → 聚合结果转回 base.Signal 返回 runner 执行
- 任一子策略 on_bar 异常不阻断整体（日志告警，继续其他策略）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from finhack_pro.strategies.base import BaseStrategy, BarData, Context, Signal, SignalDirection
from finhack_pro.strategies.signal_aggregator import AggregatedDirection, SignalAggregator


class CompositeStrategy(BaseStrategy):
    """多策略信号组合器

    Args:
        strategies: 子策略实例列表（至少 1 个）
        aggregator: 自定义 SignalAggregator；缺省时按 filter_config 创建默认管道
        filter_config: 信号滤波管道配置（{filter_name: {...params}}）
        enable_high_cost_filters: 是否启用高开销滤波器（Transformer/粒子滤波）
    """

    def __init__(
        self,
        strategies: List[BaseStrategy],
        aggregator: Optional[SignalAggregator] = None,
        filter_config: Optional[Dict[str, Dict[str, Any]]] = None,
        enable_high_cost_filters: bool = False,
    ) -> None:
        super().__init__()
        if not strategies:
            raise ValueError("CompositeStrategy 至少需要 1 个子策略")
        self._strategies: List[BaseStrategy] = strategies
        self._aggregator = aggregator or SignalAggregator(
            filter_config=filter_config,
            enable_high_cost_filters=enable_high_cost_filters,
        )
        self._name = "CompositeStrategy"
        self._symbol = strategies[0].symbol if hasattr(strategies[0], "symbol") else ""

    @property
    def strategies(self) -> List[BaseStrategy]:
        """子策略列表"""
        return self._strategies

    @property
    def aggregator(self) -> SignalAggregator:
        """信号聚合器（含滤波管道）"""
        return self._aggregator

    def on_init(self, context: Context) -> None:
        for s in self._strategies:
            try:
                s.on_init(context)
            except Exception as e:
                logger.warning(f"[Composite] 子策略 on_init 失败 {s.__class__.__name__}: {e}")

    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        # 1. 收集所有子策略信号（打上策略来源，供聚合器加权/滤波识别）
        raw_signals: List[Signal] = []
        for s in self._strategies:
            try:
                sub_signals = s.on_bar(context, bar) or []
                for sig in sub_signals:
                    if not sig.strategy_name:
                        sig.strategy_name = s.__class__.__name__
                    raw_signals.append(sig)
            except Exception as e:
                logger.warning(f"[Composite] 子策略 on_bar 失败 {s.__class__.__name__}: {e}")

        if not raw_signals:
            return []

        # 2. 信号聚合（标准化 → 滤波管道 → 去重 → 加权 → 校准）
        try:
            aggregated = self._aggregator.aggregate(raw_signals, apply_filters=True)
        except Exception as e:
            logger.warning(f"[Composite] 信号聚合失败，回退原始信号: {e}")
            return raw_signals

        # 3. 聚合信号 → base.Signal（仅产出 BUY/SELL；HOLD 不产生交易）
        outputs: List[Signal] = []
        for agg in aggregated:
            if agg.direction not in (AggregatedDirection.BUY, AggregatedDirection.SELL):
                continue
            direction = SignalDirection.BUY if agg.direction == AggregatedDirection.BUY else SignalDirection.SELL
            outputs.append(Signal(
                symbol=agg.symbol or self._symbol,
                direction=direction,
                price=float(bar.close),
                strategy_name=self._name,
                extra={
                    "aggregated_confidence": agg.confidence,
                    "reasoning": getattr(agg, "reasoning", "") or "多策略聚合信号（信号聚合器加权投票）",
                },
            ))
        return outputs
