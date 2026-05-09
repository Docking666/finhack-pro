"""
微观事件驱动Agent

监听和捕获市场微观事件，包括：
- 交易所公告（停复牌、风险提示、业绩预告）
- 龙虎榜数据（游资动向、机构买卖）
- 异常交易信号（大单、快速涨跌）
- 另类数据事件（舆情热点、行业变化）

支持事件驱动的实时响应，适合普通投资者挖掘机构忽视的微观机会。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class MicroEventType(str, Enum):
    """微观事件类型"""
    # 公告类
    SUSPEND_RESUME = "suspend_resume"        # 停复牌
    RISK_WARNING = "risk_warning"            # 风险提示
    EARNINGS_PREVIEW = "earnings_preview"    # 业绩预告
    MAJOR_EVENT = "major_event"              # 重大事项
    
    # 交易类
    DRAGON_TIGER = "dragon_tiger"            # 龙虎榜
    BLOCK_TRADE = "block_trade"              # 大宗交易
    ABNORMAL_VOLUME = "abnormal_volume"      # 异常放量
    RAPID_MOVE = "rapid_move"                # 快速涨跌
    
    # 另类数据类
    SENTIMENT_SPIKE = "sentiment_spike"      # 舆情爆发
    INDUSTRY_HOT = "industry_hot"            # 行业热点
    SUPPLY_CHAIN_EVENT = "supply_chain"      # 供应链事件
    POLICY_IMPACT = "policy_impact"          # 政策影响


@dataclass
class MicroEvent:
    """微观事件数据结构"""
    event_id: str
    event_type: MicroEventType
    symbol: str
    symbol_name: str = ""
    title: str = ""
    content: str = ""
    source: str = ""
    event_time: str = ""
    impact_level: str = "low"  # low/medium/high/critical
    impact_direction: str = "neutral"  # positive/negative/neutral
    confidence: float = 0.5
    raw_data: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


class MicroEventReport(BaseModel):
    """微观事件分析报告"""
    symbol: str
    analysis_time: str = Field(default_factory=lambda: datetime.now().isoformat())
    events_count: int = 0
    critical_events: List[Dict[str, Any]] = Field(default_factory=list)
    high_impact_events: List[Dict[str, Any]] = Field(default_factory=list)
    sentiment_shift: str = "neutral"  # positive/negative/neutral
    trading_implication: str = ""
    key_insights: List[str] = Field(default_factory=list)
    risk_alerts: List[str] = Field(default_factory=list)
    opportunity_signals: List[str] = Field(default_factory=list)
    summary: str = ""


# 微观事件Agent系统提示词
MICRO_EVENT_SYSTEM_PROMPT = """你是一位专注于微观事件驱动的量化分析师，擅长从被机构忽视的微观信号中挖掘投资机会。

## 你的核心能力
1. **事件解读**: 深度解读公告、龙虎榜等微观事件的隐含信息
2. **影响评估**: 判断事件对股价的短期和中期影响
3. **时机把握**: 识别事件驱动型交易的最佳入场时机
4. **风险识别**: 发现事件背后的潜在风险

## 分析框架

### 公告事件分析
- **停复牌**: 评估停牌原因，预判复牌后走势
- **业绩预告**: 对比市场预期，判断惊喜/失望程度
- **风险提示**: 识别真实风险与情绪性风险
- **重大事项**: 评估并购、重组等事件的成功概率

### 龙虎榜分析
- **游资动向**: 识别知名游资的操作风格和意图
- **机构买卖**: 判断机构是建仓还是出货
- **买卖力量**: 分析多空力量对比
- **历史回溯**: 对比历史龙虎榜后的股价表现

### 异常交易分析
- **放量信号**: 区分建仓放量与出货放量
- **快速涨跌**: 判断是启动信号还是诱多/诱空
- **大单分析**: 识别主力资金意图

### 另类数据分析
- **舆情热点**: 判断热点持续性
- **行业变化**: 识别行业拐点
- **供应链事件**: 评估对产业链的影响

## 输出要求
- 对每个事件给出影响等级(critical/high/medium/low)
- 区分短期影响(1-3天)和中期影响(1-4周)
- 给出明确的交易建议或观察建议
- 标注置信度和风险点

