"""
Agent系统测试
"""

from typing import Any

import pytest

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.fundamental_analyst import (
    FundamentalAnalysisReport,
    FundamentalAnalystAgent,
)
from finhack_pro.agents.news_analyst import NewsAnalysisReport, NewsAnalystAgent
from finhack_pro.agents.shared_memory import (
    MemoryEntry,
    MemoryImportance,
    MemoryType,
    SharedMemory,
)
from finhack_pro.agents.tool_registry import (
    BaseTool,
    ToolCategory,
    ToolDefinition,
    ToolParameter,
    ToolRegistry,
    create_default_toolkit,
)


class DummyAgent(BaseAgent):
    """用于测试的虚拟Agent"""

    async def process(self, message: AgentMessage):
        return self.create_message(
            receiver=message.sender,
            msg_type="response",
            payload={"echo": message.payload},
        )

    async def on_init(self):
        pass


@pytest.mark.asyncio
async def test_agent_creation():
    """测试Agent创建"""
    agent = DummyAgent(role=AgentRole.MARKET_ANALYZER, config={"key": "value"})
    assert agent.role == AgentRole.MARKET_ANALYZER
    assert agent.config["key"] == "value"
    assert agent.agent_id.startswith("market_analyzer_")


@pytest.mark.asyncio
async def test_agent_lifecycle():
    """测试Agent生命周期"""
    agent = DummyAgent(role=AgentRole.STRATEGY_GENERATOR, config={})
    assert not agent.is_running

    await agent.start()
    assert agent.is_running

    await agent.stop()
    assert not agent.is_running


@pytest.mark.asyncio
async def test_agent_message_handling():
    """测试Agent消息处理"""
    agent = DummyAgent(role=AgentRole.RISK_MANAGER, config={})
    await agent.start()

    message = AgentMessage(
        sender="test_sender",
        receiver=agent.agent_id,
        msg_type="test",
        payload={"data": "hello"},
    )

    response = await agent.handle_message(message)
    assert response is not None
    assert response.msg_type == "response"
    assert response.payload["echo"]["data"] == "hello"


@pytest.mark.asyncio
async def test_agent_message_handler_registration():
    """测试消息处理器注册"""
    agent = DummyAgent(role=AgentRole.TRADE_EXECUTOR, config={})
    await agent.start()

    custom_called = False

    async def custom_handler(msg: AgentMessage):
        nonlocal custom_called
        custom_called = True
        return None

    agent.register_handler("custom_type", custom_handler)

    message = AgentMessage(
        sender="test",
        receiver=agent.agent_id,
        msg_type="custom_type",
        payload={},
    )

    await agent.handle_message(message)
    assert custom_called


@pytest.mark.asyncio
async def test_agent_message_model():
    """测试AgentMessage模型"""
    msg = AgentMessage(
        sender="agent_a",
        receiver="agent_b",
        msg_type="test_type",
        payload={"key": "value"},
        priority=5,
    )
    assert msg.msg_id  # 自动生成
    assert msg.sender == "agent_a"
    assert msg.receiver == "agent_b"
    assert msg.timestamp > 0
    assert msg.priority == 5

    # 序列化测试
    data = msg.model_dump()
    assert data["sender"] == "agent_a"
    assert data["payload"]["key"] == "value"


@pytest.mark.asyncio
async def test_agent_role_enum():
    """测试AgentRole枚举"""
    assert AgentRole.MARKET_ANALYZER.value == "market_analyzer"
    assert AgentRole.STRATEGY_GENERATOR.value == "strategy_generator"
    assert AgentRole.RISK_MANAGER.value == "risk_manager"
    assert AgentRole.TRADE_EXECUTOR.value == "trade_executor"


# ============================================================
# SharedMemory 测试
# ============================================================


