"""
市场分析Agent

使用LLM分析市场状态，输出包含趋势判断、风险等级、操作建议的分析报告。
内置技术分析工具(RSI, MACD, 布林带, 均线系统)辅助LLM决策。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class MarketState(str, Enum):
    """市场状态"""
    BULL = "bull"  # 牛市
    BEAR = "bear"  # 熊市
    SIDEWAYS = "sideways"  # 震荡
    VOLATILE = "volatile"  # 高波动


class TrendDirection(str, Enum):
    """趋势方向"""
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


class RiskLevel(str, Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MarketAnalysisReport(BaseModel):
    """市场分析报告

    Attributes:
        symbol: 标的代码
        market_state: 市场状态
        trend_direction: 趋势方向
        confidence: 置信度 (0-1)
        key_factors: 关键分析因素
        risk_level: 风险等级
        suggestion: 操作建议
        technical_summary: 技术面摘要
        support_levels: 支撑位列表
        resistance_levels: 阻力位列表
        volume_analysis: 成交量分析
    """
    symbol: str
    market_state: MarketState
    trend_direction: TrendDirection
    confidence: float = Field(ge=0.0, le=1.0)
    key_factors: List[str] = Field(default_factory=list)
    risk_level: RiskLevel
    suggestion: str = ""
    technical_summary: str = ""
    support_levels: List[float] = Field(default_factory=list)
    resistance_levels: List[float] = Field(default_factory=list)
    volume_analysis: str = ""


# 市场分析Agent的系统提示词
MARKET_ANALYZER_SYSTEM_PROMPT = """你是一位资深的量化市场分析师，拥有20年以上的A股市场分析经验。
你的专长包括技术分析、量价关系、市场情绪判断和宏观分析。

## 分析框架

### 1. 技术分析
- **趋势判断**: 使用均线系统(MA5/MA10/MA20/MA60)判断趋势方向
- **动量指标**: RSI判断超买超卖，MACD判断趋势强度和转折
- **波动率**: 布林带判断波动区间和突破信号
- **成交量**: OBV、量价配合关系

### 2. 市场状态分类
- **牛市(Bull)**: 均线多头排列，MACD在零轴上方，成交量放大
- **熊市(Bear)**: 均线空头排列，MACD在零轴下方，成交量萎缩
- **震荡(Sideways)**: 均线缠绕，布林带收窄，成交量平稳
- **高波动(Volatile)**: 布林带急剧扩张，ATR放大，价格大幅波动

### 3. 风险评估
- **低风险**: 趋势明确，波动率低，流动性好
- **中风险**: 趋势不明确或波动率中等
- **高风险**: 趋势反转信号，波动率极高，流动性差

### 4. 输出要求
- 必须给出明确的操作建议
- 置信度要基于技术指标的信号一致性
- 关键因素要列举3-5个最重要的
- 支撑位和阻力位要基于近期高低点和均线位置

请基于提供的数据进行专业、客观的分析。"""


class MarketAnalyzerAgent(BaseAgent):
    """市场分析Agent

    使用LLM结合技术指标分析市场状态，输出结构化的分析报告。

    Usage:
        agent = MarketAnalyzerAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        report = await agent.analyze(symbol="600519.SH", market_data=df)
    """

    def __init__(self, config: Dict[str, Any], shared_memory=None, tool_registry=None) -> None:
        super().__init__(AgentRole.MARKET_ANALYZER, config,
                         shared_memory=shared_memory, tool_registry=tool_registry)
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
        self.register_handler("analyze_request", self._handle_analyze_request)

    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理默认消息"""
        self._logger.warning(f"收到未处理的消息类型: {message.msg_type}")
        return None

    async def _handle_analyze_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理分析请求消息"""
        payload = message.payload
        symbol = payload.get("symbol", "")
        market_data = payload.get("market_data", {})

        report = await self.analyze(
            symbol=symbol,
            market_data=market_data,
            indicators=payload.get("indicators", {}),
        )

        return self.create_message(
            receiver=message.sender,
            msg_type="analysis_report",
            payload=report.model_dump(),
        )

    async def analyze(
        self,
        symbol: str,
        market_data: Dict[str, Any] | None = None,
        indicators: Dict[str, Any] | None = None,
    ) -> MarketAnalysisReport:
        """执行市场分析

        Args:
            symbol: 标的代码
            market_data: 市场数据字典(包含OHLCV、技术指标等)
            indicators: 技术指标字典

        Returns:
            MarketAnalysisReport 市场分析报告
        """
        assert self._llm is not None

        self._logger.info(f"开始分析 {symbol} 的市场状态...")

        # 构建分析上下文
        context = self._build_analysis_context(symbol, market_data, indicators)

        try:
            report = await self._llm.chat_structured(
                message=context,
                response_model=MarketAnalysisReport,
                system=MARKET_ANALYZER_SYSTEM_PROMPT,
                temperature=0.3,
            )
            self._logger.info(
                f"分析完成: {symbol} -> 状态={report.market_state.value}, "
                f"趋势={report.trend_direction.value}, 置信度={report.confidence:.2f}"
            )
            return report

        except Exception as e:
            self._logger.error(f"市场分析失败: {e}")
            # 返回默认报告
            return MarketAnalysisReport(
                symbol=symbol,
                market_state=MarketState.SIDEWAYS,
                trend_direction=TrendDirection.FLAT,
                confidence=0.0,
                key_factors=["分析失败，使用默认值"],
                risk_level=RiskLevel.HIGH,
                suggestion="分析服务异常，建议暂停交易",
                technical_summary=f"分析失败: {e}",
            )

    def _build_analysis_context(
        self,
        symbol: str,
        market_data: Dict[str, Any] | None,
        indicators: Dict[str, Any] | None,
    ) -> str:
        """构建发送给LLM的分析上下文

        Args:
            symbol: 标的代码
            market_data: 市场数据
            indicators: 技术指标

        Returns:
            格式化的分析上下文字符串
        """
        parts = [f"## 请分析以下标的: {symbol}\n"]

        if market_data:
            parts.append("### 最近行情数据\n")
            # 取最近的数据点
            recent = market_data.get("recent_bars", [])
            if recent:
                parts.append("| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量 |")
                parts.append("|------|------|------|------|------|--------|")
                for bar in recent[-10:]:  # 最近10根K线
                    parts.append(
                        f"| {bar.get('date', '')} | {bar.get('open', 0):.2f} | "
                        f"{bar.get('high', 0):.2f} | {bar.get('low', 0):.2f} | "
                        f"{bar.get('close', 0):.2f} | {bar.get('volume', 0):.0f} |"
                    )

            current = market_data.get("current", {})
            if current:
                parts.append(f"\n当前价格: {current.get('close', 0):.2f}")
                parts.append(f"今日涨跌: {current.get('change_pct', 0):.2f}%")

        if indicators:
            parts.append("\n### 技术指标\n")
            for name, value in indicators.items():
                if isinstance(value, dict):
                    parts.append(f"- **{name}**: {value}")
                else:
                    parts.append(f"- **{name}**: {value}")

        parts.append(
            "\n请基于以上数据进行全面的市场分析，"
            "输出JSON格式的分析报告。"
        )

        return "\n".join(parts)
