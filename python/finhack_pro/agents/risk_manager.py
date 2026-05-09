"""
风险管理Agent

实时监控组合风险，对策略信号进行风控审批。
内置风控规则: 仓位限制、回撤限制、VaR检查、相关性检查。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.agents.strategy_generator import StrategySignal
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class RiskDecision(BaseModel):
    """风控决策

    Attributes:
        approved: 是否通过风控审批
        adjusted_position_size: 调整后的仓位百分比
        risk_alerts: 风险预警列表
        max_loss_today: 今日最大可接受亏损
        portfolio_var: 组合VaR值
        reasoning: 决策理由
        original_signal: 原始策略信号
        adjustments: 调整说明
    """
    approved: bool
    adjusted_position_size: Optional[float] = None
    risk_alerts: List[str] = Field(default_factory=list)
    max_loss_today: float = 0.0
    portfolio_var: float = 0.0
    reasoning: str = ""
    original_signal: Dict[str, Any] = Field(default_factory=dict)
    adjustments: str = ""


class PortfolioState(BaseModel):
    """组合状态"""
    total_value: float = 0.0
    cash: float = 0.0
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_value: float = 0.0


# 风险管理Agent的系统提示词
RISK_MANAGER_SYSTEM_PROMPT = """你是一位严谨的量化风控专家，负责审核每一笔交易信号的风险。

## 风控审核原则

### 1. 仓位控制
- 单只股票仓位不超过总资金的30%
- 总仓位不超过80%
- 高风险信号降低仓位
- 同一行业集中度不超过40%

### 2. 止损纪律
- 个股止损线: -5%
- 组合日亏损上限: -3%
- 组合最大回撤: -15%
- 连续亏损3次后降低仓位50%

### 3. 风险评估
- VaR(95%置信度)不超过总资金的5%
- 波动率异常时暂停交易
- 流动性不足时限制交易量

### 4. 相关性控制
- 持仓间相关性过高时分散化
- 避免同板块过度集中
- 大盘弱势时降低整体仓位

### 5. 审批标准
- **通过**: 信号置信度>0.6，风险可控，仓位合理
- **有条件通过**: 需要调整仓位或止损位
- **拒绝**: 风险过高，违反风控规则

