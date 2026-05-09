"""
信号滤波模块 - Signal Filtering Module

提供多种信号滤波和融合方法，用于处理多源信号的噪声过滤、趋势提取和融合。

分层设计:
- P1 (默认开启): 卡尔曼滤波融合器 + 自适应加权平均
- P2 (默认开启): KAMA/FRAMA趋势滤波 + 异常检测模块
- P3 (默认关闭): Transformer注意力融合 + 粒子滤波 (高性能开销)

Usage:
    from finhack_pro.strategies.signal_filters import SignalFilterPipeline
    
    # 创建滤波管道
    pipeline = SignalFilterPipeline({
        'kalman': {'enabled': True},
        'kama': {'enabled': True, 'period': 10},
        'anomaly': {'enabled': True, 'threshold': 2.0},
        'transformer': {'enabled': False},  # 默认关闭
    })
    
    # 处理信号
    filtered_signals = pipeline.process(signals)
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
from pydantic import BaseModel, Field

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据结构定义
# ============================================================

class SignalType(str, Enum):
    """信号类型"""
    TECHNICAL = "technical"       # 技术面信号
    SENTIMENT = "sentiment"       # 情感信号
    FUNDAMENTAL = "fundamental"   # 基本面信号
    EVENT = "event"               # 事件信号
    COMBINED = "combined"         # 组合信号


@dataclass
class RawSignal:
    """原始信号数据结构"""
    source: str                    # 信号来源 (agent_name)
    signal_type: SignalType        # 信号类型
    value: float                   # 信号值 (-1 到 1, 负数看空, 正数看多)
    confidence: float              # 置信度 (0 到 1)
    timestamp: str = ""            # 时间戳
    metadata: Dict[str, Any] = field(default_factory=dict)


class FilteredSignal(BaseModel):
    """滤波后信号"""
    value: float = Field(description="滤波后信号值")
    confidence: float = Field(description="滤波后置信度")
    uncertainty: float = Field(default=0.0, description="不确定性估计")
    contributing_sources: List[str] = Field(default_factory=list, description="贡献来源")
    filter_applied: List[str] = Field(default_factory=list, description="应用的滤波器")
    anomaly_flag: bool = Field(default=False, description="异常标记")
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# 滤波器基类
# ============================================================

class BaseFilter(ABC):
    """滤波器基类"""
    
    name: str = "base_filter"
    priority: int = 100  # 执行优先级，数字越小越先执行
    performance_cost: str = "low"  # low/medium/high
    default_enabled: bool = True
    
    def __init__(self, enabled: bool = None, **kwargs):
        self.enabled = enabled if enabled is not None else self.default_enabled
        self.params = kwargs
        
    @abstractmethod
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用滤波器"""
        pass
    
    def _log(self, message: str) -> None:
        logger.debug(f"[{self.name}] {message}")


# ============================================================
# P1: 卡尔曼滤波融合器 (默认开启)
# ============================================================

