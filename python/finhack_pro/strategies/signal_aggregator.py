"""
信号聚合器模块

将多个策略产生的信号聚合为单一决策，支持信号去重、
基于历史表现的加权、L2正则化防止过度自信以及置信度校准。

新增功能:
- 集成信号滤波管道 (卡尔曼/KAMA/FRAMA/异常检测/Transformer/粒子滤波)
- 支持可配置的滤波器组合

支持的信号来源:
- finhack_pro.strategies.base.Signal (传统策略信号)
- finhack_pro.agents.strategy_generator.StrategySignal (AI策略信号)
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from pydantic import BaseModel, Field

from finhack_pro.strategies.base import Signal, SignalDirection
from finhack_pro.strategies.signal_filters import (
    SignalFilterPipeline,
    RawSignal,
    FilteredSignal,
    SignalType,
    create_default_pipeline,
)
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 输出模型
# ---------------------------------------------------------------------------


class AggregatedDirection(str, Enum):
    """聚合信号方向"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class AggregatedSignal(BaseModel):
    """聚合后的交易信号

    Attributes:
        symbol: 标的代码
        direction: 聚合后的信号方向
        confidence: 聚合置信度 (0-1)
        position_size_pct: 建议仓位百分比 (0-1)
        contributing_strategies: 贡献信号的策略名称列表
        reasoning: 聚合决策理由
        risk_factors: 风险因素列表
    """
    symbol: str
    direction: AggregatedDirection
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    position_size_pct: float = Field(ge=0.0, le=1.0, default=0.0)
    contributing_strategies: List[str] = Field(default_factory=list)
    reasoning: str = ""
    risk_factors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 内部辅助数据结构
# ---------------------------------------------------------------------------


class _NormalizedSignal(BaseModel):
    """内部标准化信号

    将不同来源的信号统一为相同格式，便于后续聚合处理。
    """
    symbol: str
    direction: AggregatedDirection
    raw_confidence: float = Field(ge=0.0, le=1.0)
    weighted_confidence: float = Field(ge=0.0, le=1.0)
    strategy_name: str
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size_pct: float = 0.0
    reasoning: str = ""
    # 用于相关性计算的特征向量
    feature_vector: List[float] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SignalAggregator
# ---------------------------------------------------------------------------


