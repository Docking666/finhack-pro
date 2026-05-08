"""
FinHack Pro AI Agent系统

提供市场分析、策略生成、风险管理、交易执行等智能体，
通过Agent协调器实现多Agent协作决策。
"""

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.coordinator import AgentCoordinator
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.agents.market_analyzer import MarketAnalyzerAgent
from finhack_pro.agents.risk_manager import RiskManagerAgent
from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent
from finhack_pro.agents.trade_executor import TradeExecutorAgent

__all__ = [
    "AgentMessage",
    "AgentRole",
    "BaseAgent",
    "AgentCoordinator",
    "LLMClient",
    "MarketAnalyzerAgent",
    "StrategyGeneratorAgent",
    "RiskManagerAgent",
    "TradeExecutorAgent",
]