class KalmanFilterFusion(BaseFilter):
    """卡尔曼滤波多源信号融合器
    
    使用卡尔曼滤波融合多个信号源的输出，输出带不确定性估计的融合信号。
    状态模型: 真实信号 + 偏置
    观测模型: 各源信号 = 真实信号 + 噪声
    
    特点:
    - 理论最优估计 (高斯假设下)
    - 输出不确定性估计
    - 自适应调整各源权重
    - 低计算开销，适合实时
    """
    
    name = "kalman_fusion"
    priority = 10
    performance_cost = "low"
    default_enabled = True
    
    def __init__(
        self,
        enabled: bool = None,
        dim_state: int = 2,           # 状态维度: [信号值, 偏置]
        signal_persistence: float = 0.95,  # 信号持续性
        process_noise: float = 0.01,  # 过程噪声
        source_noise: Optional[Dict[str, float]] = None,  # 各源噪声水平
    ):
        super().__init__(enabled)
        self.dim_state = dim_state
        self.signal_persistence = signal_persistence
        self.process_noise = process_noise
        self.source_noise = source_noise or {
            SignalType.TECHNICAL.value: 0.1,
            SignalType.SENTIMENT.value: 0.2,
            SignalType.FUNDAMENTAL.value: 0.15,
            SignalType.EVENT.value: 0.25,
        }
        
        # 初始化状态
        self._state = np.zeros(dim_state)
        self._P = np.eye(dim_state) * 1.0  # 状态协方差
        self._F = np.array([[signal_persistence, 0], [0, 0.9]])  # 状态转移
        self._Q = np.eye(dim_state) * process_noise  # 过程噪声协方差
        
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用卡尔曼滤波融合"""
        if not self.enabled or not signals:
            return signals
            
        # 按信号类型分组
        grouped = self._group_by_type(signals)
        
        # 构建观测向量
        observations, R, H = self._build_observation_model(grouped)
        
        if observations is None:
            return signals
            
        # 预测步骤
        self._state = self._F @ self._state
        self._P = self._F @ self._P @ self._F.T + self._Q
        
        # 更新步骤
        y = observations - H @ self._state  # 残差
        S = H @ self._P @ H.T + R           # 残差协方差
        K = self._P @ H.T @ np.linalg.inv(S)  # 卡尔曼增益
        
        self._state = self._state + K @ y
        self._P = (np.eye(self.dim_state) - K @ H) @ self._P
        
        # 提取融合结果
        fused_value = float(self._state[0])
        uncertainty = float(np.sqrt(self._P[0, 0]))
        
        # 更新所有信号的值和置信度
        for sig in signals:
            sig.value = fused_value
            sig.confidence = max(0, 1 - uncertainty)
            sig.metadata["kalman_uncertainty"] = uncertainty
            
        self._log(f"融合信号: value={fused_value:.3f}, uncertainty={uncertainty:.3f}")
        
        return signals
    
    def _group_by_type(self, signals: List[RawSignal]) -> Dict[str, List[RawSignal]]:
        """按信号类型分组"""
        grouped = {}
        for sig in signals:
            key = sig.signal_type.value
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(sig)
        return grouped
    
    def _build_observation_model(
        self, grouped: Dict[str, List[RawSignal]]
    ) -> Tuple[Optional[np.ndarray], np.ndarray, np.ndarray]:
        """构建观测模型"""
        # 定义观测顺序
        obs_order = [
            SignalType.TECHNICAL.value,
            SignalType.SENTIMENT.value,
            SignalType.FUNDAMENTAL.value,
            SignalType.EVENT.value,
        ]
        
        observations = []
        noise_vars = []
        
        for obs_type in obs_order:
            if obs_type in grouped and grouped[obs_type]:
                # 取该类型的平均信号值
                values = [s.value * s.confidence for s in grouped[obs_type]]
                weights = [s.confidence for s in grouped[obs_type]]
                avg_value = sum(values) / sum(weights) if sum(weights) > 0 else 0
                observations.append(avg_value)
                noise_vars.append(self.source_noise.get(obs_type, 0.2) ** 2)
        
        if not observations:
            return None, np.eye(1), np.zeros((1, 2))
            
        n_obs = len(observations)
        H = np.zeros((n_obs, 2))
        H[:, 0] = 1  # 观测只与信号值相关
        
        R = np.diag(noise_vars)
        
        return np.array(observations), R, H
    
    def get_fused_signal(self) -> Tuple[float, float]:
        """获取当前融合信号和不确定性"""
        return float(self._state[0]), float(np.sqrt(self._P[0, 0]))


# ============================================================
# P1: 自适应加权平均 (默认开启)
# ============================================================

class AdaptiveWeightedAverage(BaseFilter):
    """自适应加权平均滤波器
    
    基于历史信息系数(IC)动态调整各信号源的权重。
    
    特点:
    - 基于历史表现加权
    - 权重衰减机制
    - 低计算开销
    """
    
    name = "adaptive_weighted"
    priority = 20
    performance_cost = "low"
    default_enabled = True
    
    def __init__(
        self,
        enabled: bool = None,
        ic_decay: float = 0.95,      # IC衰减因子
        min_weight: float = 0.05,    # 最小权重
        lookback: int = 20,          # 回看期
    ):
        super().__init__(enabled)
        self.ic_decay = ic_decay
        self.min_weight = min_weight
        self.lookback = lookback
        
        # 各源历史IC
        self._source_ic: Dict[str, List[float]] = {}
        self._source_weights: Dict[str, float] = {}
        
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用自适应加权"""
        if not self.enabled or not signals:
            return signals
            
        # 计算各源权重
        weights = self._compute_weights(signals)
        
        # 加权平均
        total_weight = 0
        weighted_value = 0
        weighted_confidence = 0
        
        for sig in signals:
            source_key = f"{sig.source}_{sig.signal_type.value}"
            w = weights.get(source_key, 1.0 / len(signals))
            
            weighted_value += sig.value * sig.confidence * w
            weighted_confidence += sig.confidence * w
            total_weight += w
            
        if total_weight > 0:
            weighted_value /= total_weight
            weighted_confidence /= total_weight
            
        # 更新所有信号
        for sig in signals:
            sig.value = weighted_value
            sig.confidence = weighted_confidence
            sig.metadata["adaptive_weight"] = weights.get(
                f"{sig.source}_{sig.signal_type.value}", 0
            )
            
        self._log(f"加权信号: value={weighted_value:.3f}, confidence={weighted_confidence:.3f}")
        
        return signals
    
    def _compute_weights(self, signals: List[RawSignal]) -> Dict[str, float]:
        """计算各源权重"""
        weights = {}
        
        for sig in signals:
            source_key = f"{sig.source}_{sig.signal_type.value}"
            
            # 如果有历史IC，使用IC的绝对值作为权重基础
            if source_key in self._source_ic and self._source_ic[source_key]:
                ic_values = self._source_ic[source_key][-self.lookback:]
                avg_ic = np.mean(np.abs(ic_values))
                weights[source_key] = max(avg_ic, self.min_weight)
            else:
                # 无历史数据时使用均匀权重
                weights[source_key] = 1.0 / len(signals)
                
        # 归一化
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}
            
        return weights
    
    def update_ic(self, source: str, ic_value: float) -> None:
        """更新某源的信息系数"""
        if source not in self._source_ic:
            self._source_ic[source] = []
        self._source_ic[source].append(ic_value)
        
        # 保持回看期长度
        if len(self._source_ic[source]) > self.lookback * 2:
            self._source_ic[source] = self._source_ic[source][-self.lookback:]


