"""
策略生成Agent

接收市场分析报告，使用LLM生成交易策略信号。
内置多空辩论机制，在生成策略前通过多空研究员角色进行充分的正反面论证。
"""

from __future__ import annotations

from datetime import datetime
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


class BullBearDebateResult(BaseModel):
    """多空辩论结果

    Attributes:
        symbol: 标的代码
        bull_arguments: 多头论点列表
        bear_arguments: 空头论点列表
        bull_strength: 多头论点强度 (0-1)
        bear_strength: 空头论点强度 (0-1)
        consensus: 共识方向 (bullish/bearish/neutral)
        confidence: 共识置信度 (0-1)
        key_debates: 关键争议点列表
        conclusion: 综合结论
    """
    symbol: str
    bull_arguments: List[str] = Field(default_factory=list)       # 多头论点
    bear_arguments: List[str] = Field(default_factory=list)       # 空头论点
    bull_strength: float = Field(default=0.5)                     # 多头论点强度(0-1)
    bear_strength: float = Field(default=0.5)                     # 空头论点强度(0-1)
    consensus: str = Field(default="neutral")                     # 共识方向(bullish/bearish/neutral)
    confidence: float = Field(default=0.0)                        # 共识置信度(0-1)
    key_debates: List[str] = Field(default_factory=list)          # 关键争议点
    conclusion: str = ""                                          # 综合结论


# 策略生成Agent的系统提示词
STRATEGY_GENERATOR_SYSTEM_PROMPT = """你是一位顶级的量化交易策略师，擅长根据市场分析结果制定交易策略。
在制定策略之前，你需要通过多空辩论机制进行充分的正反面论证，确保决策的全面性和客观性。

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

请基于市场分析报告和多空辩论结果制定交易策略。"""

# 多头研究员系统提示词
BULL_ANALYST_SYSTEM_PROMPT = """你是一位经验丰富的多头研究员，你的任务是找出所有支持看涨的理由。

## 你的分析角度
1. **基本面利好**: 业绩增长、估值低位、行业景气
2. **技术面信号**: 趋势向上、突破关键阻力、量价配合
3. **资金面支持**: 北向资金流入、机构增持、融资余额上升
4. **消息面催化**: 政策利好、订单增长、新产品发布
5. **市场情绪**: 恐慌情绪消退、市场预期改善

## 要求
- 每个论点要有事实依据
- 评估每个论点的强度(0-1)
- 识别最重要的3-5个看涨理由
- 也要指出多头论点的潜在弱点
"""

# 空头研究员系统提示词
BEAR_ANALYST_SYSTEM_PROMPT = """你是一位谨慎的空头研究员，你的任务是找出所有支持看跌的理由。

## 你的分析角度
1. **基本面风险**: 业绩下滑、估值过高、行业衰退
2. **技术面信号**: 趋势向下、跌破关键支撑、量价背离
3. **资金面压力**: 北向资金流出、机构减持、融资余额下降
4. **消息面利空**: 政策收紧、诉讼风险、高管减持
5. **市场情绪**: 贪婪情绪过热、市场预期过高

## 要求
- 每个论点要有事实依据
- 评估每个论点的强度(0-1)
- 识别最重要的3-5个看跌理由
- 也要指出空头论点的潜在弱点
"""

# 多空辩论裁判系统提示词
DEBATE_JUDGE_SYSTEM_PROMPT = """你是一位公正的多空辩论裁判，你的任务是综合多空双方的论点，做出最终判断。

## 裁判原则
1. **证据权重**: 有数据支撑的论点权重更高
2. **时效性**: 近期事件的影响大于远期事件
3. **可逆性**: 不可逆因素(如政策变化)权重更高
4. **市场定价**: 尚未被市场充分定价的因素更重要
5. **概率思维**: 评估各论点实现的概率而非确定性

## 输出要求
- 给出明确的共识方向(bullish/bearish/neutral)
- 评估共识的置信度(0-1)
- 列出关键的争议点
- 给出综合结论(100字以内)
"""


