"""
新闻与社媒分析Agent

负责搜索、聚合和分析与投资标的相关的新闻、公告、社交媒体信息。
输出情感分析报告和重大事件提醒。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class NewsItem(BaseModel):
    """单条新闻

    Attributes:
        title: 新闻标题
        source: 来源
        publish_time: 发布时间
        summary: 摘要
        sentiment: 情感标签 (positive/negative/neutral)
        confidence: 情感判断置信度
        relevance_score: 与标的的相关性评分
        impact_level: 影响等级 (low/medium/high/critical)
        url: 原文链接
        tags: 标签列表
    """
    title: str
    source: str
    publish_time: str
    summary: str
    sentiment: str = Field(default="neutral")  # positive/negative/neutral
    confidence: float = Field(default=0.5)
    relevance_score: float = Field(default=0.5)  # 与标的的相关性
    impact_level: str = Field(default="low")  # low/medium/high/critical
    url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class NewsAnalysisReport(BaseModel):
    """新闻分析报告

    Attributes:
        symbol: 标的代码
        analysis_time: 分析时间
        overall_sentiment: 总体情感 (positive/negative/neutral)
        sentiment_score: 情感分数 (-1到1)
        news_count: 新闻总数
        positive_count: 正面新闻数
        negative_count: 负面新闻数
        neutral_count: 中性新闻数
        key_news: 重要新闻列表
        hot_topics: 热门话题列表
        risk_events: 风险事件列表
        opportunity_events: 机会事件列表
        summary: 综合分析摘要
        market_impact_assessment: 市场影响评估
    """
    symbol: str
    analysis_time: str = Field(default_factory=lambda: datetime.now().isoformat())
    overall_sentiment: str = Field(default="neutral")  # positive/negative/neutral
    sentiment_score: float = Field(default=0.0)  # -1到1
    news_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    key_news: List[NewsItem] = Field(default_factory=list)  # 重要新闻
    hot_topics: List[str] = Field(default_factory=list)  # 热门话题
    risk_events: List[str] = Field(default_factory=list)  # 风险事件
    opportunity_events: List[str] = Field(default_factory=list)  # 机会事件
    summary: str = ""
    market_impact_assessment: str = ""  # 市场影响评估


# 新闻分析Agent的系统提示词
NEWS_ANALYST_SYSTEM_PROMPT = """你是一位专业的金融新闻与社媒分析师，擅长从海量信息中提取对投资决策有价值的内容。

## 你的核心能力
1. **新闻情感分析**: 准确判断新闻对标的资产的影响方向和力度
2. **事件识别**: 识别财报、政策、行业变化等关键事件
3. **信息聚合**: 从多个来源综合判断，避免单一信源偏差
4. **影响评估**: 评估新闻对短期和中期价格走势的影响

## 分析框架
- **正面信号**: 利好消息、业绩超预期、政策利好、机构增持、行业景气
- **负面信号**: 利空消息、业绩不及预期、政策收紧、机构减持、行业衰退
- **中性信号**: 常规公告、人事变动(非核心)、行业常规动态

## 输出要求
- 情感判断要基于事实，避免过度解读
- 区分短期噪音和长期趋势性信息
- 对每条重要新闻给出影响等级(critical/high/medium/low)
- 综合评估时要考虑市场预期(利好出尽是利空)