@pytest.mark.asyncio
async def test_shared_memory_store_and_retrieve():
    """测试SharedMemory存储和检索记忆"""
    memory = SharedMemory()

    # 存储一条记忆
    memory_id = await memory.store(
        agent_id="test_agent",
        memory_type=MemoryType.MARKET_OBSERVATION,
        content="贵州茅台今日上涨3.5%",
        structured_data={"symbol": "600519.SH", "change_pct": 3.5},
        importance=MemoryImportance.HIGH,
        tags=["茅台", "上涨"],
    )

    assert memory_id is not None
    assert len(memory_id) == 12  # MD5前12位

    # 按ID检索
    entry = await memory.get(memory_id)
    assert entry is not None
    assert entry.content == "贵州茅台今日上涨3.5%"
    assert entry.memory_type == MemoryType.MARKET_OBSERVATION
    assert entry.importance == MemoryImportance.HIGH
    assert entry.agent_id == "test_agent"
    assert entry.structured_data["symbol"] == "600519.SH"
    assert "茅台" in entry.tags

    # 检索所有记忆
    results = await memory.retrieve()
    assert len(results) >= 1
    assert results[0].content == "贵州茅台今日上涨3.5%"


@pytest.mark.asyncio
async def test_shared_memory_type_filter():
    """测试SharedMemory按类型过滤"""
    memory = SharedMemory()

    # 存储不同类型的记忆
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.MARKET_OBSERVATION,
        content="市场观察数据",
    )
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.NEWS_EVENT,
        content="新闻事件数据",
    )
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.STRATEGY_DECISION,
        content="策略决策数据",
    )

    # 按类型过滤
    news_results = await memory.retrieve(memory_type=MemoryType.NEWS_EVENT)
    assert len(news_results) == 1
    assert news_results[0].content == "新闻事件数据"

    # 按类型过滤 - 市场观察
    market_results = await memory.retrieve(memory_type=MemoryType.MARKET_OBSERVATION)
    assert len(market_results) == 1
    assert market_results[0].content == "市场观察数据"

    # 无过滤 - 应返回全部
    all_results = await memory.retrieve()
    assert len(all_results) == 3


@pytest.mark.asyncio
async def test_shared_memory_keyword_search():
    """测试SharedMemory关键词搜索"""
    memory = SharedMemory()

    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.NEWS_EVENT,
        content="贵州茅台发布年报，营收同比增长15%",
        tags=["茅台", "年报"],
    )
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.NEWS_EVENT,
        content="宁德时代获得海外大单",
        tags=["宁德时代", "订单"],
    )
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.MARKET_OBSERVATION,
        content="大盘指数震荡调整",
        tags=["大盘"],
    )

    # 关键词搜索 - "茅台"
    results = await memory.retrieve(keywords=["茅台"])
    assert len(results) == 1
    assert "茅台" in results[0].content

    # 关键词搜索 - "营收"
    results = await memory.retrieve(keywords=["营收"])
    assert len(results) == 1

    # 关键词搜索 - 标签中的词
    results = await memory.retrieve(keywords=["年报"])
    assert len(results) == 1

    # 关键词搜索 - 无匹配
    results = await memory.retrieve(keywords=["不存在的关键词"])
    assert len(results) == 0


@pytest.mark.asyncio
async def test_shared_memory_decay():
    """测试SharedMemory衰减机制"""
    memory = SharedMemory()

    # 存储一条记忆
    memory_id = await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.MARKET_OBSERVATION,
        content="测试衰减",
        importance=MemoryImportance.LOW,
    )

    # 初始衰减分数应为1.0
    entry = await memory.get(memory_id)
    assert entry is not None
    assert entry.decay_score == 1.0

    # 执行衰减 (0小时 = 所有记忆都会被衰减)
    decayed_count = await memory.decay(hours=0)
    assert decayed_count >= 1

    # 衰减后分数应降低
    entry = await memory.get(memory_id)
    assert entry is not None
    assert entry.decay_score < 1.0

    # 重要记忆衰减更慢
    high_id = await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.ANALYSIS_REPORT,
        content="重要分析报告",
        importance=MemoryImportance.CRITICAL,
    )

    await memory.decay(hours=0)

    low_entry = await memory.get(memory_id)
    high_entry = await memory.get(high_id)
    assert low_entry is not None
    assert high_entry is not None
    # CRITICAL衰减率(0.01)远低于LOW衰减率(0.3)
    assert high_entry.decay_score > low_entry.decay_score


