"""
Agent基类模块

定义所有Agent的抽象接口和消息传递协议。
支持共享记忆(SharedMemory)和共享工具集(ToolRegistry)。
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class AgentRole(str, Enum):
    """Agent角色枚举"""
    MARKET_ANALYZER = "market_analyzer"
    NEWS_ANALYST = "news_analyst"
    FUNDAMENTAL_ANALYST = "fundamental_analyst"
    MICRO_EVENT_MONITOR = "micro_event_monitor"
    STRATEGY_GENERATOR = "strategy_generator"
    RISK_MANAGER = "risk_manager"
    TRADE_EXECUTOR = "trade_executor"


class AgentMessage(BaseModel):
    """Agent间通信消息

    Attributes:
        msg_id: 消息唯一标识
        sender: 发送者Agent ID
        receiver: 接收者Agent ID
        msg_type: 消息类型
        payload: 消息负载(数据内容)
        timestamp: 时间戳
        priority: 消息优先级(0=普通, 越大越优先)
    """
    msg_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    sender: str
    receiver: str
    msg_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=lambda: __import__("time").time())
    priority: int = 0


class BaseAgent(ABC):
    """Agent基类，所有Agent需继承此类

    提供消息处理、生命周期管理、消息路由等基础能力。
    支持共享记忆(SharedMemory)和共享工具集(ToolRegistry)。
    子类需实现 process() 和 on_init() 方法。

    Attributes:
        role: Agent角色
        config: Agent配置字典
        agent_id: Agent唯一标识
        shared_memory: 共享记忆系统实例(可选)
        tool_registry: 工具注册中心实例(可选)
    """

    def __init__(
        self,
        role: AgentRole,
        config: Dict[str, Any],
        shared_memory: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
    ) -> None:
        self.role = role
        self.config = config
        self.agent_id = f"{role.value}_{id(self):08x}"
        self._message_handlers: Dict[str, Any] = {}
        self._running = False
        self._logger = get_logger(f"agent.{role.value}")

        # 共享记忆和工具集(向后兼容，默认为None)
        self.shared_memory = shared_memory
        self.tool_registry = tool_registry

    @abstractmethod
    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理接收到的消息，返回响应消息

        Args:
            message: 接收到的消息

        Returns:
            响应消息，无响应时返回None
        """
        ...

    @abstractmethod
    async def on_init(self) -> None:
        """Agent初始化，在start()时自动调用"""
        ...

    async def start(self) -> None:
        """启动Agent"""
        self._logger.info(f"Agent [{self.agent_id}] 正在启动...")
        await self.on_init()
        self._running = True
        self._logger.info(f"Agent [{self.agent_id}] 启动完成")

    async def stop(self) -> None:
        """停止Agent"""
        self._running = False
        self._logger.info(f"Agent [{self.agent_id}] 已停止")

    @property
    def is_running(self) -> bool:
        """Agent是否正在运行"""
        return self._running

    def register_handler(self, msg_type: str, handler: Any) -> None:
        """注册消息处理器

        Args:
            msg_type: 消息类型
            handler: 异步处理函数，签名为 async def handler(message: AgentMessage) -> Optional[AgentMessage]
        """
        self._message_handlers[msg_type] = handler
        self._logger.debug(f"注册消息处理器: {msg_type}")

    async def handle_message(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理消息

        优先查找已注册的消息处理器，未找到则调用 process() 方法。

        Args:
            message: 接收到的消息

        Returns:
            响应消息
        """
        self._logger.debug(
            f"收到消息: type={message.msg_type}, from={message.sender}, "
            f"to={message.receiver}"
        )
        handler = self._message_handlers.get(message.msg_type)
        if handler:
            return await handler(message)
        return await self.process(message)

    def create_message(
        self,
        receiver: str,
        msg_type: str,
        payload: Dict[str, Any],
        priority: int = 0,
    ) -> AgentMessage:
        """创建消息

        Args:
            receiver: 接收者Agent ID
            msg_type: 消息类型
            payload: 消息负载
            priority: 消息优先级

        Returns:
            AgentMessage实例
        """
        return AgentMessage(
            sender=self.agent_id,
            receiver=receiver,
            msg_type=msg_type,
            payload=payload,
            priority=priority,
        )

    # ============================================================
    # 共享记忆便捷方法
    # ============================================================

    async def store_memory(
        self,
        content: str,
        memory_type: Any,
        importance: Any = None,
        structured_data: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[str]:
        """存储一条记忆到共享记忆系统

        Args:
            content: 记忆内容(自然语言描述)
            memory_type: 记忆类型(MemoryType枚举值)
            importance: 记忆重要性(MemoryImportance枚举值)，默认MEDIUM
            structured_data: 结构化数据(可选)
            tags: 标签列表(可选)

        Returns:
            记忆ID，若共享记忆未配置则返回None
        """
        if not self.shared_memory:
            self._logger.debug("共享记忆未配置，跳过 store_memory")
            return None

        # 延迟导入避免循环依赖
        from finhack_pro.agents.shared_memory import MemoryImportance

        if importance is None:
            importance = MemoryImportance.MEDIUM

        memory_id = await self.shared_memory.store(
            agent_id=self.agent_id,
            memory_type=memory_type,
            content=content,
            structured_data=structured_data,
            importance=importance,
            tags=tags,
        )
        self._logger.debug(f"存储记忆: {memory_id}")
        return memory_id

    async def recall_memories(
        self,
        memory_type: Any = None,
        keywords: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        limit: int = 20,
    ) -> list:
        """从共享记忆中检索记忆

        Args:
            memory_type: 按记忆类型过滤(可选)
            keywords: 关键词列表(可选)
            tags: 标签列表(可选)
            limit: 返回数量上限

        Returns:
            MemoryEntry列表，若共享记忆未配置则返回空列表
        """
        if not self.shared_memory:
            self._logger.debug("共享记忆未配置，跳过 recall_memories")
            return []

        memories = await self.shared_memory.retrieve(
            memory_type=memory_type,
            keywords=keywords,
            tags=tags,
            limit=limit,
        )
        return memories

    # ============================================================
    # 工具集便捷方法
    # ============================================================

    async def use_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """调用共享工具集中的工具

        Args:
            tool_name: 工具名称
            **kwargs: 工具参数

        Returns:
            工具调用结果字典，包含 success 和 result/error 字段
        """
        if not self.tool_registry:
            self._logger.debug("工具集未配置，跳过 use_tool")
            return {"success": False, "error": "工具集未配置"}

        result = await self.tool_registry.call_tool(
            tool_name=tool_name,
            args=kwargs,
            caller_agent_id=self.agent_id,
        )
        self._logger.debug(f"调用工具 {tool_name}: success={result.get('success')}")
        return result

    def get_available_tools(self) -> List[str]:
        """获取当前Agent可用的工具名称列表

        Returns:
            工具名称列表，若工具集未配置则返回空列表
        """
        if not self.tool_registry:
            return []
        definitions = self.tool_registry.list_tools(agent_role=self.role.value)
        return [d.name for d in definitions]

    # ============================================================
    # 上下文Prompt生成
    # ============================================================

    async def get_context_prompt(
        self,
        include_self_memory: bool = True,
        include_global_memory: bool = True,
        include_available_tools: bool = True,
        memory_limit: int = 10,
        global_memory_limit: int = 20,
    ) -> str:
        """自动从共享记忆和工具集中获取上下文，格式化为LLM prompt

        将当前Agent的历史记忆、全局记忆、可用工具信息组装成
        结构化的prompt文本，可直接注入到LLM的system/user消息中。

        Args:
            include_self_memory: 是否包含当前Agent的历史记忆
            include_global_memory: 是否包含全局记忆
            include_available_tools: 是否包含可用工具列表
            memory_limit: 自身记忆条数上限
            global_memory_limit: 全局记忆条数上限

        Returns:
            格式化后的上下文prompt字符串
        """
        sections = []

        # 自身记忆上下文
        if include_self_memory and self.shared_memory:
            agent_context = await self.shared_memory.get_agent_context(
                agent_id=self.agent_id,
                n=memory_limit,
            )
            sections.append(f"## 你的历史记忆\n{agent_context}")

        # 全局记忆上下文
        if include_global_memory and self.shared_memory:
            full_context = await self.shared_memory.get_full_context(
                n=global_memory_limit,
            )
            sections.append(f"## 全局共享记忆\n{full_context}")

        # 可用工具列表
        if include_available_tools and self.tool_registry:
            tools = self.get_available_tools()
            if tools:
                tool_lines = []
                for tool_name in tools:
                    tool = self.tool_registry.get_tool(tool_name)
                    if tool:
                        defn = tool.definition
                        params_desc = ", ".join(
                            f"{p.name}({p.type})" for p in defn.parameters
                        )
                        tool_lines.append(f"- {tool_name}({params_desc}): {defn.description}")
                sections.append("## 可用工具\n" + "\n".join(tool_lines))

        if not sections:
            return ""

        header = f"=== Agent上下文 [{self.agent_id}] ===\n"
        return header + "\n\n".join(sections)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} role={self.role.value} id={self.agent_id}>"

    def set_llm_stream_callbacks(
        self,
        on_token: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
    ) -> None:
        """注入 LLM 流式/推理回调（WebUI 实时思考链展示用）。

        子类在 on_init 中创建 self._llm 后生效；未创建 LLMClient 的 agent
        静默跳过。调用方在流水线结束后应注入 None 清理。
        """
        llm = getattr(self, "_llm", None)
        if llm is not None and hasattr(llm, "set_stream_callbacks"):
            llm.set_stream_callbacks(on_token=on_token, on_reasoning=on_reasoning)