# ============================================================
# P2: KAMA趋势滤波 (默认开启)
# ============================================================

class KAMAFilter(BaseFilter):
    """Kaufman自适应移动平均滤波器
    
    根据市场波动自适应调整平滑程度:
    - 趋势明显时快速响应
    - 震荡时慢速平滑
    
    特点:
    - 自适应波动
    - 低延迟
    - 低计算开销
    """
    
    name = "kama"
    priority = 30
    performance_cost = "low"
    default_enabled = True
    
    def __init__(
        self,
        enabled: bool = None,
        period: int = 10,           # 效率比率计算周期
        fast_sc: float = 2 / (2 + 1),   # 快速平滑常数
        slow_sc: float = 2 / (30 + 1),  # 慢速平滑常数
    ):
        super().__init__(enabled)
        self.period = period
        self.fast_sc = fast_sc
        self.slow_sc = slow_sc
        
        self._prev_value: Optional[float] = None
        self._price_history: List[float] = []
        
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用KAMA滤波"""
        if not self.enabled or not signals:
            return signals
            
        # 对每个信号应用KAMA
        for sig in signals:
            kama_value = self._compute_kama(sig.value)
            sig.value = kama_value
            sig.metadata["kama_applied"] = True
            
        return signals
    
    def _compute_kama(self, price: float) -> float:
        """计算KAMA值"""
        self._price_history.append(price)
        
        if len(self._price_history) < self.period + 1:
            self._prev_value = price
            return price
            
        # 计算效率比率 (ER)
        direction = abs(price - self._price_history[-self.period])
        volatility = sum(
            abs(self._price_history[i] - self._price_history[i-1])
            for i in range(-self.period + 1, 0)
        )
        
        er = direction / volatility if volatility > 0 else 0
        
        # 计算平滑常数 (SC)
        sc = er * (self.fast_sc - self.slow_sc) + self.slow_sc
        sc = sc ** 2  # 平方使响应更平滑
        
        # 计算KAMA
        if self._prev_value is None:
            kama = price
        else:
            kama = self._prev_value + sc * (price - self._prev_value)
            
        self._prev_value = kama
        
        # 保持历史长度
        if len(self._price_history) > self.period * 3:
            self._price_history = self._price_history[-self.period * 2:]
            
        return kama
    
    def reset(self) -> None:
        """重置滤波器状态"""
        self._prev_value = None
        self._price_history = []


# ============================================================
# P2: FRAMA趋势滤波 (默认开启)
# ============================================================

class FRAMAFilter(BaseFilter):
    """分形自适应移动平均滤波器
    
    基于价格序列的分形维度自适应调整平滑。
    
    特点:
    - 捕捉市场分形特征
    - 对非线性趋势敏感
    - 低计算开销
    """
    
    name = "frama"
    priority = 31
    performance_cost = "low"
    default_enabled = True
    
    def __init__(
        self,
        enabled: bool = None,
        period: int = 16,           # 分形计算周期
        fc: float = 1.0,            # 分形常数
    ):
        super().__init__(enabled)
        self.period = period
        self.fc = fc
        
        self._price_history: List[float] = []
        self._prev_frama: Optional[float] = None
        
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用FRAMA滤波"""
        if not self.enabled or not signals:
            return signals
            
        for sig in signals:
            frama_value = self._compute_frama(sig.value)
            sig.value = frama_value
            sig.metadata["frama_applied"] = True
            
        return signals
    
    def _compute_frama(self, price: float) -> float:
        """计算FRAMA值"""
        self._price_history.append(price)
        n = self.period
        
        if len(self._price_history) < n * 2:
            self._prev_frama = price
            return price
            
        prices = self._price_history[-n * 2:]
        
        # 计算三段的价格范围
        n3 = (max(prices[-n:]) - min(prices[-n:])) / n
        n2 = (max(prices[-n*2:-n]) - min(prices[-n*2:-n])) / n
        n1 = (max(prices) - min(prices)) / (n * 2)
        
        # 计算分形维度
        if n1 > 0 and n2 > 0 and n3 > 0:
            d = (math.log(n1 + n2) - math.log(n3)) / math.log(2)
        else:
            d = 1.0
            
        # 计算alpha
        alpha = math.exp(-self.fc * (d - 1))
        alpha = max(min(alpha, 1.0), 0.01)  # 限制在[0.01, 1]
        
        # 计算FRAMA
        if self._prev_frama is None:
            frama = price
        else:
            frama = alpha * price + (1 - alpha) * self._prev_frama
            
        self._prev_frama = frama
        
        # 保持历史长度
        if len(self._price_history) > self.period * 4:
            self._price_history = self._price_history[-self.period * 2:]
            
        return frama
    
    def reset(self) -> None:
        """重置滤波器状态"""
        self._price_history = []
        self._prev_frama = None