@pytest.mark.asyncio
async def test_shared_memory_stats():
    """测试SharedMemory统计信息"""
    memory = SharedMemory()

    # 空记忆统计
    stats = await memory.get_stats()
    assert stats["total_memories"] == 0
    assert stats["total_entries_ever"] == 0
    assert isinstance(stats["by_type"], dict)
    assert isinstance(stats["by_agent"], dict)

    # 存储一些记忆
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.MARKET_OBSERVATION,
        content="观察1",
    )
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.NEWS_EVENT,
        content="新闻1",
    )
    await memory.store(
        agent_id="agent_b",
        memory_type=MemoryType.MARKET_OBSERVATION,
        content="观察2",
    )

    stats = await memory.get_stats()
    assert stats["total_memories"] == 3
    assert stats["total_entries_ever"] == 3
    assert stats["by_type"]["market_observation"] == 2
    assert stats["by_type"]["news_event"] == 1
    assert stats["by_agent"]["agent_a"] == 2
    assert stats["by_agent"]["agent_b"] == 1


@pytest.mark.asyncio
async def test_shared_memory_persistence_append(tmp_path):
    """回归测试：持久化必须追加而非整文件覆盖

    旧实现 _persist_entry_atomic 使用 os.replace 覆盖整个 jsonl 文件，
    每次写入一条 high/critical 记忆都会丢失该类型之前持久化的所有记忆。
    本测试验证：写入多条后重新加载，所有记忆都在。
    """
    import json as _json

    persist_dir = tmp_path / "memory"

    # 第一次实例：写入 3 条同类型 high/critical 记忆
    memory = SharedMemory(persist_dir=str(persist_dir))
    ids = []
    for i in range(3):
        mid = await memory.store(
            agent_id=f"agent_{i}",
            memory_type=MemoryType.ANALYSIS_REPORT,
            content=f"持久化记忆 {i}",
            importance=MemoryImportance.HIGH,
        )
        ids.append(mid)

    # 持久化文件存在且包含 3 行
    jsonl_files = list(persist_dir.glob("*.jsonl"))
    assert len(jsonl_files) == 1
    with open(jsonl_files[0], encoding="utf-8") as f:
        lines = [line for line in f if line.strip()]
    assert len(lines) == 3, f"持久化文件应包含 3 条记录，实际 {len(lines)} 条"

    # 第二次实例：模拟进程重启后重新加载
    memory2 = SharedMemory(persist_dir=str(persist_dir))
    retrieved = await memory2.retrieve(
        memory_type=MemoryType.ANALYSIS_REPORT,
        limit=100,
    )
    contents = {m.content for m in retrieved}
    assert contents == {"持久化记忆 0", "持久化记忆 1", "持久化记忆 2"}

    # 内存计数一致
    stats = await memory2.get_stats()
    assert stats["total_memories"] == 3


@pytest.mark.asyncio
async def test_shared_memory_persistence_reload_all_types(tmp_path):
    """回归测试：不同 memory_type 持久化到各自文件，重启后全部还原"""
    persist_dir = tmp_path / "memory2"

    memory = SharedMemory(persist_dir=str(persist_dir))
    await memory.store(
        agent_id="agent_a",
        memory_type=MemoryType.RISK_DECISION,
        content="风控决策1",
        importance=MemoryImportance.CRITICAL,
    )
    await memory.store(
        agent_id="agent_b",
        memory_type=MemoryType.EXECUTION_RECORD,
        content="执行记录1",
        importance=MemoryImportance.CRITICAL,
    )

    memory2 = SharedMemory(persist_dir=str(persist_dir))
    stats = await memory2.get_stats()
    assert stats["total_memories"] == 2
    assert stats["by_type"].get("risk_decision") == 1
    assert stats["by_type"].get("execution_record") == 1