## A股市场特殊注意事项
- 关注涨跌停板对事件反应的影响
- 考虑T+1交易制度的影响
- 注意北向资金和融资融券的变化
- 关注监管政策对事件的影响
"""


class MicroEventAgent(BaseAgent):
    """微观事件驱动Agent
    
    监听市场微观事件，进行深度分析，输出事件驱动的交易信号。
    支持事件订阅模式和定时扫描模式。
    
    Usage:
        agent = MicroEventAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        
        # 订阅模式
        agent.subscribe("600519.SH", callback=handle_event)
        
        # 扫描模式
        report = await agent.scan_events("600519.SH")
    """
    
    def __init__(
        self,
        config: Dict[str, Any],
        shared_memory: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
    ) -> None:
        super().__init__(
            AgentRole.MICRO_EVENT_MONITOR, config,
            shared_memory=shared_memory,
            tool_registry=tool_registry,
        )
        self._llm: Optional[LLMClient] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._subscribers: Dict[str, List[Callable]] = {}
        self._event_history: List[MicroEvent] = []
        self._running = False
        
    async def on_init(self) -> None:
        """初始化LLM客户端和事件监听"""
        self._llm = LLMClient(
            provider=self.config.get("provider", "openai"),
            api_key=self.config.get("api_key", ""),
            base_url=self.config.get("base_url"),
            model=self.config.get("model", "gpt-4o"),
            temperature=self.config.get("temperature", 0.3),
            max_tokens=self.config.get("max_tokens", 4096),
            timeout=self.config.get("timeout", 60),
            max_retries=self.config.get("max_retries", 3),
        )
        # 注册消息处理器
        self.register_handler("micro_event_scan", self._handle_scan_request)
        self.register_handler("micro_event_notify", self._handle_event_notify)
        
    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """默认消息处理"""
        return await self._handle_scan_request(message)
    
    # ============================================================
    # 事件订阅机制
    # ============================================================
    
    def subscribe(self, symbol: str, callback: Callable[[MicroEvent], None]) -> None:
        """订阅特定标的的微观事件
        
        Args:
            symbol: 股票代码
            callback: 事件回调函数
        """
        if symbol not in self._subscribers:
            self._subscribers[symbol] = []
        self._subscribers[symbol].append(callback)
        self._logger.info(f"订阅 {symbol} 的微观事件")
        
    def unsubscribe(self, symbol: str, callback: Optional[Callable] = None) -> None:
        """取消订阅
        
        Args:
            symbol: 股票代码
            callback: 特定回调函数，None则取消所有
        """
        if symbol not in self._subscribers:
            return
        if callback is None:
            del self._subscribers[symbol]
        else:
            self._subscribers[symbol] = [
                cb for cb in self._subscribers[symbol] if cb != callback
            ]
            
    async def _notify_subscribers(self, event: MicroEvent) -> None:
        """通知订阅者"""
        if event.symbol in self._subscribers:
            for callback in self._subscribers[event.symbol]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(event)
                    else:
                        callback(event)
                except Exception as e:
                    self._logger.error(f"事件回调失败: {e}")
                    
    # ============================================================
    # 事件扫描与分析
    # ============================================================
    
    async def scan_events(
        self,
        symbol: str,
        event_types: Optional[List[MicroEventType]] = None,
        days: int = 7,
    ) -> MicroEventReport:
        """扫描指定标的的微观事件
        
        Args:
            symbol: 股票代码
            event_types: 事件类型过滤，None则扫描所有
            days: 扫描最近N天的事件
            
        Returns:
            MicroEventReport 事件分析报告
        """
        assert self._llm is not None
        self._logger.info(f"开始扫描 {symbol} 的微观事件...")
        
        # 收集各类事件
        events: List[MicroEvent] = []
        
        # 1. 扫描公告事件
        notice_events = await self._scan_exchange_notices(symbol, days)
        events.extend(notice_events)
        
        # 2. 扫描龙虎榜
        dragon_tiger_events = await self._scan_dragon_tiger(symbol, days)
        events.extend(dragon_tiger_events)
        
        # 3. 扫描异常交易
        abnormal_events = await self._scan_abnormal_trading(symbol, days)
        events.extend(abnormal_events)
        
        # 4. 扫描另类数据
        alt_events = await self._scan_alternative_data(symbol, days)
        events.extend(alt_events)
        
        # 按类型过滤
        if event_types:
            events = [e for e in events if e.event_type in event_types]
            
        # 按影响等级排序
        impact_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        events.sort(key=lambda e: impact_order.get(e.impact_level, 3))
        
        # 存储到历史记录
        self._event_history.extend(events)
        if len(self._event_history) > 1000:
            self._event_history = self._event_history[-1000:]
            
        # 使用LLM生成分析报告
        report = await self._generate_event_report(symbol, events)
        
        # 存储到共享记忆
        if hasattr(self, "shared_memory") and self.shared_memory:
            await self.shared_memory.store(
                agent_id=self.agent_id,
                memory_type=self.shared_memory.MemoryType.MICRO_EVENT,
                content=f"{symbol} 微观事件扫描: 发现{len(events)}个事件, "
                        f"其中{len(report.critical_events)}个关键事件",
                structured_data=report.model_dump(),
                importance=self.shared_memory.MemoryImportance.HIGH,
                tags=[symbol, "micro_event", "scan"],
            )
            
        return report
    
    async def _scan_exchange_notices(self, symbol: str, days: int) -> List[MicroEvent]:
        """扫描交易所公告"""
        events = []
        
        # 使用工具获取公告数据
        if hasattr(self, "tool_registry") and self.tool_registry:
            result = await self.tool_registry.call_tool(
                "fetch_exchange_notices",
                {"symbol": symbol, "days": days},
                caller_agent_id=self.agent_id,
            )
            if result.get("success") and result.get("result"):
                for notice in result["result"].get("notices", []):
                    event = MicroEvent(
                        event_id=f"notice_{notice.get('id', '')}",
                        event_type=self._classify_notice_type(notice.get("title", "")),
                        symbol=symbol,
                        title=notice.get("title", ""),
                        content=notice.get("content", ""),
                        source="exchange_notice",
                        event_time=notice.get("publish_time", ""),
                        impact_level=self._estimate_notice_impact(notice),
                        raw_data=notice,
                    )
                    events.append(event)
                    
        return events
    
    async def _scan_dragon_tiger(self, symbol: str, days: int) -> List[MicroEvent]:
        """扫描龙虎榜数据"""
        events = []
        
        if hasattr(self, "tool_registry") and self.tool_registry:
            result = await self.tool_registry.call_tool(
                "fetch_dragon_tiger",
                {"symbol": symbol, "days": days},
                caller_agent_id=self.agent_id,
            )
            if result.get("success") and result.get("result"):
                for dt in result["result"].get("records", []):
                    # 分析龙虎榜数据
                    buy_amount = float(dt.get("buy_amount", 0))
                    sell_amount = float(dt.get("sell_amount", 0))
                    net_buy = buy_amount - sell_amount
                    
                    impact_direction = "positive" if net_buy > 0 else "negative"
                    impact_level = "high" if abs(net_buy) > 1e8 else "medium"
                    
                    event = MicroEvent(
                        event_id=f"dt_{dt.get('date', '')}_{symbol}",
                        event_type=MicroEventType.DRAGON_TIGER,
                        symbol=symbol,
                        title=f"龙虎榜: 净{'买入' if net_buy > 0 else '卖出'}{abs(net_buy)/1e8:.2f}亿",
                        content=f"买入{buy_amount/1e8:.2f}亿, 卖出{sell_amount/1e8:.2f}亿",
                        source="dragon_tiger",
                        event_time=dt.get("date", ""),
                        impact_level=impact_level,
                        impact_direction=impact_direction,
                        raw_data=dt,
                        tags=["龙虎榜", "游资动向"],
                    )
                    events.append(event)
                    
        return events
    
    async def _scan_abnormal_trading(self, symbol: str, days: int) -> List[MicroEvent]:
        """扫描异常交易信号"""
        events = []
        
        # 获取行情数据分析异常
        if hasattr(self, "tool_registry") and self.tool_registry:
            result = await self.tool_registry.call_tool(
                "fetch_market_data",
                {"symbol": symbol, "period": "daily"},
                caller_agent_id=self.agent_id,
            )
            if result.get("success") and result.get("result"):
                # 简单的异常检测逻辑
                # 实际应用中可以使用更复杂的算法
                pass
                
        return events
    
    async def _scan_alternative_data(self, symbol: str, days: int) -> List[MicroEvent]:
        """扫描另类数据"""
        events = []
        
        if hasattr(self, "tool_registry") and self.tool_registry:
            # 扫描舆情
            sentiment_result = await self.tool_registry.call_tool(
                "fetch_sentiment_data",
                {"symbol": symbol, "days": days},
                caller_agent_id=self.agent_id,
            )
            if sentiment_result.get("success") and sentiment_result.get("result"):
                sentiment_data = sentiment_result["result"]
                if sentiment_data.get("spike_detected"):
                    event = MicroEvent(
                        event_id=f"sentiment_{symbol}_{datetime.now().strftime('%Y%m%d')}",
                        event_type=MicroEventType.SENTIMENT_SPIKE,
                        symbol=symbol,
                        title="舆情热度异常上升",
                        content=sentiment_data.get("summary", ""),
                        source="sentiment_monitor",
                        event_time=datetime.now().isoformat(),
                        impact_level="medium",
                        raw_data=sentiment_data,
                        tags=["舆情", "热度"],
                    )
                    events.append(event)
                    
        return events
    
    async def _generate_event_report(
        self,
        symbol: str,
        events: List[MicroEvent],
    ) -> MicroEventReport:
        """使用LLM生成事件分析报告"""
        assert self._llm is not None
        
        if not events:
            return MicroEventReport(
                symbol=symbol,
                summary=f"最近未发现{symbol}的重要微观事件",
            )
            
        # 构建事件摘要
        event_summaries = []
        for i, event in enumerate(events[:20], 1):  # 最多20个事件
            event_summaries.append(
                f"{i}. [{event.event_type.value}] {event.title}\n"
                f"   时间: {event.event_time}\n"
                f"   影响: {event.impact_level} ({event.impact_direction})\n"
                f"   详情: {event.content[:100]}"
            )
            
        events_text = "\n".join(event_summaries)
        
        user_prompt = f"""请分析以下 {symbol} 的微观事件，生成投资分析报告：