# ============================================================
# P2: 异常检测模块 (默认开启)
# ============================================================

class AnomalyDetector(BaseFilter):
    """信号异常检测模块
    
    使用统计方法检测异常信号:
    - Z-Score异常检测
    - IQR异常检测
    - MAD异常检测
    
    特点:
    - 多种检测方法
    - 低计算开销
    - 可配置阈值
    """
    
    name = "anomaly_detector"
    priority = 5  # 最先执行
    performance_cost = "low"
    default_enabled = True
    
    def __init__(
        self,
        enabled: bool = None,
        method: str = "zscore",     # zscore/iqr/mad
        threshold: float = 2.0,      # 异常阈值
        action: str = "flag",        # flag/remove/clip
    ):
        super().__init__(enabled)
        self.method = method
        self.threshold = threshold
        self.action = action
        
        self._signal_history: List[float] = []
        
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用异常检测"""
        if not self.enabled or not signals:
            return signals
            
        # 收集信号值
        values = [s.value for s in signals]
        self._signal_history.extend(values)
        
        # 保持历史长度
        if len(self._signal_history) > 1000:
            self._signal_history = self._signal_history[-500:]
            
        # 检测异常
        anomaly_mask = self._detect_anomalies(values)
        
        # 处理异常
        result = []
        for i, sig in enumerate(signals):
            if anomaly_mask[i]:
                sig.metadata["anomaly_detected"] = True
                self._log(f"异常信号: {sig.source}, value={sig.value:.3f}")
                
                if self.action == "remove":
                    continue  # 跳过异常信号
                elif self.action == "clip":
                    sig.value = self._clip_value(sig.value)
                    
            result.append(sig)
            
        return result if result else signals
    
    def _detect_anomalies(self, values: List[float]) -> List[bool]:
        """检测异常值"""
        if len(values) < 3:
            return [False] * len(values)
            
        values_arr = np.array(values)
        
        if self.method == "zscore":
            mean = np.mean(values_arr)
            std = np.std(values_arr)
            if std > 0:
                z_scores = np.abs((values_arr - mean) / std)
                return z_scores > self.threshold
            return [False] * len(values)
            
        elif self.method == "iqr":
            q1 = np.percentile(values_arr, 25)
            q3 = np.percentile(values_arr, 75)
            iqr = q3 - q1
            lower = q1 - self.threshold * iqr
            upper = q3 + self.threshold * iqr
            return [(v < lower or v > upper) for v in values]
            
        elif self.method == "mad":
            median = np.median(values_arr)
            mad = np.median(np.abs(values_arr - median))
            if mad > 0:
                modified_z = 0.6745 * (values_arr - median) / mad
                return np.abs(modified_z) > self.threshold
            return [False] * len(values)
            
        return [False] * len(values)
    
    def _clip_value(self, value: float) -> float:
        """裁剪异常值到合理范围"""
        if not self._signal_history:
            return value
        mean = np.mean(self._signal_history)
        std = np.std(self._signal_history)
        return np.clip(value, mean - self.threshold * std, mean + self.threshold * std)


# ============================================================
# P3: Transformer注意力融合 (默认关闭, 高性能开销)
# ============================================================

class TransformerAttentionFusion(BaseFilter):
    """Transformer注意力机制信号融合
    
    使用自注意力机制学习多源信号的最优融合权重。
    
    特点:
    - 自动学习最优权重
    - 可解释性较好 (注意力权重)
    - 需要训练数据
    - 高计算开销
    """
    
    name = "transformer_fusion"
    priority = 50
    performance_cost = "high"
    default_enabled = False  # 默认关闭
    
    def __init__(
        self,
        enabled: bool = None,
        d_model: int = 64,
        n_heads: int = 4,
        n_layers: int = 2,
        dropout: float = 0.1,
    ):
        super().__init__(enabled)
        self.d_model = d_model
        self.n_heads = n_heads
        self.n_layers = n_layers
        self.dropout = dropout
        
        # 注意力权重缓存
        self._attention_weights: Optional[np.ndarray] = None
        self._is_initialized = False
        
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用Transformer注意力融合"""
        if not self.enabled or not signals:
            return signals
            
        # 检查是否有torch
        try:
            import torch
            import torch.nn as nn
            import torch.nn.functional as F
        except ImportError:
            self._log("PyTorch未安装, 跳过Transformer融合")
            return signals
            
        # 简化实现: 使用点积注意力
        # 构建信号特征矩阵
        features = []
        for sig in signals:
            feat = [
                sig.value,
                sig.confidence,
                self._encode_signal_type(sig.signal_type),
            ]
            features.append(feat)
            
        features_arr = np.array(features)
        
        # 计算注意力权重
        # Q = K = V = features
        d_k = features_arr.shape[1]
        scores = features_arr @ features_arr.T / np.sqrt(d_k)
        attention_weights = self._softmax(scores)
        
        # 加权融合
        fused = attention_weights @ features_arr[:, 0:1]  # 只取value列
        
        # 更新信号
        for i, sig in enumerate(signals):
            sig.value = float(fused[i, 0])
            sig.metadata["attention_weight"] = float(attention_weights[i].mean())
            
        self._attention_weights = attention_weights
        self._log(f"Transformer融合完成, 注意力权重: {attention_weights.mean(axis=0)}")
        
        return signals
    
    def _encode_signal_type(self, signal_type: SignalType) -> float:
        """编码信号类型为数值"""
        encoding = {
            SignalType.TECHNICAL: 0.1,
            SignalType.SENTIMENT: 0.2,
            SignalType.FUNDAMENTAL: 0.3,
            SignalType.EVENT: 0.4,
            SignalType.COMBINED: 0.5,
        }
        return encoding.get(signal_type, 0.0)
    
    def _softmax(self, x: np.ndarray) -> np.ndarray:
        """计算softmax"""
        exp_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
        return exp_x / np.sum(exp_x, axis=-1, keepdims=True)
    
    def get_attention_weights(self) -> Optional[np.ndarray]:
        """获取最近的注意力权重"""
        return self._attention_weights