class StrategyGeneratorAgent(BaseAgent):
    """策略生成Agent

    接收市场分析报告，使用LLM生成交易策略信号。
    内置多空辩论机制，在生成策略前进行充分的正反面论证。

    Usage:
        agent = StrategyGeneratorAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        signal = await agent.generate_strategy(analysis_report)
        # 带多空辩论的策略生成
        signal = await agent.generate_strategy(analysis_report, enable_debate=True)
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

    async def debate(
        self,
        symbol: str,
        analysis_context: str,
    ) -> BullBearDebateResult:
        """执行多空辩论

        通过三轮LLM调用完成多空辩论：
        1. 多头研究生成看涨论点
        2. 空头研究生成看跌论点
        3. 裁判综合双方论点做出判断

        Args:
            symbol: 标的代码
            analysis_context: 分析上下文(市场分析报告等信息)

        Returns:
            BullBearDebateResult 多空辩论结果
        """
        assert self._llm is not None

        self._logger.info(f"[{symbol}] 开始多空辩论...")

        try:
            # 第一轮：多头研究员生成看涨论点
            bull_prompt = (
                f"## 标的: {symbol}\n\n"
                f"{analysis_context}\n\n"
                "请作为多头研究员，列出所有支持看涨的理由。\n"
                "输出JSON格式：\n"
                "- arguments: 看涨论点列表(每个论点包含 reason 和 strength)\n"
                "- overall_strength: 多头整体强度(0-1)\n"
                "- weaknesses: 多头论点的潜在弱点"
            )
            bull_response = await self._llm.chat(
                message=bull_prompt,
                system=BULL_ANALYST_SYSTEM_PROMPT,
                temperature=0.4,
            )

            # 第二轮：空头研究员生成看跌论点
            bear_prompt = (
                f"## 标的: {symbol}\n\n"
                f"{analysis_context}\n\n"
                "请作为空头研究员，列出所有支持看跌的理由。\n"
                "输出JSON格式：\n"
                "- arguments: 看跌论点列表(每个论点包含 reason 和 strength)\n"
                "- overall_strength: 空头整体强度(0-1)\n"
                "- weaknesses: 空头论点的潜在弱点"
            )
            bear_response = await self._llm.chat(
                message=bear_prompt,
                system=BEAR_ANALYST_SYSTEM_PROMPT,
                temperature=0.4,
            )

            # 第三轮：裁判综合判断
            judge_prompt = (
                f"## 标的: {symbol}\n\n"
                f"### 原始分析\n{analysis_context}\n\n"
                f"### 多头论点\n{bull_response}\n\n"
                f"### 空头论点\n{bear_response}\n\n"
                "请作为裁判，综合多空双方论点做出最终判断。\n"
                "输出JSON格式：\n"
                "- bull_arguments: 提取的多头核心论点列表\n"
                "- bear_arguments: 提取的空头核心论点列表\n"
                "- bull_strength: 多头论点强度(0-1)\n"
                "- bear_strength: 空头论点强度(0-1)\n"
                "- consensus: 共识方向(bullish/bearish/neutral)\n"
                "- confidence: 共识置信度(0-1)\n"
                "- key_debates: 关键争议点列表\n"
                "- conclusion: 综合结论(100字以内)"
            )
            judge_response = await self._llm.chat(
                message=judge_prompt,
                system=DEBATE_JUDGE_SYSTEM_PROMPT,
                temperature=0.2,
            )

            # 解析裁判结果
            debate_result = await self._parse_debate_result(symbol, judge_response)

            # 存储到共享记忆
            if hasattr(self, "shared_memory") and self.shared_memory:
                await self.shared_memory.store(
                    agent_id=self.agent_id,
                    memory_type=self.shared_memory.MemoryType.ANALYSIS_REPORT,
                    content=(
                        f"{symbol} 多空辩论: consensus={debate_result.consensus}, "
                        f"confidence={debate_result.confidence:.2f}"
                    ),
                    structured_data=debate_result.model_dump(),
                    importance=self.shared_memory.MemoryImportance.HIGH,
                    tags=[symbol, "debate", debate_result.consensus],
                )

            self._logger.info(
                f"[{symbol}] 多空辩论完成: consensus={debate_result.consensus}, "
                f"bull={debate_result.bull_strength:.2f}, "
                f"bear={debate_result.bear_strength:.2f}, "
                f"confidence={debate_result.confidence:.2f}"
            )
            return debate_result

        except Exception as e:
            self._logger.error(f"[{symbol}] 多空辩论失败: {e}")
            return BullBearDebateResult(
                symbol=symbol,
                conclusion=f"辩论失败: {str(e)}",
            )

    async def _parse_debate_result(
        self,
        symbol: str,
        judge_response: str,
    ) -> BullBearDebateResult:
        """解析裁判的响应为结构化辩论结果

        Args:
            symbol: 标的代码
            judge_response: 裁判LLM的原始响应文本

        Returns:
            BullBearDebateResult 结构化的辩论结果
        """
        import json

        try:
            # 尝试提取JSON块
            json_str = judge_response.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)
            return BullBearDebateResult(
                symbol=symbol,
                bull_arguments=data.get("bull_arguments", []),
                bear_arguments=data.get("bear_arguments", []),
                bull_strength=float(data.get("bull_strength", 0.5)),
                bear_strength=float(data.get("bear_strength", 0.5)),
                consensus=data.get("consensus", "neutral"),
                confidence=float(data.get("confidence", 0.0)),
                key_debates=data.get("key_debates", []),
                conclusion=data.get("conclusion", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            self._logger.warning(f"辩论结果JSON解析失败，使用原始文本: {e}")
            return BullBearDebateResult(
                symbol=symbol,
                conclusion=judge_response[:500],
            )

    async def generate_strategy(
        self,
        analysis: MarketAnalysisReport,
        current_price: Optional[float] = None,
        enable_debate: bool = True,
    ) -> StrategySignal:
        """根据市场分析生成交易策略

        如果启用多空辩论(enable_debate=True)，将在生成策略前先进行多空辩论，
        辩论结果会作为额外上下文提供给策略生成LLM。

        Args:
            analysis: 市场分析报告
            current_price: 当前价格(可选)
            enable_debate: 是否启用多空辩论(默认True)

        Returns:
            StrategySignal 策略信号
        """
        assert self._llm is not None

        self._logger.info(f"为 {analysis.symbol} 生成交易策略...")

        # 构建基础分析上下文
        analysis_context = self._build_strategy_context(analysis, current_price)

        # 多空辩论(如果启用)
        debate_result: Optional[BullBearDebateResult] = None
        if enable_debate:
            debate_result = await self.debate(analysis.symbol, analysis_context)

        # 构建最终策略上下文(包含辩论结果)
        final_context = analysis_context
        if debate_result:
            final_context = self._build_debate_enhanced_context(
                analysis_context, debate_result
            )

        try:
            signal = await self._llm.chat_structured(
                message=final_context,
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

    def _build_debate_enhanced_context(
        self,
        base_context: str,
        debate_result: BullBearDebateResult,
    ) -> str:
        """构建包含多空辩论结果的增强上下文

        Args:
            base_context: 基础分析上下文
            debate_result: 多空辩论结果

        Returns:
            包含辩论信息的增强上下文字符串
        """
        parts = [base_context]

        parts.append("\n\n## 多空辩论结果\n")
        parts.append(f"**共识方向**: {debate_result.consensus}")
        parts.append(f"**共识置信度**: {debate_result.confidence:.2f}")
        parts.append(f"**多头强度**: {debate_result.bull_strength:.2f}")
        parts.append(f"**空头强度**: {debate_result.bear_strength:.2f}")

        if debate_result.bull_arguments:
            parts.append("\n**多头核心论点**:")
            for arg in debate_result.bull_arguments:
                parts.append(f"- {arg}")

        if debate_result.bear_arguments:
            parts.append("\n**空头核心论点**:")
            for arg in debate_result.bear_arguments:
                parts.append(f"- {arg}")

        if debate_result.key_debates:
            parts.append("\n**关键争议点**:")
            for debate in debate_result.key_debates:
                parts.append(f"- {debate}")

        if debate_result.conclusion:
            parts.append(f"\n**辩论结论**: {debate_result.conclusion}")

        parts.append(
            "\n\n请综合以上市场分析报告和多空辩论结果，制定具体的交易策略。"
            "如果多空辩论的共识方向明确且置信度较高，策略应与共识方向一致；"
            "如果多空分歧较大(多头和空头强度接近)，应降低仓位或选择观望。"
            "输出JSON格式的策略信号。"
        )

        return "\n".join(parts)

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