请严格审核交易信号，保护组合安全。"""


class RiskManagerAgent(BaseAgent):
    """风险管理Agent

    实时监控组合风险，对策略信号进行风控审批。
    内置规则引擎进行初步筛选，LLM进行综合评估。

    Usage:
        agent = RiskManagerAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        decision = await agent.evaluate_risk(signal, portfolio_state)
    """

    def __init__(
        self,
        config: Dict[str, Any],
        shared_memory: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
    ) -> None:
        super().__init__(
            AgentRole.RISK_MANAGER, config,
            shared_memory=shared_memory,
            tool_registry=tool_registry,
        )
        self._llm: Optional[LLMClient] = None
        self._portfolio: PortfolioState = PortfolioState()
        self._daily_loss_limit: float = config.get("daily_loss_limit", 0.05)
        self._max_drawdown_limit: float = config.get("max_drawdown_limit", 0.15)
        self._max_position_pct: float = config.get("max_position_pct", 0.3)
        self._max_total_position: float = config.get("max_total_position", 0.8)
        self._consecutive_losses: int = 0

    async def on_init(self) -> None:
        """初始化LLM客户端"""
        self._llm = LLMClient(
            provider=self.config.get("provider", "openai"),
            api_key=self.config.get("api_key", ""),
            base_url=self.config.get("base_url"),
            model=self.config.get("model", "gpt-4o"),
            temperature=self.config.get("temperature", 0.2),
            max_tokens=self.config.get("max_tokens", 4096),
            timeout=self.config.get("timeout", 60),
            max_retries=self.config.get("max_retries", 3),
        )
        self.register_handler("strategy_signal", self._handle_strategy_signal)
        self.register_handler("execution_report", self._handle_execution_report)

    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理默认消息"""
        self._logger.warning(f"收到未处理的消息类型: {message.msg_type}")
        return None

    async def _handle_strategy_signal(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理策略信号，进行风控审批"""
        payload = message.payload
        signal = StrategySignal.model_validate(payload)

        decision = await self.evaluate_risk(signal)

        return self.create_message(
            receiver=message.sender,
            msg_type="risk_decision",
            payload=decision.model_dump(),
        )

    async def _handle_execution_report(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理执行报告，更新组合状态"""
        payload = message.payload
        # 更新持仓信息
        self._logger.info(f"收到执行报告: {payload.get('order_id', '')}")
        return None

    def update_portfolio(self, portfolio: PortfolioState) -> None:
        """更新组合状态

        Args:
            portfolio: 最新的组合状态
        """
        self._portfolio = portfolio
        self._logger.debug(
            f"组合状态更新: 总值={portfolio.total_value:.2f}, "
            f"持仓数={len(portfolio.positions)}, "
            f"日盈亏={portfolio.daily_pnl:.2f}"
        )

    async def evaluate_risk(
        self,
        signal: StrategySignal,
        portfolio: Optional[PortfolioState] = None,
    ) -> RiskDecision:
        """评估策略信号的风险

        先通过规则引擎进行硬性检查，再使用LLM进行综合评估。

        Args:
            signal: 策略信号
            portfolio: 组合状态(可选，使用最新状态)

        Returns:
            RiskDecision 风控决策
        """
        if portfolio:
            self._portfolio = portfolio

        self._logger.info(f"评估 {signal.symbol} 的交易信号风险...")

        # 第一步: 规则引擎硬性检查
        rule_result = self._rule_engine_check(signal)
        if not rule_result["passed"]:
            self._logger.warning(
                f"信号被规则引擎拒绝: {rule_result['reasons']}"
            )
            return RiskDecision(
                approved=False,
                risk_alerts=rule_result["reasons"],
                reasoning="; ".join(rule_result["reasons"]),
                original_signal=signal.model_dump(),
            )

        # 第二步: LLM综合评估
        decision = await self._llm_evaluate(signal, rule_result)

        self._logger.info(
            f"风控决策: {signal.symbol} -> "
            f"{'通过' if decision.approved else '拒绝'}, "
            f"预警={len(decision.risk_alerts)}"
        )
        return decision

    def _rule_engine_check(self, signal: StrategySignal) -> Dict[str, Any]:
        """规则引擎硬性检查

        Args:
            signal: 策略信号

        Returns:
            包含passed和reasons的字典
        """
        reasons: List[str] = []

        # 检查1: 信号方向为HOLD时直接通过(不交易)
        if signal.direction.value == "hold":
            return {"passed": True, "reasons": [], "warnings": []}

        # 检查2: 单只股票仓位限制
        if signal.position_size_pct > self._max_position_pct:
            reasons.append(
                f"仓位超限: {signal.position_size_pct:.1%} > {self._max_position_pct:.1%}"
            )

        # 检查3: 总仓位限制
        current_total_position = sum(
            pos.get("weight", 0) for pos in self._portfolio.positions
        )
        if current_total_position + signal.position_size_pct > self._max_total_position:
            reasons.append(
                f"总仓位将超限: "
                f"{current_total_position + signal.position_size_pct:.1%} > "
                f"{self._max_total_position:.1%}"
            )

        # 检查4: 日亏损限制
        if self._portfolio.daily_pnl < 0:
            daily_loss_pct = abs(self._portfolio.daily_pnl) / max(
                self._portfolio.total_value, 1
            )
            if daily_loss_pct >= self._daily_loss_limit:
                reasons.append(
                    f"今日亏损已达上限: {daily_loss_pct:.2%} >= {self._daily_loss_limit:.2%}"
                )

        # 检查5: 最大回撤限制
        if self._portfolio.max_drawdown >= self._max_drawdown_limit:
            reasons.append(
                f"最大回撤已达上限: {self._portfolio.max_drawdown:.2%} >= "
                f"{self._max_drawdown_limit:.2%}"
            )

        # 检查6: 连续亏损检查
        if self._consecutive_losses >= 3:
            reasons.append(f"连续亏损{self._consecutive_losses}次，建议暂停交易")

        # 检查7: 重复持仓检查
        existing_symbols = {pos.get("symbol") for pos in self._portfolio.positions}
        if signal.symbol in existing_symbols and signal.direction.value == "buy":
            reasons.append(f"{signal.symbol} 已在持仓中，不建议加仓")

        return {
            "passed": len(reasons) == 0,
            "reasons": reasons,
            "warnings": [],
        }

    async def _llm_evaluate(
        self,
        signal: StrategySignal,
        rule_result: Dict[str, Any],
    ) -> RiskDecision:
        """使用LLM进行综合风险评估

        Args:
            signal: 策略信号
            rule_result: 规则引擎检查结果

        Returns:
            RiskDecision 风控决策
        """
        assert self._llm is not None

        context = self._build_risk_context(signal, rule_result)

        try:
            decision = await self._llm.chat_structured(
                message=context,
                response_model=RiskDecision,
                system=RISK_MANAGER_SYSTEM_PROMPT,
                temperature=0.2,
            )
            decision.original_signal = signal.model_dump()
            return decision

        except Exception as e:
            self._logger.error(f"LLM风控评估失败: {e}")
            # 规则引擎通过则默认通过
            return RiskDecision(
                approved=rule_result["passed"],
                risk_alerts=rule_result["reasons"],
                reasoning=f"LLM评估失败，使用规则引擎结果: {e}",
                original_signal=signal.model_dump(),
            )

    def _build_risk_context(
        self,
        signal: StrategySignal,
        rule_result: Dict[str, Any],
    ) -> str:
        """构建风控评估上下文"""
        parts = [
            "## 交易信号审核请求\n",
            f"**标的**: {signal.symbol}",
            f"**方向**: {signal.direction.value}",
            f"**置信度**: {signal.confidence:.2f}",
            f"**建议仓位**: {signal.position_size_pct:.1%}",
            f"**止损价**: {signal.stop_loss:.2f}",
            f"**止盈价**: {signal.take_profit:.2f}",
            f"**策略类型**: {signal.strategy_type}",
            f"**持有周期**: {signal.time_horizon}",
            f"\n**决策理由**: {signal.reasoning}",
            "\n## 当前组合状态",
            f"- 总资产: {self._portfolio.total_value:.2f}",
            f"- 可用资金: {self._portfolio.cash:.2f}",
            f"- 持仓数量: {len(self._portfolio.positions)}",
            f"- 今日盈亏: {self._portfolio.daily_pnl:.2f}",
            f"- 最大回撤: {self._portfolio.max_drawdown:.2%}",
        ]

        if self._portfolio.positions:
            parts.append("\n### 当前持仓")
            for pos in self._portfolio.positions:
                parts.append(
                    f"- {pos.get('symbol', '')}: "
                    f"仓位={pos.get('weight', 0):.1%}, "
                    f"盈亏={pos.get('pnl', 0):.2f}"
                )

        if rule_result["reasons"]:
            parts.append("\n### 规则引擎警告")
            for reason in rule_result["reasons"]:
                parts.append(f"- [警告] {reason}")

        parts.append(
            "\n\n请综合评估此交易信号的风险，输出JSON格式的风控决策。"
        )

        return "\n".join(parts)