# ============================================================
# ToolRegistry 测试
# ============================================================


class DummyTool(BaseTool):
    """用于测试的虚拟工具"""

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="dummy_tool",
            description="一个测试工具",
            category=ToolCategory.UTILITY,
            parameters=[
                ToolParameter("input_text", "string", "输入文本"),
                ToolParameter("count", "integer", "重复次数", required=False, default=1),
            ],
        )

    async def execute(self, **kwargs) -> Any:
        text = kwargs["input_text"]
        count = kwargs.get("count", 1)
        return {"result": text * count}


class RestrictedTool(BaseTool):
    """限制角色的工具"""

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="restricted_tool",
            description="仅限特定角色的工具",
            category=ToolCategory.RISK_MANAGEMENT,
            parameters=[
                ToolParameter("value", "number", "数值"),
            ],
            agent_roles=["risk_manager"],
        )

    async def execute(self, **kwargs) -> Any:
        return {"doubled": kwargs["value"] * 2}


@pytest.mark.asyncio
async def test_tool_registry_register_and_list():
    """测试ToolRegistry注册和列出工具"""
    registry = ToolRegistry()

    # 初始状态为空
    tools = registry.list_tools()
    assert len(tools) == 0

    # 注册工具
    registry.register(DummyTool())
    tools = registry.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "dummy_tool"
    assert tools[0].category == ToolCategory.UTILITY

    # 注册第二个工具
    registry.register(RestrictedTool())
    tools = registry.list_tools()
    assert len(tools) == 2

    # 按分类过滤
    utility_tools = registry.list_tools(category=ToolCategory.UTILITY)
    assert len(utility_tools) == 1
    assert utility_tools[0].name == "dummy_tool"

    risk_tools = registry.list_tools(category=ToolCategory.RISK_MANAGEMENT)
    assert len(risk_tools) == 1
    assert risk_tools[0].name == "restricted_tool"

    # 按角色过滤 - DummyTool的agent_roles=None表示所有角色可用，restricted_tool限risk_manager
    all_for_role = registry.list_tools(agent_role="risk_manager")
    assert len(all_for_role) == 2  # dummy_tool(None=全部可用) + restricted_tool(risk_manager)

    # 注销工具
    assert registry.unregister("dummy_tool") is True
    assert registry.unregister("not_exist") is False
    tools = registry.list_tools()
    assert len(tools) == 1


@pytest.mark.asyncio
async def test_tool_registry_call_tool():
    """测试ToolRegistry调用工具"""
    registry = ToolRegistry()
    registry.register(DummyTool())
    registry.register(RestrictedTool())

    # 正常调用
    result = await registry.call_tool(
        "dummy_tool",
        {"input_text": "hello", "count": 3},
        caller_agent_id="test_agent",
    )
    assert result["success"] is True
    assert result["result"]["result"] == "hellohellohello"

    # 调用不存在的工具
    result = await registry.call_tool(
        "nonexistent_tool",
        {"input_text": "test"},
        caller_agent_id="test_agent",
    )
    assert result["success"] is False
    assert "不存在" in result["error"]

    # 缺少必需参数
    result = await registry.call_tool(
        "dummy_tool",
        {},  # 缺少 input_text
        caller_agent_id="test_agent",
    )
    assert result["success"] is False
    assert "参数验证失败" in result["error"]

    # 角色权限检查 - 无权限的Agent调用restricted_tool
    result = await registry.call_tool(
        "restricted_tool",
        {"value": 10},
        caller_agent_id="market_analyzer",
    )
    assert result["success"] is False
    assert "无权使用" in result["error"]

    # 有权限的Agent调用restricted_tool
    result = await registry.call_tool(
        "restricted_tool",
        {"value": 10},
        caller_agent_id="risk_manager",
    )
    assert result["success"] is True
    assert result["result"]["doubled"] == 20


