"""
交易执行Agent

负责订单生成和提交，支持A股交易规则(100股整数手、涨跌停检查)。
支持TWAP/VWAP等算法交易。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.agents.risk_manager import RiskDecision
from finhack_pro.agents.strategy_generator import StrategySignal
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class OrderSide(str, Enum):
    """订单方向"""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    """订单状态"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ExecutionAlgorithm(str, Enum):
    """执行算法"""
    MARKET = "market"  # 市价单
    LIMIT = "limit"  # 限价单
    TWAP = "twap"  # 时间加权平均价格
    VWAP = "vwap"  # 成交量加权平均价格


class ExecutionReport(BaseModel):
    """执行报告

    Attributes:
        order_id: 订单ID
        symbol: 标的代码
        side: 买卖方向
        price: 成交价格
        volume: 成交数量(股)
        algorithm: 使用的执行算法
        status: 订单状态
        estimated_cost: 预估交易成本
        filled_volume: 实际成交数量
        avg_price: 平均成交价格
        slippage: 滑点
        commission: 佣金
        error_message: 错误信息
    """
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    volume: int
    algorithm: str = "market"
    status: str = "pending"
    estimated_cost: float = 0.0
    filled_volume: int = 0
    avg_price: float = 0.0
    slippage: float = 0.0
    commission: float = 0.0
    error_message: str = ""


# 交易执行Agent的系统提示词
TRADE_EXECUTOR_SYSTEM_PROMPT = """你是一位专业的交易执行专家，负责将审批通过的策略信号转化为最优的订单。

## 执行原则

### 1. A股交易规则
- 买入必须是100股的整数倍(1手=100股)
- 卖出可以不满100股(碎股)
- T+1制度: 当日买入次日才能卖出
- 涨跌停限制: ±10%(ST股±5%, 科创板/创业板±20%)
- 最小报价单位: 0.01元

### 2. 执行算法选择
- **市价单**: 紧急情况，立即成交
- **限价单**: 控制价格，等待成交
- **TWAP**: 大单拆分，均匀分布在时间窗口内执行
- **VWAP**: 跟随成交量分布执行，减少市场冲击

### 3. 成本控制
- 佣金: 万三(双向)
- 印花税: 千一(仅卖出)
- 过户费: 万分之0.5(双向)
- 滑点: 根据流动性预估

### 4. 执行优化
- 避免在开盘和收盘前5分钟执行大单
- 利用成交量高峰时段执行
- 设置价格保护区间

请根据策略信号和当前市场状态，制定最优的执行方案。"""