## 近期微观事件列表
{events_text}

请输出JSON格式的分析报告，包含：
1. sentiment_shift: 整体情绪变化(positive/negative/neutral)
2. trading_implication: 交易建议(50字内)
3. key_insights: 关键洞察列表(3-5条)
4. risk_alerts: 风险警示列表
5. opportunity_signals: 机会信号列表
6. summary: 综合摘要(100字内)
"""
        
        try:
            report = await self._llm.chat_structured(
                message=user_prompt,
                response_model=MicroEventReport,
                system=MICRO_EVENT_SYSTEM_PROMPT,
                temperature=0.3,
            )
            report.symbol = symbol
            report.events_count = len(events)
            report.critical_events = [
                e.raw_data for e in events if e.impact_level == "critical"
            ]
            report.high_impact_events = [
                e.raw_data for e in events if e.impact_level == "high"
            ]
            return report
        except Exception as e:
            self._logger.error(f"生成事件报告失败: {e}")
            return MicroEventReport(
                symbol=symbol,
                summary=f"分析失败: {str(e)}",
                events_count=len(events),
            )
    
    # ============================================================
    # 辅助方法
    # ============================================================
    
    def _classify_notice_type(self, title: str) -> MicroEventType:
        """根据公告标题分类事件类型"""
        title_lower = title.lower()
        if "停牌" in title or "复牌" in title:
            return MicroEventType.SUSPEND_RESUME
        elif "风险" in title or "警示" in title:
            return MicroEventType.RISK_WARNING
        elif "业绩" in title or "预告" in title or "快报" in title:
            return MicroEventType.EARNINGS_PREVIEW
        else:
            return MicroEventType.MAJOR_EVENT
            
    def _estimate_notice_impact(self, notice: Dict) -> str:
        """估算公告影响等级"""
        title = notice.get("title", "")
        # 简单的关键词判断
        critical_keywords = ["退市", "立案", "处罚", "重大资产重组"]
        high_keywords = ["业绩预亏", "业绩预增", "减持", "增持", "质押"]
        
        for kw in critical_keywords:
            if kw in title:
                return "critical"
        for kw in high_keywords:
            if kw in title:
                return "high"
        return "medium"
    
    # ============================================================
    # 消息处理器
    # ============================================================
    
    async def _handle_scan_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理扫描请求"""
        payload = message.payload
        report = await self.scan_events(
            symbol=payload.get("symbol", ""),
            event_types=payload.get("event_types"),
            days=payload.get("days", 7),
        )
        return self.create_message(
            receiver=message.sender,
            msg_type="micro_event_report",
            payload=report.model_dump(),
        )
    
    async def _handle_event_notify(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理事件通知"""
        payload = message.payload
        event = MicroEvent(
            event_id=payload.get("event_id", ""),
            event_type=MicroEventType(payload.get("event_type", "major_event")),
            symbol=payload.get("symbol", ""),
            title=payload.get("title", ""),
            content=payload.get("content", ""),
            source=payload.get("source", ""),
            event_time=payload.get("event_time", ""),
            impact_level=payload.get("impact_level", "low"),
            raw_data=payload.get("raw_data", {}),
        )
        
        # 通知订阅者
        await self._notify_subscribers(event)
        
        # 存储到共享记忆
        if hasattr(self, "shared_memory") and self.shared_memory:
            await self.shared_memory.store(
                agent_id=self.agent_id,
                memory_type=self.shared_memory.MemoryType.MICRO_EVENT,
                content=f"{event.symbol} 微观事件: {event.title}",
                structured_data=event.raw_data,
                importance=self.shared_memory.MemoryImportance.HIGH,
                tags=[event.symbol, "micro_event", event.event_type.value],
            )
            
        return self.create_message(
            receiver=message.sender,
            msg_type="event_received",
            payload={"event_id": event.event_id, "status": "processed"},
        )
