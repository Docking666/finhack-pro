"""
差异化策略框架 - Niche Strategy Framework

实现"机构做广度，个人做深度"的差异化策略定位：
- 小市值股票的 niche 模式挖掘
- 另类数据与技术面的交叉验证
- 公告事件后的情绪博弈分析
- 专注细分领域的深度跟踪

核心思想：利用LLM的推理能力分析公告文本的隐含信息，
利用多Agent辩论机制评估小事件的市场影响，
利用共享记忆积累特定股票的"事件-反应"历史模式。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from finhack_pro.strategies.base import (
    BaseStrategy,
    Context,
    Signal,
    SignalDirection,
    BarData,
    TickData,
)
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class NicheType(str, Enum):
    """差异化策略类型"""
    MICRO_CAP = "micro_cap"              # 小市值策略
    EVENT_DRIVEN = "event_driven"        # 事件驱动策略
    SENTIMENT_REVERSAL = "sentiment_reversal"  # 情绪反转策略
    DRAGON_TIGER_FOLLOW = "dragon_tiger_follow"  # 龙虎榜跟随策略
    ALTERNATIVE_CROSS = "alternative_cross"  # 另类数据交叉验证策略


@dataclass
class NicheSignal:
    """差异化策略信号"""
    symbol: str
    signal_type: NicheType
    direction: SignalDirection
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    position_ratio: float  # 建议仓位比例
    holding_period: int    # 预计持有天数
    reasoning: str
    risk_factors: List[str] = field(default_factory=list)
    supporting_data: Dict[str, Any] = field(default_factory=dict)


class NicheStrategyConfig:
    """差异化策略配置
    
    定义策略的参数和约束条件。
    """
    
    def __init__(
        self,
        niche_type: NicheType = NicheType.MICRO_CAP,
        max_position_ratio: float = 0.1,  # 单只股票最大仓位
        max_total_position: float = 0.6,  # 总仓位上限
        min_confidence: float = 0.6,       # 最低置信度
        max_market_cap: float = 100e8,     # 最大市值(100亿)
        min_turnover: float = 0.02,        # 最小换手率
        exclude_st: bool = True,           # 排除ST股
        exclude_new: bool = True,          # 排除次新股(上市<1年)
    ):
        self.niche_type = niche_type
        self.max_position_ratio = max_position_ratio
        self.max_total_position = max_total_position
        self.min_confidence = min_confidence
        self.max_market_cap = max_market_cap
        self.min_turnover = min_turnover
        self.exclude_st = exclude_st
        self.exclude_new = exclude_new


class NicheStrategy(BaseStrategy):
    """差异化策略基类
    
    实现"机构做广度，个人做深度"的投资理念。
    通过专注细分领域、挖掘微观事件、另类数据交叉验证等方式，
    寻找机构忽视的投资机会。
    
    Usage:
        config = NicheStrategyConfig(
            niche_type=NicheType.MICRO_CAP,
            max_market_cap=50e8,  # 50亿以下市值
        )
        strategy = NicheStrategy(config)
        signals = strategy.generate_signals(context, symbols)
    """
    
    def __init__(self, config: Optional[NicheStrategyConfig] = None):
        super().__init__()
        self.config = config or NicheStrategyConfig()
        self._watch_list: List[str] = []
        self._event_history: Dict[str, List[Dict]] = {}  # 股票 -> 事件历史
        self._pattern_memory: Dict[str, Dict] = {}       # 股票 -> 历史模式
        
    def on_init(self, context: Context) -> None:
        """策略初始化"""
        self._logger.info(f"初始化差异化策略: {self.config.niche_type.value}")
        
    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        """K线回调"""
        signals = []
        
        # 根据策略类型生成信号
        if self.config.niche_type == NicheType.MICRO_CAP:
            signals = self._micro_cap_strategy(context, bar)
        elif self.config.niche_type == NicheType.EVENT_DRIVEN:
            signals = self._event_driven_strategy(context, bar)
        elif self.config.niche_type == NicheType.SENTIMENT_REVERSAL:
            signals = self._sentiment_reversal_strategy(context, bar)
        elif self.config.niche_type == NicheType.DRAGON_TIGER_FOLLOW:
            signals = self._dragon_tiger_follow_strategy(context, bar)
        elif self.config.niche_type == NicheType.ALTERNATIVE_CROSS:
            signals = self._alternative_cross_strategy(context, bar)
            
        return signals
    
    # ============================================================
    # 策略实现
    # ============================================================
    
    def _micro_cap_strategy(self, context: Context, bar: BarData) -> List[Signal]:
        """小市值策略
        
        专注于小市值股票，挖掘被机构忽视的机会。
        特点：
        - 市值小，机构覆盖少
        - 流动性适中，适合小资金
        - 事件驱动特征明显
        """
        signals = []
        
        # 检查市值约束
        market_cap = bar.extra.get("market_cap", 0)
        if market_cap > self.config.max_market_cap:
            return signals
            
        # 检查换手率
        turnover = bar.extra.get("turnover", 0)
        if turnover < self.config.min_turnover:
            return signals
            
        # 检查ST和次新股
        if self.config.exclude_st and bar.extra.get("is_st", False):
            return signals
        if self.config.exclude_new and bar.extra.get("days_listed", 1000) < 365:
            return signals
            
        # 小市值策略逻辑
        # 1. 成交量放大
        volume_ratio = bar.extra.get("volume_ratio", 1.0)
        if volume_ratio > 2.0:
            # 2. 价格突破
            ma20 = bar.extra.get("ma20", bar.close)
            if bar.close > ma20 * 1.02:
                signal = Signal(
                    symbol=bar.symbol,
                    direction=SignalDirection.BUY,
                    price=bar.close,
                    volume=int(context.portfolio.cash * self.config.max_position_ratio / bar.close),
                    stop_loss=bar.close * 0.95,
                    take_profit=bar.close * 1.15,
                    strategy_name=f"niche_{self.config.niche_type.value}",
                    extra={
                        "reasoning": f"小市值放量突破: 市值{market_cap/1e8:.1f}亿, 量比{volume_ratio:.1f}",
                        "niche_type": self.config.niche_type.value,
                    }
                )
                signals.append(signal)
                
        return signals
    
    def _event_driven_strategy(self, context: Context, bar: BarData) -> List[Signal]:
        """事件驱动策略
        
        基于微观事件(公告、龙虎榜等)的交易策略。
        特点：
        - 事件发生后快速响应
        - 利用LLM分析事件隐含信息
        - 结合历史模式判断
        """
        signals = []
        
        # 获取该股票的事件历史
        events = self._event_history.get(bar.symbol, [])
        if not events:
            return signals
            
        # 分析最近事件
        recent_events = [e for e in events if 
                        datetime.fromisoformat(e.get("time", "2000-01-01")) > 
                        datetime.now() - timedelta(days=7)]
        
        if not recent_events:
            return signals
            
        # 判断事件影响
        for event in recent_events:
            impact_level = event.get("impact_level", "low")
            impact_direction = event.get("impact_direction", "neutral")
            
            if impact_level in ["critical", "high"] and impact_direction != "neutral":
                # 高影响事件，生成信号
                direction = SignalDirection.BUY if impact_direction == "positive" else SignalDirection.SELL
                
                signal = Signal(
                    symbol=bar.symbol,
                    direction=direction,
                    price=bar.close,
                    volume=int(context.portfolio.cash * self.config.max_position_ratio / bar.close),
                    stop_loss=bar.close * (0.92 if direction == SignalDirection.BUY else 1.08),
                    take_profit=bar.close * (1.20 if direction == SignalDirection.BUY else 0.85),
                    strategy_name=f"niche_{self.config.niche_type.value}",
                    extra={
                        "reasoning": f"事件驱动: {event.get('title', '未知事件')}",
                        "event_id": event.get("id", ""),
                        "niche_type": self.config.niche_type.value,
                    }
                )
                signals.append(signal)
                break  # 只取最近一个高影响事件
                
        return signals
    
    def _sentiment_reversal_strategy(self, context: Context, bar: BarData) -> List[Signal]:
        """情绪反转策略
        
        基于舆情数据的反转策略。
        特点：
        - 极度悲观时买入
        - 极度乐观时卖出
        - 结合技术面确认
        """
        signals = []
        
        # 获取舆情数据
        sentiment_score = bar.extra.get("sentiment_score", 0.5)
        sentiment_trend = bar.extra.get("sentiment_trend", "stable")
        
        # 极度悲观反转
        if sentiment_score < 0.2 and sentiment_trend == "falling":
            # 技术面确认：超卖
            rsi = bar.extra.get("rsi", 50)
            if rsi < 30:
                signal = Signal(
                    symbol=bar.symbol,
                    direction=SignalDirection.BUY,
                    price=bar.close,
                    volume=int(context.portfolio.cash * self.config.max_position_ratio / bar.close),
                    stop_loss=bar.close * 0.90,
                    take_profit=bar.close * 1.25,
                    strategy_name=f"niche_{self.config.niche_type.value}",
                    extra={
                        "reasoning": f"情绪极度悲观反转: 情绪分数{sentiment_score:.2f}, RSI{rsi:.0f}",
                        "niche_type": self.config.niche_type.value,
                    }
                )
                signals.append(signal)
                
        # 极度乐观反转
        elif sentiment_score > 0.8 and sentiment_trend == "rising":
            # 检查是否持仓
            position = context.portfolio.get_position(bar.symbol)
            if position.get("volume", 0) > 0:
                signal = Signal(
                    symbol=bar.symbol,
                    direction=SignalDirection.SELL,
                    price=bar.close,
                    volume=position.get("volume", 0),
                    strategy_name=f"niche_{self.config.niche_type.value}",
                    extra={
                        "reasoning": f"情绪极度乐观止盈: 情绪分数{sentiment_score:.2f}",
                        "niche_type": self.config.niche_type.value,
                    }
                )
                signals.append(signal)
                
        return signals
    
    def _dragon_tiger_follow_strategy(self, context: Context, bar: BarData) -> List[Signal]:
        """龙虎榜跟随策略
        
        跟踪龙虎榜游资和机构的动向。
        特点：
        - 识别知名游资的操作
        - 分析买卖力量对比
        - 结合历史胜率
        """
        signals = []
        
        # 获取龙虎榜数据
        dragon_tiger = bar.extra.get("dragon_tiger", {})
        if not dragon_tiger:
            return signals
            
        net_buy = dragon_tiger.get("net_buy", 0)
        buyers = dragon_tiger.get("buyers", [])
        sellers = dragon_tiger.get("sellers", [])
        
        # 净买入且买方实力强
        if net_buy > 0:
            # 检查是否有知名游资
            famous_buyers = [b for b in buyers if b.get("is_famous", False)]
            
            if famous_buyers or net_buy > 1e8:  # 有知名游资或净买入过亿
                signal = Signal(
                    symbol=bar.symbol,
                    direction=SignalDirection.BUY,
                    price=bar.close,
                    volume=int(context.portfolio.cash * self.config.max_position_ratio / bar.close),
                    stop_loss=bar.close * 0.93,
                    take_profit=bar.close * 1.12,
                    strategy_name=f"niche_{self.config.niche_type.value}",
                    extra={
                        "reasoning": f"龙虎榜跟随: 净买入{net_buy/1e8:.2f}亿, 知名游资{len(famous_buyers)}家",
                        "dragon_tiger_data": dragon_tiger,
                        "niche_type": self.config.niche_type.value,
                    }
                )
                signals.append(signal)
                
        return signals
    
    def _alternative_cross_strategy(self, context: Context, bar: BarData) -> List[Signal]:
        """另类数据交叉验证策略
        
        综合多种另类数据进行交叉验证。
        特点：
        - 多维度数据验证
        - 提高信号可靠性
        - 降低假信号率
        """
        signals = []
        
        # 收集各维度信号
        signals_count = 0
        total_confidence = 0.0
        reasoning_parts = []
        
        # 1. 技术面信号
        rsi = bar.extra.get("rsi", 50)
        macd_signal = bar.extra.get("macd_signal", "neutral")
        if rsi < 30 or (macd_signal == "golden_cross"):
            signals_count += 1
            total_confidence += 0.3
            reasoning_parts.append(f"技术面看多(RSI={rsi:.0f})")
            
        # 2. 资金面信号
        net_inflow = bar.extra.get("net_inflow", 0)
        if net_inflow > 0:
            signals_count += 1
            total_confidence += 0.25
            reasoning_parts.append(f"资金净流入{net_inflow/1e6:.0f}万")
            
        # 3. 舆情信号
        sentiment_score = bar.extra.get("sentiment_score", 0.5)
        if sentiment_score > 0.6:
            signals_count += 1
            total_confidence += 0.2
            reasoning_parts.append(f"舆情偏多({sentiment_score:.2f})")
            
        # 4. 事件信号
        has_positive_event = bar.extra.get("has_positive_event", False)
        if has_positive_event:
            signals_count += 1
            total_confidence += 0.25
            reasoning_parts.append("存在正面事件")
            
        # 需要至少3个维度共振
        if signals_count >= 3:
            confidence = total_confidence / signals_count
            if confidence >= self.config.min_confidence:
                signal = Signal(
                    symbol=bar.symbol,
                    direction=SignalDirection.BUY,
                    price=bar.close,
                    volume=int(context.portfolio.cash * self.config.max_position_ratio / bar.close),
                    stop_loss=bar.close * 0.94,
                    take_profit=bar.close * 1.18,
                    strategy_name=f"niche_{self.config.niche_type.value}",
                    extra={
                        "reasoning": f"多维度共振({signals_count}个信号): " + "; ".join(reasoning_parts),
                        "confidence": confidence,
                        "signal_count": signals_count,
                        "niche_type": self.config.niche_type.value,
                    }
                )
                signals.append(signal)
                
        return signals
    
    # ============================================================
    # 事件管理
    # ============================================================
    
    def add_event(self, symbol: str, event: Dict[str, Any]) -> None:
        """添加事件到历史记录
        
        Args:
            symbol: 股票代码
            event: 事件数据
        """
        if symbol not in self._event_history:
            self._event_history[symbol] = []
        self._event_history[symbol].append(event)
        
        # 限制历史长度
        if len(self._event_history[symbol]) > 100:
            self._event_history[symbol] = self._event_history[symbol][-100:]
            
    def get_event_pattern(self, symbol: str) -> Dict[str, Any]:
        """获取股票的事件-反应历史模式
        
        Args:
            symbol: 股票代码
            
        Returns:
            历史模式统计
        """
        events = self._event_history.get(symbol, [])
        if not events:
            return {}
            
        # 统计各类事件的市场反应
        pattern = {
            "total_events": len(events),
            "by_type": {},
            "avg_return_after_positive": 0.0,
            "avg_return_after_negative": 0.0,
        }
        
        positive_returns = []
        negative_returns = []
        
        for event in events:
            event_type = event.get("type", "unknown")
            if event_type not in pattern["by_type"]:
                pattern["by_type"][event_type] = {"count": 0, "avg_return": 0.0}
            pattern["by_type"][event_type]["count"] += 1
            
            # 记录后续收益
            subsequent_return = event.get("subsequent_return")
            if subsequent_return is not None:
                if event.get("impact_direction") == "positive":
                    positive_returns.append(subsequent_return)
                elif event.get("impact_direction") == "negative":
                    negative_returns.append(subsequent_return)
                    
        if positive_returns:
            pattern["avg_return_after_positive"] = sum(positive_returns) / len(positive_returns)
        if negative_returns:
            pattern["avg_return_after_negative"] = sum(negative_returns) / len(negative_returns)
            
        return pattern
    
    # ============================================================
    # 工具方法
    # ============================================================
    
    def update_watch_list(self, symbols: List[str]) -> None:
        """更新监控列表
        
        Args:
            symbols: 股票代码列表
        """
        self._watch_list = symbols
        self._logger.info(f"更新监控列表: {len(symbols)}只股票")
        
    def get_watch_list(self) -> List[str]:
        """获取当前监控列表"""
        return self._watch_list.copy()
    
    def generate_signals(
        self,
        context: Context,
        symbols: List[str],
        market_data: Optional[Dict[str, BarData]] = None,
    ) -> List[NicheSignal]:
        """批量生成差异化策略信号
        
        Args:
            context: 策略上下文
            symbols: 股票代码列表
            market_data: 市场数据
            
        Returns:
            NicheSignal列表
        """
        all_signals = []
        
        for symbol in symbols:
            if market_data and symbol in market_data:
                bar = market_data[symbol]
                signals = self.on_bar(context, bar)
                
                for sig in signals:
                    niche_signal = NicheSignal(
                        symbol=sig.symbol,
                        signal_type=self.config.niche_type,
                        direction=sig.direction,
                        confidence=sig.extra.get("confidence", 0.5),
                        entry_price=sig.price,
                        stop_loss=sig.stop_loss,
                        take_profit=sig.take_profit,
                        position_ratio=self.config.max_position_ratio,
                        holding_period=5,  # 默认持有5天
                        reasoning=sig.extra.get("reasoning", ""),
                        supporting_data=sig.extra,
                    )
                    all_signals.append(niche_signal)
                    
        # 按置信度排序
        all_signals.sort(key=lambda s: s.confidence, reverse=True)
        
        return all_signals


# ============================================================
# 策略工厂
# ============================================================

def create_niche_strategy(
    niche_type: str = "micro_cap",
    **kwargs
) -> NicheStrategy:
    """创建差异化策略实例
    
    Args:
        niche_type: 策略类型 (micro_cap/event_driven/sentiment_reversal/dragon_tiger_follow/alternative_cross)
        **kwargs: 策略参数
        
    Returns:
        NicheStrategy实例
    """
    type_map = {
        "micro_cap": NicheType.MICRO_CAP,
        "event_driven": NicheType.EVENT_DRIVEN,
        "sentiment_reversal": NicheType.SENTIMENT_REVERSAL,
        "dragon_tiger_follow": NicheType.DRAGON_TIGER_FOLLOW,
        "alternative_cross": NicheType.ALTERNATIVE_CROSS,
    }
    
    config = NicheStrategyConfig(
        niche_type=type_map.get(niche_type, NicheType.MICRO_CAP),
        **kwargs
    )
    
    return NicheStrategy(config)