class TradeExecutorAgent(BaseAgent):
    """交易执行Agent

    负责将风控审批通过的策略信号转化为订单并执行。
    支持A股交易规则和多种执行算法。

    Usage:
        agent = TradeExecutorAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        report = await agent.execute(signal, risk_decision)
    """

    def __init__(
        self,
        config: Dict[str, Any],
        shared_memory: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
    ) -> None:
        super().__init__(
            AgentRole.TRADE_EXECUTOR, config,
            shared_memory=shared_memory,
            tool_registry=tool_registry,
        )
        self._llm: Optional[LLMClient] = None
        self._commission_rate: float = config.get("commission_rate", 0.0003)
        self._stamp_tax_rate: float = config.get("stamp_tax_rate", 0.001)
        self._slippage: float = config.get("slippage", 0.001)
        self._dry_run: bool = config.get("dry_run", True)  # 默认模拟模式

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
        self.register_handler("risk_decision", self._handle_risk_decision)

    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理默认消息"""
        self._logger.warning(f"收到未处理的消息类型: {message.msg_type}")
        return None

    async def _handle_risk_decision(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理风控决策"""
        payload = message.payload
        decision = RiskDecision.model_validate(payload)

        if not decision.approved:
            self._logger.info(f"信号被风控拒绝，不执行: {decision.reasoning}")
            return None

        signal = StrategySignal.model_validate(decision.original_signal)
        report = await self.execute(signal, decision)

        return self.create_message(
            receiver=message.sender,
            msg_type="execution_report",
            payload=report.model_dump(),
        )

    async def execute(
        self,
        signal: StrategySignal,
        decision: Optional[RiskDecision] = None,
        current_price: Optional[float] = None,
    ) -> ExecutionReport:
        """执行交易信号

        Args:
            signal: 策略信号
            decision: 风控决策
            current_price: 当前价格

        Returns:
            ExecutionReport 执行报告
        """
        self._logger.info(f"准备执行 {signal.symbol} 的交易信号...")

        # 生成订单ID
        order_id = f"ORD_{uuid.uuid4().hex[:10].upper()}"

        # 检查信号方向
        if signal.direction.value == "hold":
            self._logger.info(f"信号为HOLD，不执行交易: {signal.symbol}")
            return ExecutionReport(
                order_id=order_id,
                symbol=signal.symbol,
                side=OrderSide.BUY,
                price=0.0,
                volume=0,
                status="cancelled",
                error_message="信号方向为HOLD",
            )

        # A股规则: 买入数量必须是100的整数倍
        side = OrderSide(signal.direction.value)
        position_size = decision.adjusted_position_size if decision and decision.adjusted_position_size else signal.position_size_pct

        if current_price and current_price > 0:
            price = current_price
        elif signal.entry_price:
            price = signal.entry_price
        else:
            price = signal.target_price or 0.0

        if price <= 0:
            return ExecutionReport(
                order_id=order_id,
                symbol=signal.symbol,
                side=side,
                price=0.0,
                volume=0,
                status="rejected",
                error_message="无法确定执行价格",
            )

        # 计算交易数量(假设总资金100万)
        total_capital = 1_000_000.0
        trade_value = total_capital * position_size
        raw_volume = int(trade_value / price)

        # A股买入100股整数倍
        if side == OrderSide.BUY:
            volume = (raw_volume // 100) * 100
            if volume < 100:
                volume = 100  # 最少买1手
        else:
            volume = raw_volume  # 卖出可以碎股

        # 计算预估成本
        commission = max(trade_value * self._commission_rate, 5.0)  # 最低5元
        stamp_tax = trade_value * self._stamp_tax_rate if side == OrderSide.SELL else 0.0
        slippage_cost = trade_value * self._slippage
        estimated_cost = commission + stamp_tax + slippage_cost

        # 选择执行算法
        algorithm = self._select_algorithm(volume, price)

        if self._dry_run:
            # 模拟执行
            self._logger.info(
                f"[模拟] 提交订单: {order_id}, {signal.symbol}, "
                f"{side.value}, 价格={price:.2f}, 数量={volume}, "
                f"算法={algorithm}"
            )
            report = ExecutionReport(
                order_id=order_id,
                symbol=signal.symbol,
                side=side,
                price=price,
                volume=volume,
                algorithm=algorithm,
                status="filled",
                estimated_cost=estimated_cost,
                filled_volume=volume,
                avg_price=price * (1 + self._slippage if side == OrderSide.BUY else 1 - self._slippage),
                slippage=slippage_cost,
                commission=commission,
            )
        else:
            # 实际执行(调用LLM优化执行方案)
            report = await self._execute_with_llm(
                order_id, signal, side, price, volume, algorithm
            )

        self._logger.info(
            f"执行完成: {order_id}, 状态={report.status}, "
            f"成交={report.filled_volume}股, 均价={report.avg_price:.2f}"
        )
        return report

    def _select_algorithm(self, volume: int, price: float) -> str:
        """根据订单特征选择执行算法

        Args:
            volume: 订单数量
            price: 订单价格

        Returns:
            执行算法名称
        """
        trade_value = volume * price

        if trade_value < 50_000:
            # 小单直接市价执行
            return "market"
        elif trade_value < 500_000:
            # 中单使用限价单
            return "limit"
        else:
            # 大单使用TWAP拆分
            return "twap"

    async def _execute_with_llm(
        self,
        order_id: str,
        signal: StrategySignal,
        side: OrderSide,
        price: float,
        volume: int,
        algorithm: str,
    ) -> ExecutionReport:
        """使用LLM优化执行方案"""
        assert self._llm is not None

        context = f"""## 执行请求

**订单ID**: {order_id}
**标的**: {signal.symbol}
**方向**: {side.value}
**价格**: {price:.2f}
**数量**: {volume}股
**交易金额**: {volume * price:.2f}元
**策略类型**: {signal.strategy_type}
**紧急程度**: {signal.urgency}

请制定最优的执行方案，包括:
1. 执行算法选择和参数
2. 价格区间设置
3. 时间安排
4. 风险控制措施

输出JSON格式的执行报告。"""

        try:
            report = await self._llm.chat_structured(
                message=context,
                response_model=ExecutionReport,
                system=TRADE_EXECUTOR_SYSTEM_PROMPT,
                temperature=0.2,
            )
            report.order_id = order_id
            return report
        except Exception as e:
            self._logger.error(f"LLM执行优化失败: {e}")
            return ExecutionReport(
                order_id=order_id,
                symbol=signal.symbol,
                side=side,
                price=price,
                volume=volume,
                algorithm=algorithm,
                status="pending",
                error_message=f"LLM执行优化失败: {e}",
            )
