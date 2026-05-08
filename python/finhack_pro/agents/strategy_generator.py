"""
策略生成Agent

接收市场分析报告，使用LLM生成交易策略信号。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.agents.market_analyzer import MarketAnalysisReport
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class SignalDirection(str, Enum):
    """信号方向"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class StrategySignal(BaseModel):
    """策略信号

    Attributes:
        symbol: 标的代码
        direction: 交易方向
        confidence: 信号置信度 (0-1)
        target_price: 目标价格
        stop_loss: 止损价格
        take_profit: 止盈价格
        position_size_pct: 建议仓位百分比 (0-1)
        reasoning: 决策理由
        strategy_type: 策略类型
        time_horizon: 持有周期
        entry_price: 建议入场价格
        urgency: 紧急程度 (low/medium/high)
    """
    symbol: str
    direction: SignalDirection
    confidence: float = Field(ge=0.0, le=1.0)
    target_price: Optional[float] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    position_size_pct: float = Field(ge=0.0, le=1.0, default=0.0)
    reasoning: str = ""
    strategy_type: str = ""
    time_horizon: str = "short_term"  # short_term / medium_term / long_term
    entry_price: Optional[float] = None
    urgency: str = "medium"  # low / medium / high


# 策略生成Agent的系统提示词
STRATEGY_GENERATOR_SYSTEM_PROMPT = """你是一位顶级的量化交易策略师，擅长根据市场分析结果制定交易策略。

## 策略制定原则

### 1. 风险收益比
- 每笔交易的风险收益比至少 1:2
- 止损位要设置在关键技术位下方
- 止盈位要考虑前方阻力位和趋势目标

### 2. 仓位管理
- 根据信号置信度调整仓位:
  - 置信度 > 0.8: 20-30% 仓位
  - 置信度 0.6-0.8: 10-20% 仓位
  - 置信度 < 0.6: 5-10% 仓位或观望
- 高风险环境下降低仓位

### 3. 策略类型
- **趋势跟踪**: 顺势而为，突破买入
- **均值回归**: 超卖反弹，超买回落
- **动量策略**: 强者恒强，买入强势股
- **事件驱动**: 基于特定事件或公告

### 4. 持有周期
- **短线**: 1-5天，适合技术性交易
- **中线**: 1-4周，适合波段操作
- **长线**: 1-6月，适合价值投资

### 5. 输出要求
- 必须给出明确的买卖方向
- 止损和止盈要具体到价格
- 决策理由要充分、有逻辑
- 紧急程度基于当前价格与入场价的距离

请基于市场分析报告制定交易策略。"""


class StrategyGeneratorAgent(BaseAgent):
    """策略生成Agent

    接收市场分析报告，使用LLM生成交易策略信号。

    Usage:
        agent = StrategyGeneratorAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        signal = await agent.generate_strategy(analysis_report)
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(AgentRole.STRATEGY_GENERATOR, config)
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
        self.register_handler("analysis_report", self._handle_analysis_report)

    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理默认消息"""
        self._logger.warning(f"收到未处理的消息类型: {message.msg_type}")
        return None

    async def _handle_analysis_report(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理市场分析报告"""
        payload = message.payload
        report = MarketAnalysisReport.model_validate(payload)

        signal = await self.generate_strategy(report)

        return self.create_message(
            receiver=message.sender,
            msg_type="strategy_signal",
            payload=signal.model_dump(),
        )

    async def generate_strategy(
        self,
        analysis: MarketAnalysisReport,
        current_price: Optional[float] = None,
    ) -> StrategySignal:
        """根据市场分析生成交易策略

        Args:
            analysis: 市场分析报告
            current_price: 当前价格(可选)

        Returns:
            StrategySignal 策略信号
        """
        assert self._llm is not None

        self._logger.info(f"为 {analysis.symbol} 生成交易策略...")

        context = self._build_strategy_context(analysis, current_price)

        try:
            signal = await self._llm.chat_structured(
                message=context,
                response_model=StrategySignal,
                system=STRATEGY_GENERATOR_SYSTEM_PROMPT,
                temperature=0.3,
            )
            self._logger.info(
                f"策略生成完成: {analysis.symbol} -> "
                f"方向={signal.direction.value}, 置信度={signal.confidence:.2f}, "
                f"仓位={signal.position_size_pct:.1%}"
            )
            return signal

        except Exception as e:
            self._logger.error(f"策略生成失败: {e}")
            return StrategySignal(
                symbol=analysis.symbol,
                direction=SignalDirection.HOLD,
                confidence=0.0,
                reasoning=f"策略生成失败: {e}",
                strategy_type="none",
            )

    def _build_strategy_context(
        self,
        analysis: MarketAnalysisReport,
        current_price: Optional[float],
    ) -> str:
        """构建策略生成上下文"""
        parts = [
            f"## 市场分析报告\n",
            f"**标的**: {analysis.symbol}",
            f"**市场状态**: {analysis.market_state.value}",
            f"**趋势方向**: {analysis.trend_direction.value}",
            f"**置信度**: {analysis.confidence:.2f}",
            f"**风险等级**: {analysis.risk_level.value}",
            f"\n**关键因素**:",
        ]
        for factor in analysis.key_factors:
            parts.append(f"- {factor}")

        if analysis.technical_summary:
            parts.append(f"\n**技术面摘要**: {analysis.technical_summary}")

        if analysis.support_levels:
            parts.append(f"\n**支撑位**: {', '.join(f'{s:.2f}' for s in analysis.support_levels)}")

        if analysis.resistance_levels:
            parts.append(f"**阻力位**: {', '.join(f'{r:.2f}' for r in analysis.resistance_levels)}")

        if analysis.volume_analysis:
            parts.append(f"\n**成交量分析**: {analysis.volume_analysis}")

        if analysis.suggestion:
            parts.append(f"\n**市场分析建议**: {analysis.suggestion}")

        if current_price:
            parts.append(f"\n**当前价格**: {current_price:.2f}")

        parts.append(
            "\n\n请基于以上市场分析报告，制定具体的交易策略。"
            "输出JSON格式的策略信号。"
        )

        return "\n".join(parts)