# ============================================================
# P3: 粒子滤波 (默认关闭, 高性能开销)
# ============================================================

class ParticleFilter(BaseFilter):
    """粒子滤波信号融合
    
    使用蒙特卡洛方法进行非高斯、非线性系统的状态估计。
    
    特点:
    - 无高斯假设
    - 适合非线性系统
    - 高计算开销
    - 输出完整后验分布
    """
    
    name = "particle_filter"
    priority = 15
    performance_cost = "high"
    default_enabled = False  # 默认关闭
    
    def __init__(
        self,
        enabled: bool = None,
        n_particles: int = 1000,
        resample_threshold: float = 0.5,  # 有效粒子数阈值
    ):
        super().__init__(enabled)
        self.n_particles = n_particles
        self.resample_threshold = resample_threshold
        
        # 粒子状态
        self._particles: Optional[np.ndarray] = None
        self._weights: Optional[np.ndarray] = None
        
    def apply(self, signals: List[RawSignal]) -> List[RawSignal]:
        """应用粒子滤波"""
        if not self.enabled or not signals:
            return signals
            
        # 初始化粒子
        if self._particles is None:
            self._initialize_particles(signals)
            
        # 预测步骤: 添加过程噪声
        self._predict()
        
        # 更新步骤: 根据观测更新权重
        self._update(signals)
        
        # 重采样
        if self._needs_resample():
            self._resample()
            
        # 计算估计值
        estimate = self._estimate()
        
        # 更新信号
        for sig in signals:
            sig.value = estimate["mean"]
            sig.confidence = max(0, 1 - estimate["std"])
            sig.metadata["particle_estimate"] = estimate
            
        self._log(f"粒子滤波: mean={estimate['mean']:.3f}, std={estimate['std']:.3f}")
        
        return signals
    
    def _initialize_particles(self, signals: List[RawSignal]) -> None:
        """初始化粒子"""
        values = [s.value for s in signals]
        mean = np.mean(values)
        std = np.std(values) if len(values) > 1 else 0.1
        
        # 从先验分布采样
        self._particles = np.random.normal(mean, std, self.n_particles)
        self._weights = np.ones(self.n_particles) / self.n_particles
        
    def _predict(self) -> None:
        """预测步骤"""
        # 简单的随机游走模型
        process_noise = 0.01
        self._particles += np.random.normal(0, process_noise, self.n_particles)
        
    def _update(self, signals: List[RawSignal]) -> None:
        """更新步骤"""
        for sig in signals:
            # 计算似然
            obs_noise = 0.1 * (1 - sig.confidence + 0.1)
            likelihood = np.exp(
                -0.5 * ((self._particles - sig.value) / obs_noise) ** 2
            )
            self._weights *= likelihood
            
        # 归一化权重
        self._weights /= np.sum(self._weights)
        
    def _needs_resample(self) -> bool:
        """判断是否需要重采样"""
        # 有效粒子数
        n_eff = 1 / np.sum(self._weights ** 2)
        return n_eff < self.n_particles * self.resample_threshold
    
    def _resample(self) -> None:
        """系统重采样"""
        indices = np.random.choice(
            self.n_particles,
            size=self.n_particles,
            p=self._weights,
        )
        self._particles = self._particles[indices]
        self._weights = np.ones(self.n_particles) / self.n_particles
        
    def _estimate(self) -> Dict[str, float]:
        """计算估计值"""
        mean = float(np.average(self._particles, weights=self._weights))
        variance = float(np.average(
            (self._particles - mean) ** 2,
            weights=self._weights,
        ))
        return {
            "mean": mean,
            "std": np.sqrt(variance),
            "median": float(np.median(self._particles)),
            "q05": float(np.percentile(self._particles, 5)),
            "q95": float(np.percentile(self._particles, 95)),
        }
    
    def reset(self) -> None:
        """重置滤波器"""
        self._particles = None
        self._weights = None