@pytest.mark.asyncio
async def test_tool_registry_openai_format():
    """测试ToolRegistry OpenAI格式输出"""
    registry = ToolRegistry()
    registry.register(DummyTool())

    openai_tools = registry.get_openai_tools()
    assert len(openai_tools) == 1

    tool_def = openai_tools[0]
    assert tool_def["type"] == "function"
    func = tool_def["function"]
    assert func["name"] == "dummy_tool"
    assert func["description"] == "一个测试工具"
    assert "parameters" in func
    assert func["parameters"]["type"] == "object"
    assert "input_text" in func["parameters"]["properties"]
    assert "input_text" in func["parameters"]["required"]
    # count有默认值，不是必需参数
    assert "count" not in func["parameters"]["required"]


@pytest.mark.asyncio
async def test_tool_registry_stats():
    """测试ToolRegistry统计信息"""
    registry = ToolRegistry()
    registry.register(DummyTool())
    registry.register(RestrictedTool())

    # 初始统计
    stats = registry.get_stats()
    assert stats["total_tools"] == 2
    assert stats["total_calls"] == 0
    assert isinstance(stats["call_counts"], dict)
    assert ToolCategory.UTILITY.value in stats["categories"]
    assert ToolCategory.RISK_MANAGEMENT.value in stats["categories"]

    # 调用工具后统计
    await registry.call_tool("dummy_tool", {"input_text": "test"}, caller_agent_id="agent_a")
    await registry.call_tool("dummy_tool", {"input_text": "test"}, caller_agent_id="agent_b")
    await registry.call_tool("restricted_tool", {"value": 5}, caller_agent_id="risk_manager")

    stats = registry.get_stats()
    assert stats["total_calls"] == 3
    assert stats["call_counts"]["dummy_tool"] == 2
    assert stats["call_counts"]["restricted_tool"] == 1


# ============================================================
# 新Agent创建测试
# ============================================================


@pytest.mark.asyncio
async def test_news_analyst_creation():
    """测试NewsAnalystAgent创建"""
    agent = NewsAnalystAgent(config={"model": "gpt-4o", "api_key": "test-key"})

    # 验证基本属性
    assert agent is not None
    assert agent.config["model"] == "gpt-4o"
    assert agent.config["api_key"] == "test-key"
    assert agent.agent_id.startswith("news_analyst_")
    assert not agent.is_running

    # 验证NewsAnalysisReport可以正常创建
    report = NewsAnalysisReport(
        symbol="600519.SH",
        overall_sentiment="positive",
        sentiment_score=0.5,
        news_count=10,
        summary="茅台利好消息较多",
    )
    assert report.symbol == "600519.SH"
    assert report.overall_sentiment == "positive"
    assert report.sentiment_score == 0.5
    assert report.news_count == 10


@pytest.mark.asyncio
async def test_fundamental_analyst_creation():
    """测试FundamentalAnalystAgent创建"""
    agent = FundamentalAnalystAgent(config={"model": "gpt-4o", "api_key": "test-key"})

    # 验证基本属性
    assert agent is not None
    assert agent.config["model"] == "gpt-4o"
    assert agent.config["api_key"] == "test-key"
    assert agent.agent_id.startswith("fundamental_analyst_")
    assert not agent.is_running

    # 验证FundamentalAnalysisReport可以正常创建
    report = FundamentalAnalysisReport(
        symbol="600519.SH",
        overall_rating="bullish",
        rating_score=0.7,
        summary="茅台基本面优秀",
        recommendation="建议买入",
    )
    assert report.symbol == "600519.SH"
    assert report.overall_rating == "bullish"
    assert report.rating_score == 0.7
    assert report.summary == "茅台基本面优秀"


