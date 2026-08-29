"""
策略生成Agent

接收市场分析报告，使用LLM生成交易策略信号。
"""

from __future__ import annotations

import json
import os
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

    Usage:
        agent = StrategyGeneratorAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        signal = await agent.generate_strategy(analysis_report)
    """

    def __init__(
        self,
        config: Dict[str, Any],
        shared_memory: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
    ) -> None:
        super().__init__(
            AgentRole.STRATEGY_GENERATOR, config,
            shared_memory=shared_memory,
            tool_registry=tool_registry,
        )
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
            raise

    def _build_strategy_context(
        self,
        analysis: MarketAnalysisReport,
        current_price: Optional[float],
    ) -> str:
        """构建策略生成上下文"""
        parts = [
            "## 市场分析报告\n",
            f"**标的**: {analysis.symbol}",
            f"**市场状态**: {analysis.market_state.value}",
            f"**趋势方向**: {analysis.trend_direction.value}",
            f"**置信度**: {analysis.confidence:.2f}",
            f"**风险等级**: {analysis.risk_level.value}",
            "\n**关键因素**:",
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

    # ============================================================
    # 多空辩论
    # ============================================================

    def _build_debate_context(
        self,
        analysis_report: Any,
        news_report: Any = None,
        fundamental_report: Any = None,
        micro_event_report: Any = None,
        current_price: Optional[float] = None,
        report_paths: Optional[Dict[str, str]] = None,
        sentiment_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """构建多空辩论的四方报告上下文

        三层架构：摘要级信息内联到 prompt，完整报告通过 md 文件路径引用
        （LLM 可按需读取文件全文，避免 token 膨胀且保留全部细节）。
        """
        parts: List[str] = []

        if analysis_report is not None:
            parts.append("## 技术面分析\n")
            parts.append(f"**标的**: {getattr(analysis_report, 'symbol', '')}")
            parts.append(f"**市场状态**: {getattr(analysis_report.market_state, 'value', analysis_report.market_state if hasattr(analysis_report, 'market_state') else '')}")
            parts.append(f"**趋势方向**: {getattr(analysis_report.trend_direction, 'value', analysis_report.trend_direction if hasattr(analysis_report, 'trend_direction') else '')}")
            parts.append(f"**置信度**: {getattr(analysis_report, 'confidence', 0):.2f}")
            parts.append(f"**风险等级**: {getattr(analysis_report.risk_level, 'value', '')}")
            if getattr(analysis_report, 'technical_summary', None):
                parts.append(f"**技术面摘要**: {analysis_report.technical_summary}")
            if getattr(analysis_report, 'key_factors', None):
                parts.append("**关键因素**:")
                for f in analysis_report.key_factors:
                    parts.append(f"- {f}")
            if getattr(analysis_report, 'suggestion', None):
                parts.append(f"**建议**: {analysis_report.suggestion}")
            # 思维链：推理过程摘要（三层架构补充，供下游参考）
            if getattr(analysis_report, 'thinking', None):
                parts.append(f"**分析推理**: {analysis_report.thinking}")

        if fundamental_report is not None:
            parts.append("\n## 基本面分析\n")
            parts.append(f"**评级**: {getattr(fundamental_report, 'overall_rating', '')}")
            parts.append(f"**评分**: {getattr(fundamental_report, 'rating_score', 0):.2f}")
            if getattr(fundamental_report, 'key_metrics', None):
                parts.append(f"**关键指标**: {fundamental_report.key_metrics}")
            if getattr(fundamental_report, 'summary', None):
                parts.append(f"**摘要**: {fundamental_report.summary}")
            if getattr(fundamental_report, 'thinking', None):
                parts.append(f"**分析推理**: {fundamental_report.thinking}")

        if news_report is not None:
            parts.append("\n## 新闻社媒分析\n")
            parts.append(f"**情感**: {getattr(news_report, 'overall_sentiment', '')}")
            parts.append(f"**情感分数**: {getattr(news_report, 'sentiment_score', 0):.2f}")
            if getattr(news_report, 'hot_news', None):
                parts.append("**热点新闻**:")
                for n in news_report.hot_news[:5]:
                    if isinstance(n, dict):
                        parts.append(f"- {n.get('title', n)}")
                    else:
                        parts.append(f"- {n}")
            if getattr(news_report, 'thinking', None):
                parts.append(f"**分析推理**: {news_report.thinking}")

        if micro_event_report is not None:
            parts.append("\n## 微观事件分析\n")
            parts.append(f"**事件数**: {getattr(micro_event_report, 'events_count', 0)}")
            parts.append(f"**情绪变化**: {getattr(micro_event_report, 'sentiment_shift', '')}")
            if getattr(micro_event_report, 'key_insights', None):
                parts.append("**关键洞察**:")
                for i in micro_event_report.key_insights[:5]:
                    parts.append(f"- {i}")
            if getattr(micro_event_report, 'summary', None):
                parts.append(f"**摘要**: {micro_event_report.summary}")
            if getattr(micro_event_report, 'thinking', None):
                parts.append(f"**分析推理**: {micro_event_report.thinking}")

        # 市场情绪与关注度（P1①：股吧真实关注度/排名变化作为辩论温度计）
        if sentiment_data:
            parts.append("\n## 市场情绪与关注度\n")
            parts.append(f"**股吧关注指数**: {sentiment_data.get('discussion_count', 'N/A')}")
            parts.append(f"**全市场人气排名**: {sentiment_data.get('hot_rank', 'N/A')}")
            rank_change = sentiment_data.get("rank_change") or 0
            if rank_change:
                parts.append(f"**排名变化**: {'上升' if rank_change > 0 else '下降'} {abs(rank_change)} 位")
            else:
                parts.append("**排名变化**: 平稳")
            if sentiment_data.get("spike_detected"):
                parts.append("**舆情爆发**: 关注度显著上升（排名快速攀升，短期波动或放大）")
            parts.append("**解读**: 关注度是市场情绪温度计——极端过热需警惕短期回调，"
                         "冷清期机会可能被低估；排名骤升常伴随波动放大。")

        # 完整报告 md 文件引用（三层架构第2层）：LLM 可按需读取全文
        if report_paths:
            parts.append("\n## 完整报告文件（可按需读取细节）")
            for label, p in report_paths.items():
                parts.append(f"- {label}: {p}")

        if current_price:
            parts.append(f"\n**当前价格**: {current_price:.2f}")

        return "\n".join(parts)

    async def debate(
        self,
        analysis_report: Any,
        news_report: Any = None,
        fundamental_report: Any = None,
        micro_event_report: Any = None,
        current_price: Optional[float] = None,
        report_paths: Optional[Dict[str, str]] = None,
        sentiment_data: Optional[Dict[str, Any]] = None,
        run_dir: Optional[str] = None,
    ) -> StrategySignal:
        """执行多空辩论并生成最终策略信号

        多轮辩论（P1① 升级）：每轮 = 多头论点 → 空头论点 → 裁判裁决；
        裁判分歧收敛（|bull-bear| < 0.1）或达轮次上限则停止——支持
        "论点→反驳→收敛"而非固定 3 次调用。情绪/关注度分位注入上下文。

        Args:
            analysis_report: 技术面分析报告
            news_report: 新闻分析报告(可选)
            fundamental_report: 基本面分析报告(可选)
            micro_event_report: 微观事件分析报告(可选)
            current_price: 当前价格(可选)
            report_paths: 各报告 md 落盘路径字典
            sentiment_data: 股吧关注度/排名数据（FetchSentimentDataTool 输出）
            run_dir: 流水线 run 目录；提供则把 BullBearDebateResult 落盘 debate.json（B5 修复）

        Returns:
            StrategySignal 策略信号
        """
        assert self._llm is not None
        symbol = getattr(analysis_report, "symbol", "unknown")

        self._logger.info(f"[{symbol}] 开始多空辩论（多轮）...")
        context = self._build_debate_context(
            analysis_report, news_report, fundamental_report, micro_event_report, current_price,
            report_paths=report_paths, sentiment_data=sentiment_data,
        )

        max_rounds = int(getattr(self, "max_debate_rounds", 2) or 2)
        bull_strength, bear_strength = 0.5, 0.5
        prev_bull = prev_bear = prev_judge = ""
        debate_data: Dict[str, Any] = {}
        final_judge = ""

        for round_i in range(1, max_rounds + 1):
            self._logger.info(f"[{symbol}] 辩论第 {round_i}/{max_rounds} 轮")

            # 第一角色：多头（第2轮起需反驳空头论点 + 参考裁判反馈）
            await self.emit_progress(f"📊 多头论点生成中（第 {round_i} 轮）...")
            bull_prompt = (
                f"## 标的: {symbol}\n\n{context}\n\n"
                "请作为多头研究员，列出所有支持看涨的理由。\n"
                "先在开头用 3-5 句逐步展示你的推理过程（关键证据与逻辑链条），"
                "然后再输出JSON格式：\n"
                "- arguments: 看涨论点列表(每个论点包含 reason 和 strength)\n"
                "- overall_strength: 多头整体强度(0-1)\n"
                "- weaknesses: 多头论点的潜在弱点"
            )
            if round_i > 1 and (prev_bear or prev_judge):
                bull_prompt += (
                    f"\n\n### 需反驳的空头论点（第 {round_i - 1} 轮）\n{prev_bear}"
                    f"\n\n### 裁判上一轮反馈\n{prev_judge}"
                    "\n\n请针对性反驳空头最强论点，并回应对裁判指出的弱点。"
                )
            bull_response = await self._llm.chat(
                message=bull_prompt,
                system=BULL_ANALYST_SYSTEM_PROMPT,
                temperature=0.4,
            )
            prev_bull = bull_response

            # 第二角色：空头（同理反驳多头）
            await self.emit_progress(f"📉 空头论点生成中（第 {round_i} 轮）...")
            bear_prompt = (
                f"## 标的: {symbol}\n\n{context}\n\n"
                "请作为空头研究员，列出所有支持看跌的理由。\n"
                "先在开头用 3-5 句逐步展示你的推理过程（关键证据与逻辑链条），"
                "然后再输出JSON格式：\n"
                "- arguments: 看跌论点列表(每个论点包含 reason 和 strength)\n"
                "- overall_strength: 空头整体强度(0-1)\n"
                "- weaknesses: 空头论点的潜在弱点"
            )
            if round_i > 1 and (prev_bull or prev_judge):
                bear_prompt += (
                    f"\n\n### 需反驳的多头论点（第 {round_i - 1} 轮）\n{prev_bull}"
                    f"\n\n### 裁判上一轮反馈\n{prev_judge}"
                    "\n\n请针对性反驳多头最强论点，并回应对裁判指出的弱点。"
                )
            bear_response = await self._llm.chat(
                message=bear_prompt,
                system=BEAR_ANALYST_SYSTEM_PROMPT,
                temperature=0.4,
            )
            prev_bear = bear_response

            # 第三角色：裁判裁决
            await self.emit_progress(f"⚖️ 裁判综合评估中（第 {round_i} 轮）...")
            judge_prompt = (
                f"## 标的: {symbol}\n\n"
                f"### 原始分析\n{context}\n\n"
                f"### 多头论点\n{bull_response}\n\n"
                f"### 空头论点\n{bear_response}\n\n"
                "请作为裁判，综合多空双方论点做出最终判断。\n"
                "先在开头用 3-5 句逐步展示你的权衡推理（关键争议如何裁决），"
                "然后再输出JSON格式：\n"
                "- bull_arguments: 提取的多头核心论点列表\n"
                "- bear_arguments: 提取的空头核心论点列表\n"
                "- bull_strength: 多头论点强度(0-1)\n"
                "- bear_strength: 空头论点强度(0-1)\n"
                "- consensus: 共识方向(bullish/bearish/neutral)\n"
                "- confidence: 共识置信度(0-1)\n"
                "- key_debates: 关键争议点列表\n"
                "- conclusion: 综合结论(100字以内)"
            )
            if round_i > 1:
                judge_prompt += (
                    f"\n\n### 上一轮裁决记录\n{prev_judge}"
                    "\n请说明本轮与上轮的分歧是否收敛，并给出最终裁决。"
                )
            judge_response = await self._llm.chat(
                message=judge_prompt,
                system=DEBATE_JUDGE_SYSTEM_PROMPT,
                temperature=0.2,
            )
            prev_judge = judge_response
            final_judge = judge_response

            # 解析本轮裁判结果
            try:
                import json as _json
                debate_data = self._llm._extract_json(judge_response)
                bull_strength = float(debate_data.get("bull_strength", 0.5) or 0.5)
                bear_strength = float(debate_data.get("bear_strength", 0.5) or 0.5)
            except Exception as e:
                self._logger.warning(f"[{symbol}] 第 {round_i} 轮裁判解析失败: {e}")
                if round_i == max_rounds:
                    raise ValueError(f"裁判结果解析失败: {e}") from e
                continue

            # 收敛早停：多空强度分歧足够小 → 辩论收敛
            if abs(bull_strength - bear_strength) < 0.1:
                self._logger.info(f"[{symbol}] 辩论收敛（|bull-bear|={abs(bull_strength - bear_strength):.2f}），第 {round_i} 轮停止")
                break

        # 最终裁判结果（末轮或收敛轮）
        try:
            debate_result = BullBearDebateResult(
                symbol=symbol,
                bull_arguments=debate_data.get("bull_arguments", []),
                bear_arguments=debate_data.get("bear_arguments", []),
                bull_strength=debate_data.get("bull_strength", 0.5),
                bear_strength=debate_data.get("bear_strength", 0.5),
                consensus=debate_data.get("consensus", "neutral"),
                confidence=debate_data.get("confidence", 0.0),
                key_debates=debate_data.get("key_debates", []),
                conclusion=debate_data.get("conclusion", ""),
            )
        except Exception as e:
            self._logger.error(f"[{symbol}] 裁判结果解析失败: {e}")
            raise ValueError(f"裁判结果解析失败: {e}") from e

        self._logger.info(
            f"[{symbol}] 多空辩论完成: consensus={debate_result.consensus}, "
            f"bull={debate_result.bull_strength:.2f}, bear={debate_result.bear_strength:.2f}, "
            f"confidence={debate_result.confidence:.2f}"
        )

        # B5 修复：辩论结果落盘到 run 目录（供决策报告/置信度合成消费，
        # 原先仅打印日志，流水线重启后不可追溯）
        if run_dir:
            try:
                os.makedirs(run_dir, exist_ok=True)
                debate_path = os.path.join(run_dir, "debate.json")
                with open(debate_path, "w", encoding="utf-8") as f:
                    json.dump(debate_result.model_dump(), f, ensure_ascii=False, indent=2)
                self._logger.info(f"[{symbol}] 辩论结果已落盘: {debate_path}")
            except Exception as e:
                self._logger.warning(f"[{symbol}] 辩论结果落盘失败: {e}")

        # 综合辩论结果生成最终策略信号
        await self.emit_progress("🎯 策略信号生成中...")
        return await self._generate_signal_from_debate(
            symbol=symbol,
            analysis_report=analysis_report,
            debate_result=debate_result,
            current_price=current_price,
        )

    async def _generate_signal_from_debate(
        self,
        symbol: str,
        analysis_report: Any,
        debate_result: BullBearDebateResult,
        current_price: Optional[float],
    ) -> StrategySignal:
        """根据多空辩论结果生成最终策略信号"""
        assert self._llm is not None

        # 组装最终生成上下文
        final_prompt = (
            f"## 标的: {symbol}\n\n"
            f"### 技术面分析\n"
            f"市场状态: {getattr(analysis_report.market_state, 'value', '')}, "
            f"趋势: {getattr(analysis_report.trend_direction, 'value', '')}, "
            f"置信度: {getattr(analysis_report, 'confidence', 0):.2f}\n\n"
            f"### 多空辩论结果\n"
            f"共识方向: {debate_result.consensus}\n"
            f"共识置信度: {debate_result.confidence:.2f}\n"
            f"多头强度: {debate_result.bull_strength:.2f}, 空头强度: {debate_result.bear_strength:.2f}\n"
            f"多头论点: {', '.join(debate_result.bull_arguments[:5])}\n"
            f"空头论点: {', '.join(debate_result.bear_arguments[:5])}\n"
            f"裁判结论: {debate_result.conclusion}"
        )
        if current_price:
            final_prompt += f"\n\n当前价格: {current_price:.2f}"

        try:
            signal = await self._llm.chat_structured(
                message=final_prompt,
                response_model=StrategySignal,
                system=STRATEGY_GENERATOR_SYSTEM_PROMPT,
                temperature=0.3,
            )
            self._logger.info(
                f"[{symbol}] 辩论后策略生成完成: 方向={signal.direction.value}, "
                f"置信度={signal.confidence:.2f}"
            )
            return signal
        except Exception as e:
            self._logger.error(f"[{symbol}] 辩论后策略生成失败: {e}")
            raise