# ============================================================
# 信号滤波管道
# ============================================================

class SignalFilterPipeline:
    """信号滤波管道
    
    可配置的滤波器组合，按优先级顺序执行。
    
    Usage:
        pipeline = SignalFilterPipeline({
            'kalman': {'enabled': True},
            'adaptive_weighted': {'enabled': True},
            'kama': {'enabled': True, 'period': 10},
            'frama': {'enabled': False},
            'anomaly': {'enabled': True, 'threshold': 2.0},
            'transformer': {'enabled': False},  # 高开销, 默认关闭
            'particle': {'enabled': False},     # 高开销, 默认关闭
        })
        
        filtered_signals = pipeline.process(raw_signals)
    """
    
    # 可用滤波器注册表
    FILTER_REGISTRY = {
        "kalman": KalmanFilterFusion,
        "adaptive_weighted": AdaptiveWeightedAverage,
        "kama": KAMAFilter,
        "frama": FRAMAFilter,
        "anomaly": AnomalyDetector,
        "transformer": TransformerAttentionFusion,
        "particle": ParticleFilter,
    }
    
    def __init__(self, config: Dict[str, Dict[str, Any]] = None):
        """初始化滤波管道
        
        Args:
            config: 滤波器配置字典
                key: 滤波器名称
                value: 滤波器参数 (enabled, 以及各滤波器特有参数)
        """
        self.config = config or {}
        self.filters: List[BaseFilter] = []
        
        self._setup_filters()
        
    def _setup_filters(self) -> None:
        """根据配置创建滤波器"""
        for name, params in self.config.items():
            if name not in self.FILTER_REGISTRY:
                logger.warning(f"未知滤波器: {name}")
                continue
                
            filter_cls = self.FILTER_REGISTRY[name]
            filter_instance = filter_cls(**params)
            self.filters.append(filter_instance)
            
        # 按优先级排序
        self.filters.sort(key=lambda f: f.priority)
        
        enabled_filters = [f.name for f in self.filters if f.enabled]
        logger.info(f"滤波管道初始化: 启用 {len(enabled_filters)} 个滤波器: {enabled_filters}")
        
    def process(self, signals: List[RawSignal]) -> List[FilteredSignal]:
        """处理信号
        
        Args:
            signals: 原始信号列表
            
        Returns:
            滤波后信号列表
        """
        if not signals:
            return []
            
        # 复制信号避免修改原始数据
        processed = [
            RawSignal(
                source=s.source,
                signal_type=s.signal_type,
                value=s.value,
                confidence=s.confidence,
                timestamp=s.timestamp,
                metadata=s.metadata.copy(),
            )
            for s in signals
        ]
        
        # 依次应用滤波器
        applied_filters = []
        for f in self.filters:
            if f.enabled:
                processed = f.apply(processed)
                applied_filters.append(f.name)
                
        # 转换为FilteredSignal
        result = []
        for s in processed:
            filtered = FilteredSignal(
                value=s.value,
                confidence=s.confidence,
                uncertainty=s.metadata.get("kalman_uncertainty", 0.0),
                contributing_sources=[s.source],
                filter_applied=applied_filters,
                anomaly_flag=s.metadata.get("anomaly_detected", False),
                metadata=s.metadata,
            )
            result.append(filtered)
            
        return result
    
    def add_filter(self, name: str, params: Dict[str, Any]) -> None:
        """动态添加滤波器"""
        if name not in self.FILTER_REGISTRY:
            raise ValueError(f"未知滤波器: {name}")
            
        filter_cls = self.FILTER_REGISTRY[name]
        filter_instance = filter_cls(**params)
        self.filters.append(filter_instance)
        self.filters.sort(key=lambda f: f.priority)
        
    def remove_filter(self, name: str) -> bool:
        """移除滤波器"""
        for i, f in enumerate(self.filters):
            if f.name == name:
                self.filters.pop(i)
                return True
        return False
    
    def get_filter_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有滤波器状态"""
        return {
            f.name: {
                "enabled": f.enabled,
                "priority": f.priority,
                "performance_cost": f.performance_cost,
            }
            for f in self.filters
        }


# ============================================================
# 便捷函数
# ============================================================

def create_default_pipeline(enable_high_cost: bool = False) -> SignalFilterPipeline:
    """创建默认滤波管道
    
    Args:
        enable_high_cost: 是否启用高开销滤波器
        
    Returns:
        配置好的滤波管道
    """
    config = {
        "anomaly": {"enabled": True, "threshold": 2.0},
        "kalman": {"enabled": True},
        "adaptive_weighted": {"enabled": True},
        "kama": {"enabled": True, "period": 10},
        "frama": {"enabled": False},  # 与KAMA功能重叠, 默认关闭
        "transformer": {"enabled": enable_high_cost},
        "particle": {"enabled": enable_high_cost},
    }
    return SignalFilterPipeline(config)


__all__ = [
    # 数据结构
    "SignalType",
    "RawSignal",
    "FilteredSignal",
    # 滤波器
    "BaseFilter",
    "KalmanFilterFusion",
    "AdaptiveWeightedAverage",
    "KAMAFilter",
    "FRAMAFilter",
    "AnomalyDetector",
    "TransformerAttentionFusion",
    "ParticleFilter",
    # 管道
    "SignalFilterPipeline",
    # 便捷函数
    "create_default_pipeline",
]