## A股市场特殊注意事项
- 关注证监会/交易所公告
- 注意涨跌停板对信息反应的影响
- 考虑北向资金动向
- 关注大股东增减持计划
"""


class NewsAnalystAgent(BaseAgent):
    """新闻与社媒分析Agent

    搜索和聚合与目标标的相关的新闻、公告、社交媒体讨论，
    进行情感分析和重大事件识别，输出结构化的 NewsAnalysisReport。

    Usage:
        agent = NewsAnalystAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        report = await agent.analyze(symbol="600519.SH", news_data=news_list)
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(AgentRole("news_analyst"), config)
        self._llm: Optional[LLMClient] = None

    async def on_init(self) -> None:
        """初始化LLM客户端"""
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
        self.register_handler("news_analysis_request", self._handle_analysis_request)
        self.register_handler("news_search", self._handle_news_search)

    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """默认消息处理"""
        return await self._handle_analysis_request(message)

    async def analyze(
        self,
        symbol: str,
        news_data: Optional[List[Dict]] = None,
        social_data: Optional[List[Dict]] = None,
        context: Optional[str] = None,
    ) -> NewsAnalysisReport:
        """分析新闻和社媒数据

        Args:
            symbol: 股票代码
            news_data: 新闻数据列表(可选, 如果不提供则使用工具搜索)
            social_data: 社交媒体数据(可选)
            context: 额外上下文信息

        Returns:
            NewsAnalysisReport 新闻分析报告
        """
        assert self._llm is not None

        self._logger.info(f"开始分析 {symbol} 的新闻与社媒信息...")

        # 如果没有提供新闻数据，尝试使用共享工具搜索
        if not news_data and hasattr(self, "tool_registry") and self.tool_registry:
            result = await self.tool_registry.call_tool(
                "search_news",
                {"keyword": symbol, "days": 7},
                caller_agent_id=self.agent_id,
            )
            if result.get("success") and result.get("result", {}).get("news"):
                news_data = result["result"]["news"]

        # 构建分析上下文
        news_section = ""
        if news_data:
            news_items = []
            for i, news in enumerate(news_data[:10], 1):
                news_items.append(
                    f"{i}. [{news.get('source', '未知')}] {news.get('title', '无标题')}\n"
                    f"   时间: {news.get('publish_time', '未知')}\n"
                    f"   摘要: {news.get('summary', '无摘要')}"
                )
            news_section = "## 近期相关新闻\n" + "\n".join(news_items)
        else:
            news_section = "## 近期相关新闻\n暂无新闻数据(新闻API未配置)"

        social_section = ""
        if social_data:
            social_items = []
            for i, post in enumerate(social_data[:10], 1):
                social_items.append(
                    f"{i}. [{post.get('platform', '未知')}] {post.get('content', '无内容')[:200]}"
                )
            social_section = "## 社交媒体讨论\n" + "\n".join(social_items)

        memory_context = ""
        if hasattr(self, "shared_memory") and self.shared_memory:
            memory_context = await self.shared_memory.get_agent_context(self.agent_id, n=5)

        memory_section = ""
        if memory_context:
            memory_section = f"## 历史分析记忆\n{memory_context}"

        context_section = ""
        if context:
            context_section = f"## 补充上下文\n{context}"

        user_prompt = f"""请分析以下与 {symbol} 相关的新闻和社交媒体信息：

{news_section}
{social_section}

{memory_section}

{context_section}

请输出JSON格式的分析报告，包含以下字段：
- overall_sentiment: 总体情感(positive/negative/neutral)
- sentiment_score: 情感分数(-1到1)
- summary: 100字以内的综合分析
- market_impact_assessment: 市场影响评估(50字)
- key_factors: 关键影响因素列表
- risk_events: 风险事件列表
- opportunity_events: 机会事件列表
- hot_topics: 热门话题列表
"""

        try:
            report = await self._llm.chat_structured(
                message=user_prompt,
                response_model=NewsAnalysisReport,
                system=NEWS_ANALYST_SYSTEM_PROMPT,
                temperature=0.3,
            )

            # 统计正负面新闻数量
            if news_data:
                for news in news_data:
                    sent = news.get("sentiment", "neutral")
                    if sent == "positive":
                        report.positive_count += 1
                    elif sent == "negative":
                        report.negative_count += 1
                    else:
                        report.neutral_count += 1
                report.news_count = len(news_data)

            # 存储到共享记忆
            if hasattr(self, "shared_memory") and self.shared_memory:
                await self.shared_memory.store(
                    agent_id=self.agent_id,
                    memory_type=self.shared_memory.MemoryType.NEWS_EVENT,
                    content=f"{symbol} 新闻分析: {report.summary}",
                    structured_data=report.model_dump(),
                    importance=self.shared_memory.MemoryImportance.HIGH,
                    tags=[symbol, "news_analysis", report.overall_sentiment],
                )

            self._logger.info(
                f"新闻分析完成: {symbol} -> sentiment={report.overall_sentiment}, "
                f"score={report.sentiment_score:.2f}"
            )
            return report

        except Exception as e:
            self._logger.error(f"新闻分析失败: {e}")
            return NewsAnalysisReport(
                symbol=symbol,
                summary=f"分析失败: {str(e)}",
            )

    async def _handle_analysis_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理分析请求消息"""
        payload = message.payload
        report = await self.analyze(
            symbol=payload.get("symbol", ""),
            news_data=payload.get("news_data"),
            social_data=payload.get("social_data"),
            context=payload.get("context"),
        )
        return self.create_message(
            receiver=message.sender,
            msg_type="news_analysis_report",
            payload=report.model_dump(),
        )

    async def _handle_news_search(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理新闻搜索消息"""
        payload = message.payload
        if hasattr(self, "tool_registry") and self.tool_registry:
            result = await self.tool_registry.call_tool(
                "search_news",
                payload,
                caller_agent_id=self.agent_id,
            )
            return self.create_message(
                receiver=message.sender,
                msg_type="news_search_result",
                payload=result,
            )
        return None