# ============================================================
# BaseAgent新功能测试
# ============================================================


@pytest.mark.asyncio
async def test_base_agent_with_shared_memory():
    """测试带共享记忆的Agent"""
    memory = SharedMemory()

    agent = DummyAgent(
        role=AgentRole.MARKET_ANALYZER,
        config={},
        shared_memory=memory,
    )

    # 验证共享记忆已绑定
    assert agent.shared_memory is memory

    # 通过Agent存储记忆
    memory_id = await agent.store_memory(
        content="测试记忆内容",
        memory_type=MemoryType.AGENT_THOUGHT,
        importance=MemoryImportance.MEDIUM,
        tags=["test"],
    )
    assert memory_id is not None

    # 通过Agent检索记忆
    memories = await agent.recall_memories(keywords=["测试"])
    assert len(memories) >= 1
    assert memories[0].content == "测试记忆内容"

    # 没有共享记忆时返回None/空列表
    agent_no_mem = DummyAgent(role=AgentRole.RISK_MANAGER, config={})
    assert agent_no_mem.shared_memory is None
    result = await agent_no_mem.store_memory(
        content="测试", memory_type=MemoryType.AGENT_THOUGHT
    )
    assert result is None
    recalled = await agent_no_mem.recall_memories()
    assert recalled == []


@pytest.mark.asyncio
async def test_base_agent_with_tool_registry():
    """测试带工具集的Agent"""
    registry = ToolRegistry()
    registry.register(DummyTool())

    agent = DummyAgent(
        role=AgentRole.MARKET_ANALYZER,
        config={},
        tool_registry=registry,
    )

    # 验证工具集已绑定
    assert agent.tool_registry is registry

    # 获取可用工具列表
    available_tools = agent.get_available_tools()
    assert "dummy_tool" in available_tools

    # 通过Agent调用工具
    result = await agent.use_tool("dummy_tool", input_text="hello", count=2)
    assert result["success"] is True
    assert result["result"]["result"] == "hellohello"

    # 调用不存在的工具
    result = await agent.use_tool("nonexistent", input_text="test")
    assert result["success"] is False

    # 没有工具集时返回错误
    agent_no_tools = DummyAgent(role=AgentRole.RISK_MANAGER, config={})
    assert agent_no_tools.tool_registry is None
    result = await agent_no_tools.use_tool("dummy_tool", input_text="test")
    assert result["success"] is False
    assert "未配置" in result["error"]
    assert agent_no_tools.get_available_tools() == []


@pytest.mark.asyncio
async def test_base_agent_context_prompt():
    """测试BaseAgent上下文prompt生成"""
    memory = SharedMemory()
    registry = ToolRegistry()
    registry.register(DummyTool())

    agent = DummyAgent(
        role=AgentRole.MARKET_ANALYZER,
        config={},
        shared_memory=memory,
        tool_registry=registry,
    )

    # 先存储一些记忆
    await agent.store_memory(
        content="市场处于震荡状态",
        memory_type=MemoryType.MARKET_OBSERVATION,
        tags=["市场"],
    )

    # 生成上下文prompt
    prompt = await agent.get_context_prompt()
    assert prompt != ""
    assert "Agent上下文" in prompt
    assert "你的历史记忆" in prompt
    assert "全局共享记忆" in prompt
    assert "可用工具" in prompt
    assert "dummy_tool" in prompt
    assert "震荡状态" in prompt

    # 不包含某些部分
    prompt_no_tools = await agent.get_context_prompt(include_available_tools=False)
    assert "可用工具" not in prompt_no_tools
    assert "你的历史记忆" in prompt_no_tools

    # 没有共享记忆和工具集时返回空字符串
    bare_agent = DummyAgent(role=AgentRole.RISK_MANAGER, config={})
    prompt_empty = await bare_agent.get_context_prompt()
    assert prompt_empty == ""