class SignalAggregator:
    """信号聚合器

    将来自多个策略的信号聚合为一个统一的交易决策。

    核心流程:
    1. 信号标准化: 将不同来源的信号转换为统一格式
    2. 信号去重: 剔除相关性过高的冗余信号 (相关系数 > 0.7 时保留最强信号)
    3. 基于历史表现的加权: 根据策略的胜率/夏普比率分配权重
    4. L2正则化: 对信号强度施加正则化，防止过度自信
    5. 置信度校准: 将聚合置信度映射到经过校准的概率空间

    Usage:
        aggregator = SignalAggregator(
            strategy_weights={"Momentum": 0.6, "MeanReversion": 0.4},
            l2_lambda=0.1,
        )
        result = aggregator.aggregate(signals)
    """

    def __init__(
        self,
        strategy_weights: Optional[Dict[str, float]] = None,
        strategy_sharpe: Optional[Dict[str, float]] = None,
        strategy_win_rate: Optional[Dict[str, float]] = None,
        l2_lambda: float = 0.1,
        correlation_threshold: float = 0.7,
        calibration_temperature: float = 1.0,
        min_confidence_threshold: float = 0.3,
        filter_pipeline: Optional[SignalFilterPipeline] = None,
        filter_config: Optional[Dict[str, Dict[str, Any]]] = None,
        enable_high_cost_filters: bool = False,
    ) -> None:
        """初始化信号聚合器

        Args:
            strategy_weights: 策略名称到权重的映射 (手动指定)
            strategy_sharpe: 策略名称到夏普比率的映射 (用于自动加权)
            strategy_win_rate: 策略名称到胜率的映射 (用于自动加权)
            l2_lambda: L2正则化系数，值越大对高置信度信号的惩罚越强
            correlation_threshold: 信号去重的相关系数阈值，超过此值视为冗余
            calibration_temperature: 置信度校准温度参数，
                >1 使输出更保守(拉向0.5)，<1 使输出更极端
            min_confidence_threshold: 最低置信度阈值，低于此值输出HOLD
            filter_pipeline: 自定义滤波管道实例
            filter_config: 滤波管道配置字典 (用于创建默认管道)
            enable_high_cost_filters: 是否启用高开销滤波器 (Transformer/粒子滤波)
        """
        self._strategy_weights: Dict[str, float] = strategy_weights or {}
        self._strategy_sharpe: Dict[str, float] = strategy_sharpe or {}
        self._strategy_win_rate: Dict[str, float] = strategy_win_rate or {}
        self._l2_lambda = l2_lambda
        self._correlation_threshold = correlation_threshold
        self._calibration_temperature = calibration_temperature
        self._min_confidence_threshold = min_confidence_threshold

        # 初始化滤波管道
        if filter_pipeline is not None:
            self._filter_pipeline = filter_pipeline
        elif filter_config is not None:
            self._filter_pipeline = SignalFilterPipeline(filter_config)
        else:
            self._filter_pipeline = create_default_pipeline(enable_high_cost=enable_high_cost_filters)

        # 如果未手动指定权重但有历史表现数据，则自动计算权重
        if not self._strategy_weights and (self._strategy_sharpe or self._strategy_win_rate):
            self._strategy_weights = self._compute_performance_weights()

        logger.info(
            f"信号聚合器初始化完成: 策略数={len(self._strategy_weights)}, "
            f"L2_lambda={self._l2_lambda}, 相关阈值={self._correlation_threshold}, "
            f"滤波器={len(self._filter_pipeline.filters)}个"
        )

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def aggregate(
        self,
        signals: List[Union[Signal, Any]],
        apply_filters: bool = True,
    ) -> List[AggregatedSignal]:
        """聚合多个策略信号

        Args:
            signals: 信号列表，支持 base.Signal 和 strategy_generator.StrategySignal
            apply_filters: 是否应用滤波管道 (默认True)

        Returns:
            按标的分组的聚合信号列表
        """
        if not signals:
            logger.warning("收到空信号列表，返回空聚合结果")
            return []

        # 第一步: 标准化信号
        normalized = self._normalize_signals(signals)
        logger.debug(f"信号标准化完成: {len(normalized)} 个信号")

        # 第二步: 应用滤波管道 (新增)
        if apply_filters and self._filter_pipeline:
            normalized = self._apply_filters(normalized)
            logger.debug(f"滤波处理完成: {len(normalized)} 个信号")

        # 按标的分组
        grouped: Dict[str, List[_NormalizedSignal]] = {}
        for sig in normalized:
            grouped.setdefault(sig.symbol, []).append(sig)

        results: List[AggregatedSignal] = []
        for symbol, sig_list in grouped.items():
            agg = self._aggregate_for_symbol(symbol, sig_list)
            results.append(agg)

        logger.info(f"信号聚合完成: {len(results)} 个标的产生聚合信号")
        return results

    def _apply_filters(self, normalized: List[_NormalizedSignal]) -> List[_NormalizedSignal]:
        """应用滤波管道处理标准化信号"""
        if not self._filter_pipeline:
            return normalized
            
        # 转换为RawSignal格式
        raw_signals = []
        for norm in normalized:
            raw = RawSignal(
                source=norm.strategy_name,
                signal_type=self._map_signal_type(norm.strategy_name),
                value=self._direction_to_value(norm.direction),
                confidence=norm.raw_confidence,
                metadata={"normalized_signal": norm.model_dump()},
            )
            raw_signals.append(raw)
            
        # 应用滤波管道
        filtered = self._filter_pipeline.process(raw_signals)
        
        # 转换回_NormalizedSignal
        result = []
        for i, fs in enumerate(filtered):
            if i < len(normalized):
                norm = normalized[i]
                # 更新值和置信度
                norm.weighted_confidence = fs.confidence
                norm.raw_confidence = fs.confidence
                # 存储滤波元数据
                norm.feature_vector = [
                    fs.value,
                    fs.confidence,
                    fs.uncertainty,
                ]
                result.append(norm)
                
        return result if result else normalized

    def _map_signal_type(self, strategy_name: str) -> SignalType:
        """根据策略名称映射信号类型"""
        name_lower = strategy_name.lower()
        if "technical" in name_lower or "momentum" in name_lower or "dual" in name_lower:
            return SignalType.TECHNICAL
        elif "sentiment" in name_lower or "news" in name_lower:
            return SignalType.SENTIMENT
        elif "fundamental" in name_lower or "value" in name_lower:
            return SignalType.FUNDAMENTAL
        elif "event" in name_lower or "niche" in name_lower or "dragon" in name_lower:
            return SignalType.EVENT
        else:
            return SignalType.COMBINED

    def _direction_to_value(self, direction: AggregatedDirection) -> float:
        """将方向转换为数值"""
        if direction == AggregatedDirection.BUY:
            return 1.0
        elif direction == AggregatedDirection.SELL:
            return -1.0
        else:
            return 0.0

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _normalize_signals(
        self,
        signals: List[Union[Signal, Any]],
    ) -> List[_NormalizedSignal]:
        """将不同来源的信号标准化为内部格式

        支持:
        - finhack_pro.strategies.base.Signal
        - finhack_pro.agents.strategy_generator.StrategySignal
        """
        normalized: List[_NormalizedSignal] = []

        for sig in signals:
            try:
                norm = self._normalize_one(sig)
                normalized.append(norm)
            except Exception as e:
                logger.warning(f"信号标准化失败，跳过: {e}")
                continue

        return normalized

    def _normalize_one(self, sig: Union[Signal, Any]) -> _NormalizedSignal:
        """标准化单个信号"""
        # 判断信号类型
        if isinstance(sig, Signal):
            return self._normalize_base_signal(sig)

        # 尝试按 StrategySignal (Pydantic model) 处理
        # 通过 duck typing 检测属性
        if hasattr(sig, "confidence") and hasattr(sig, "strategy_type"):
            return self._normalize_agent_signal(sig)

        # 兜底: 按 base.Signal 处理
        if hasattr(sig, "symbol") and hasattr(sig, "direction"):
            return self._normalize_base_signal(sig)

        raise ValueError(f"无法识别的信号类型: {type(sig)}")

    def _normalize_base_signal(self, sig: Signal) -> _NormalizedSignal:
        """标准化 base.Signal

        base.Signal 没有 confidence 字段，使用方向一致性作为代理指标。
        """
        direction = self._map_direction(sig.direction.value if hasattr(sig.direction, 'value') else str(sig.direction))
        # base.Signal 没有显式置信度，默认使用中等置信度
        confidence = 0.6

        # 构建特征向量 (用于相关性计算)
        feature_vector = self._build_feature_vector(
            price=sig.price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            volume=sig.volume,
        )

        return _NormalizedSignal(
            symbol=sig.symbol,
            direction=direction,
            raw_confidence=confidence,
            weighted_confidence=confidence,
            strategy_name=sig.strategy_name or "Unknown",
            price=sig.price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            position_size_pct=0.0,
            reasoning=sig.extra.get("reasoning", "") if sig.extra else "",
            feature_vector=feature_vector,
        )

    def _normalize_agent_signal(self, sig: Any) -> _NormalizedSignal:
        """标准化 StrategySignal (来自 strategy_generator Agent)"""
        direction = self._map_direction(
            sig.direction.value if hasattr(sig.direction, 'value') else str(sig.direction)
        )
        confidence = float(sig.confidence)

        feature_vector = self._build_feature_vector(
            price=float(getattr(sig, 'entry_price', 0) or getattr(sig, 'target_price', 0) or 0),
            stop_loss=float(sig.stop_loss),
            take_profit=float(sig.take_profit),
            volume=0,
        )

        return _NormalizedSignal(
            symbol=sig.symbol,
            direction=direction,
            raw_confidence=confidence,
            weighted_confidence=confidence,
            strategy_name=getattr(sig, 'strategy_type', '') or "AI Strategy",
            price=float(getattr(sig, 'entry_price', 0) or getattr(sig, 'target_price', 0) or 0),
            stop_loss=float(sig.stop_loss),
            take_profit=float(sig.take_profit),
            position_size_pct=float(getattr(sig, 'position_size_pct', 0) or 0),
            reasoning=getattr(sig, 'reasoning', '') or "",
            feature_vector=feature_vector,
        )

    @staticmethod
    def _map_direction(direction_str: str) -> AggregatedDirection:
        """将方向字符串映射为聚合方向枚举"""
        mapping = {
            "buy": AggregatedDirection.BUY,
            "sell": AggregatedDirection.SELL,
            "hold": AggregatedDirection.HOLD,
        }
        return mapping.get(direction_str.lower(), AggregatedDirection.HOLD)

    @staticmethod
    def _build_feature_vector(
        price: float,
        stop_loss: float,
        take_profit: float,
        volume: float,
    ) -> List[float]:
        """构建信号特征向量 (用于相关性计算)

        使用归一化的价格特征:
        - 止损距离比例: (price - stop_loss) / price
        - 止盈距离比例: (take_profit - price) / price
        - 风险收益比: (take_profit - price) / (price - stop_loss) (止损为0时用默认值)
        - 成交量对数: log(volume + 1)
        """
        if price <= 0:
            return [0.0, 0.0, 1.0, 0.0]

        sl_dist = (price - stop_loss) / price if stop_loss > 0 else 0.05
        tp_dist = (take_profit - price) / price if take_profit > 0 else 0.10
        risk_reward = tp_dist / sl_dist if sl_dist > 0 else 2.0
        vol_log = math.log(volume + 1) if volume > 0 else 0.0

        return [sl_dist, tp_dist, risk_reward, vol_log]

    # ------------------------------------------------------------------
    # 信号去重
    # ------------------------------------------------------------------

    def _deduplicate_signals(
        self,
        signals: List[_NormalizedSignal],
    ) -> List[_NormalizedSignal]:
        """信号去重: 剔除相关性过高的冗余信号

        对于相关性超过阈值的信号对，保留置信度更高的那个。
        使用贪心算法: 按置信度降序排列，依次检查与已保留信号的相关性。

        Args:
            signals: 标准化后的信号列表

        Returns:
            去重后的信号列表
        """
        if len(signals) <= 1:
            return signals

        # 按加权置信度降序排列
        sorted_sigs = sorted(signals, key=lambda s: s.weighted_confidence, reverse=True)

        kept: List[_NormalizedSignal] = [sorted_sigs[0]]
        removed_count = 0

        for sig in sorted_sigs[1:]:
            is_duplicate = False
            for existing in kept:
                corr = self._compute_correlation(sig.feature_vector, existing.feature_vector)
                if corr > self._correlation_threshold:
                    is_duplicate = True
                    removed_count += 1
                    logger.debug(
                        f"信号去重: 移除 '{sig.strategy_name}' (与 '{existing.strategy_name}' "
                        f"相关系数={corr:.3f} > 阈值={self._correlation_threshold})"
                    )
                    break

            if not is_duplicate:
                kept.append(sig)

        if removed_count > 0:
            logger.info(f"信号去重: 移除 {removed_count} 个冗余信号, 保留 {len(kept)} 个")

        return kept

    @staticmethod
    def _compute_correlation(vec_a: List[float], vec_b: List[float]) -> float:
        """计算两个特征向量的皮尔逊相关系数

        如果向量长度不足或标准差为零，返回0.0。
        """
        if len(vec_a) != len(vec_b) or len(vec_a) < 2:
            return 0.0

        a = np.array(vec_a, dtype=np.float64)
        b = np.array(vec_b, dtype=np.float64)

        std_a = np.std(a)
        std_b = np.std(b)

        if std_a < 1e-10 or std_b < 1e-10:
            return 0.0

        return float(np.corrcoef(a, b)[0, 1])

    # ------------------------------------------------------------------
    # 基于历史表现的加权
    # ------------------------------------------------------------------

    def _compute_performance_weights(self) -> Dict[str, float]:
        """根据策略的历史表现计算权重

        综合使用胜率和夏普比率:
        - 若两者都可用: weight = 0.5 * normalized_sharpe + 0.5 * normalized_win_rate
        - 若仅有夏普比率: weight = normalized_sharpe
        - 若仅有胜率: weight = normalized_win_rate

        Returns:
            策略名称到归一化权重的映射 (总和为1)
        """
        all_strategies = set(self._strategy_sharpe.keys()) | set(self._strategy_win_rate.keys())
        if not all_strategies:
            return {}

        raw_weights: Dict[str, float] = {}

        for name in all_strategies:
            sharpe = self._strategy_sharpe.get(name)
            win_rate = self._strategy_win_rate.get(name)

            # 归一化到 [0, 1] 区间
            # 夏普比率: 假设范围 [-1, 3]，映射到 [0, 1]
            norm_sharpe = (sharpe + 1.0) / 4.0 if sharpe is not None else None
            # 胜率: 已经在 [0, 1] 范围内
            norm_wr = win_rate if win_rate is not None else None

            if norm_sharpe is not None and norm_wr is not None:
                raw_weights[name] = 0.5 * max(norm_sharpe, 0) + 0.5 * norm_wr
            elif norm_sharpe is not None:
                raw_weights[name] = max(norm_sharpe, 0)
            elif norm_wr is not None:
                raw_weights[name] = norm_wr
            else:
                raw_weights[name] = 0.5  # 默认中等权重

        # 归一化使权重总和为1
        total = sum(raw_weights.values())
        if total < 1e-10:
            # 所有权重接近零，平均分配
            n = len(raw_weights)
            return {name: 1.0 / n for name in raw_weights}

        return {name: w / total for name, w in raw_weights.items()}

    def _apply_weights(self, signals: List[_NormalizedSignal]) -> List[_NormalizedSignal]:
        """对信号应用策略权重

        如果某策略未注册权重，使用默认权重 1/N (N 为已注册策略数)。
        """
        if not signals:
            return signals

        n_registered = max(len(self._strategy_weights), 1)
        default_weight = 1.0 / n_registered

        for sig in signals:
            weight = self._strategy_weights.get(sig.strategy_name, default_weight)
            sig.weighted_confidence = sig.raw_confidence * weight

        return signals

    # ------------------------------------------------------------------
    # L2 正则化
    # ------------------------------------------------------------------

    def _apply_l2_regularization(
        self,
        signals: List[_NormalizedSignal],
    ) -> List[_NormalizedSignal]:
        """对信号强度施加 L2 正则化

        L2正则化公式:
            confidence_reg = confidence / (1 + lambda * confidence^2)

        效果:
        - 高置信度信号会被适度压低，防止过度自信
        - 低置信度信号受影响较小
        - lambda 越大，正则化效果越强

        Args:
            signals: 标准化后的信号列表

        Returns:
            正则化后的信号列表
        """
        for sig in signals:
            c = sig.weighted_confidence
            # L2 正则化: c / (1 + lambda * c^2)
            reg_confidence = c / (1.0 + self._l2_lambda * c * c)
            sig.weighted_confidence = max(0.0, min(1.0, reg_confidence))

        return signals

    # ------------------------------------------------------------------
    # 置信度校准
    # ------------------------------------------------------------------

    def _calibrate_confidence(self, confidence: float) -> float:
        """使用温度缩放进行置信度校准

        校准公式 (sigmoid 温度缩放):
            calibrated = 1 / (1 + exp(-(logit / T)))

        其中:
            logit = log(confidence / (1 - confidence))
            T = calibration_temperature

        效果:
        - T > 1: 输出更保守，趋向 0.5
        - T < 1: 输出更极端，远离 0.5
        - T = 1: 无变化

        Args:
            confidence: 原始置信度 (0, 1)

        Returns:
            校准后的置信度
        """
        # 边界保护
        confidence = max(1e-6, min(1.0 - 1e-6, confidence))

        logit = math.log(confidence / (1.0 - confidence))
        calibrated_logit = logit / self._calibration_temperature
        calibrated = 1.0 / (1.0 + math.exp(-calibrated_logit))

        return max(0.0, min(1.0, calibrated))

    # ------------------------------------------------------------------
    # 单标的聚合
    # ------------------------------------------------------------------

    def _aggregate_for_symbol(
        self,
        symbol: str,
        signals: List[_NormalizedSignal],
    ) -> AggregatedSignal:
        """对单个标的的所有信号进行聚合

        流程:
        1. 应用权重
        2. 信号去重
        3. L2 正则化
        4. 加权投票确定方向
        5. 置信度校准
        6. 生成风险因素

        Args:
            symbol: 标的代码
            signals: 该标的的标准化信号列表

        Returns:
            聚合信号
        """
        # 第一步: 应用策略权重
        signals = self._apply_weights(signals)

        # 第二步: 信号去重
        signals = self._deduplicate_signals(signals)

        if not signals:
            return AggregatedSignal(
                symbol=symbol,
                direction=AggregatedDirection.HOLD,
                confidence=0.0,
                position_size_pct=0.0,
                contributing_strategies=[],
                reasoning="所有信号被去重移除，无有效信号",
                risk_factors=["信号不足"],
            )

        # 第三步: L2 正则化
        signals = self._apply_l2_regularization(signals)

        # 第四步: 加权投票确定方向
        direction, direction_scores = self._weighted_vote(signals)

        # 第五步: 计算聚合置信度
        raw_confidence = self._compute_aggregated_confidence(signals, direction_scores)

        # 第六步: 置信度校准
        calibrated_confidence = self._calibrate_confidence(raw_confidence)

        # 低于阈值时转为 HOLD
        if calibrated_confidence < self._min_confidence_threshold:
            direction = AggregatedDirection.HOLD

        # 计算建议仓位
        position_size = self._compute_position_size(calibrated_confidence, direction, signals)

        # 收集贡献策略
        contributing = [s.strategy_name for s in signals]

        # 生成决策理由
        reasoning = self._build_reasoning(symbol, direction, calibrated_confidence, signals)

        # 生成风险因素
        risk_factors = self._identify_risk_factors(signals, calibrated_confidence)

        return AggregatedSignal(
            symbol=symbol,
            direction=direction,
            confidence=round(calibrated_confidence, 4),
            position_size_pct=round(position_size, 4),
            contributing_strategies=contributing,
            reasoning=reasoning,
            risk_factors=risk_factors,
        )

    def _weighted_vote(
        self,
        signals: List[_NormalizedSignal],
    ) -> Tuple[AggregatedDirection, Dict[AggregatedDirection, float]]:
        """加权投票确定信号方向

        对每个方向的加权置信度求和，选择得分最高的方向。

        Returns:
            (胜出方向, 各方向得分字典)
        """
        scores: Dict[AggregatedDirection, float] = {
            AggregatedDirection.BUY: 0.0,
            AggregatedDirection.SELL: 0.0,
            AggregatedDirection.HOLD: 0.0,
        }

        for sig in signals:
            scores[sig.direction] += sig.weighted_confidence

        best_direction = max(scores, key=lambda d: scores[d])
        return best_direction, scores

    def _compute_aggregated_confidence(
        self,
        signals: List[_NormalizedSignal],
        direction_scores: Dict[AggregatedDirection, float],
    ) -> float:
        """计算聚合置信度

        使用胜出方向的得分占比:
            confidence = best_score / total_score

        如果总分为零，返回 0.0。
        """
        total = sum(direction_scores.values())
        if total < 1e-10:
            return 0.0

        best_score = max(direction_scores.values())
        return best_score / total

    def _compute_position_size(
        self,
        confidence: float,
        direction: AggregatedDirection,
        signals: List[_NormalizedSignal],
    ) -> float:
        """根据置信度和方向计算建议仓位

        仓位计算规则:
        - HOLD: 仓位为 0
        - BUY/SELL: 基础仓位 = confidence * max_position_factor
        - 取所有信号中建议仓位的加权平均作为参考
        - 最终仓位不超过 30%
        """
        if direction == AggregatedDirection.HOLD:
            return 0.0

        max_position = 0.30  # 单标的最大仓位 30%

        # 基于置信度的仓位
        confidence_based = confidence * max_position

        # 基于信号建议的加权平均仓位
        total_weight = sum(s.weighted_confidence for s in signals if s.position_size_pct > 0)
        if total_weight > 1e-10:
            signal_based = sum(
                s.position_size_pct * s.weighted_confidence
                for s in signals
                if s.position_size_pct > 0
            ) / total_weight
        else:
            signal_based = confidence_based

        # 取两者中较小值，并限制最大仓位
        position = min(confidence_based, signal_based, max_position)
        return max(0.0, position)

    def _build_reasoning(
        self,
        symbol: str,
        direction: AggregatedDirection,
        confidence: float,
        signals: List[_NormalizedSignal],
    ) -> str:
        """构建聚合决策理由

        生成人类可读的中文决策说明。
        """
        direction_cn = {
            AggregatedDirection.BUY: "买入",
            AggregatedDirection.SELL: "卖出",
            AggregatedDirection.HOLD: "观望",
        }

        parts = [
            f"标的 {symbol} 聚合决策: {direction_cn[direction]}",
            f"聚合置信度: {confidence:.1%}",
            f"参与策略: {', '.join(s.strategy_name for s in signals)}",
        ]

        # 方向统计
        buy_count = sum(1 for s in signals if s.direction == AggregatedDirection.BUY)
        sell_count = sum(1 for s in signals if s.direction == AggregatedDirection.SELL)
        hold_count = sum(1 for s in signals if s.direction == AggregatedDirection.HOLD)

        parts.append(
            f"方向分布: 买入={buy_count}, 卖出={sell_count}, 观望={hold_count}"
        )

        # 附加各策略理由
        reasonings = [s.reasoning for s in signals if s.reasoning]
        if reasonings:
            parts.append("各策略理由:")
            for i, r in enumerate(reasonings, 1):
                parts.append(f"  {i}. {r}")

        return "\n".join(parts)

    def _identify_risk_factors(
        self,
        signals: List[_NormalizedSignal],
        confidence: float,
    ) -> List[str]:
        """识别风险因素

        基于信号特征自动识别潜在风险。
        """
        risks: List[str] = []

        # 风险1: 策略间方向分歧大
        directions = set(s.direction for s in signals)
        if len(directions) > 1:
            risks.append(f"策略方向存在分歧 ({len(directions)} 个不同方向)")

        # 风险2: 信号数量过少
        if len(signals) < 2:
            risks.append("参与聚合的策略数量不足 (少于2个)")

        # 风险3: 置信度过低
        if confidence < 0.4:
            risks.append(f"聚合置信度偏低 ({confidence:.1%})")

        # 风险4: 置信度过高 (可能过度自信)
        if confidence > 0.9:
            risks.append(f"聚合置信度异常偏高 ({confidence:.1%})，可能存在过度自信风险")

        # 风险5: 缺少止损/止盈设置
        no_sl = sum(1 for s in signals if s.stop_loss <= 0)
        no_tp = sum(1 for s in signals if s.take_profit <= 0)
        if no_sl > 0:
            risks.append(f"{no_sl} 个信号未设置止损")
        if no_tp > 0:
            risks.append(f"{no_tp} 个信号未设置止盈")

        # 风险6: 所有信号来自同一策略类型
        strategy_types = set(s.strategy_name for s in signals)
        if len(strategy_types) == 1:
            risks.append("所有信号来自同一策略，缺乏多样性")

        return risks


__all__ = [
    "SignalAggregator",
    "AggregatedSignal",
    "AggregatedDirection",
]
