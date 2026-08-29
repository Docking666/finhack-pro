"""
Pydantic数据模型

定义WebUI API的请求/响应数据模型，包括系统信息、配置、回测、Agent、记忆、工具等。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ============================================================
# 通用模型
# ============================================================

class WSMessage(BaseModel):
    """WebSocket消息基类"""
    type: str = Field(..., description="消息类型")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    data: Dict[str, Any] = Field(default_factory=dict)


class APIResponse(BaseModel):
    """统一API响应格式"""
    success: bool = True
    message: str = ""
    data: Any = None


# ============================================================
# 系统管理
# ============================================================

class SystemInfo(BaseModel):
    """系统信息"""
    name: str = "FinHack Pro"
    version: str = "1.0.0"
    mode: str = "backtest"
    status: str = "running"
    uptime_seconds: float = 0
    python_version: str = ""
    agent_count: int = 0
    memory_count: int = 0
    tool_count: int = 0


class HealthStatus(BaseModel):
    """健康检查状态"""
    status: str = "healthy"  # healthy / degraded / unhealthy
    components: Dict[str, str] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# ============================================================
# 配置管理
# ============================================================

class LLMConfigUpdate(BaseModel):
    """LLM配置更新"""
    provider: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    timeout: Optional[int] = Field(None, ge=1, le=600)
    max_retries: Optional[int] = Field(None, ge=0, le=10)


class AgentLLMConfigUpdate(BaseModel):
    """单 Agent LLM 配置覆盖更新（全可选，留空跟随全局）"""
    provider: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    timeout: Optional[int] = Field(None, ge=1, le=600)
    max_retries: Optional[int] = Field(None, ge=0, le=10)


class DataConfigUpdate(BaseModel):
    """数据源配置更新"""
    source: Optional[str] = None
    tushare_token: Optional[str] = None
    cache_dir: Optional[str] = None
    data_dir: Optional[str] = None


class RiskConfigUpdate(BaseModel):
    """风控参数配置更新"""
    max_position_pct: Optional[float] = Field(None, ge=0, le=1)
    max_total_position_pct: Optional[float] = Field(None, ge=0, le=1)
    max_drawdown_pct: Optional[float] = Field(None, ge=0, le=1)
    max_daily_loss_pct: Optional[float] = Field(None, ge=0, le=1)
    var_confidence: Optional[float] = Field(None, ge=0, le=1)
    stop_loss_pct: Optional[float] = Field(None, ge=0, le=1)
    take_profit_pct: Optional[float] = Field(None, ge=0, le=1)


class ExecutionConfigUpdate(BaseModel):
    """执行参数配置更新"""
    slippage: Optional[float] = Field(None, ge=0)
    commission_rate: Optional[float] = Field(None, ge=0)
    stamp_tax_rate: Optional[float] = Field(None, ge=0)


class ConfigUpdate(BaseModel):
    """总配置更新"""
    llm: Optional[LLMConfigUpdate] = None
    data: Optional[DataConfigUpdate] = None
    risk: Optional[RiskConfigUpdate] = None
    execution: Optional[ExecutionConfigUpdate] = None
    # per-Agent LLM 配置覆盖（键为 agent 名，值全可选，留空跟随全局）
    agents: Optional[Dict[str, Optional[AgentLLMConfigUpdate]]] = None


class ConnectionTestRequest(BaseModel):
    """API连接测试请求（协议驱动，provider 仅回显）

    protocol 决定路由（openai / anthropic，缺省按 openai）；
    provider 是自由字符串服务商名（deepseek/orca/zhipu/自定义），仅回显不参与路由。
    """
    protocol: str = Field("openai", description="连接协议: openai / anthropic，未知按 openai")
    provider: str = Field(..., description="服务商名称(仅回显，不参与路由)")
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class ConnectionTestResult(BaseModel):
    """API连接测试结果"""
    provider: str
    success: bool
    message: str
    latency_ms: float = 0


class DataSourceTestRequest(BaseModel):
    """数据源连接测试请求"""
    source: str = Field(..., description="数据源: akshare / tushare")
    tushare_token: Optional[str] = None


class DataSourceTestResult(BaseModel):
    """数据源连接测试结果"""
    source: str
    success: bool
    message: str
    latency_ms: float = 0


# ============================================================
# 回测管理
# ============================================================

class BacktestStrategy(str, Enum):
    """回测策略类型（内置）"""
    DUAL_THRUST = "dual_thrust"
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"


class BacktestRequest(BaseModel):
    """回测请求"""
    strategy: str = BacktestStrategy.DUAL_THRUST.value  # 内置名或自定义策略ID（data/generated_strategies/）
    symbols: List[str] = Field(..., description="标的代码列表")
    start_date: str = Field(..., description="开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期 YYYY-MM-DD")
    initial_capital: float = Field(1_000_000.0, description="初始资金")
    benchmark: str = "000300.SH"
    commission_rate: float = Field(0.0003, description="佣金费率")
    stamp_tax_rate: float = Field(0.001, description="印花税率")
    slippage: float = Field(0.001, description="滑点")
    strategy_params: Optional[Dict[str, Any]] = Field(None, description="策略参数")
    # 信号处理流水线（README：信号聚合器 + 滤波管道）
    strategies: Optional[List[str]] = Field(None, description="多策略组合（非空时启用信号聚合，strategy 字段被忽略）")
    signal_filters: Optional[Dict[str, Any]] = Field(None, description="信号滤波配置（如 {enable_high_cost: false, kama_period: 10}）")
    validator_profile: str = Field("default", description="策略验证档位: default/conservative/aggressive/high_frequency/low_frequency")
    micro_events: Optional[List[Dict[str, Any]]] = Field(None, description="Agent 扫描的微观事件（喂入差异化策略实时信号，如事件驱动/情绪反转/龙虎榜跟随）")


class BacktestStatus(BaseModel):
    """回测任务状态"""
    task_id: str
    status: str = "pending"  # pending / running / completed / failed
    progress: float = 0.0
    current_bar: int = 0
    total_bars: int = 0
    message: str = ""
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class TradeRecord(BaseModel):
    """交易记录"""
    date: str
    symbol: str
    direction: str  # buy / sell
    price: float
    volume: int
    commission: float
    pnl: float = 0.0
    reason: str = ""
    # 交易溯源上下文（阶段2）：成交时快照 {bar_extra, position_volume, signal}，
    # 供前端"详情"展开与权益曲线买卖点弹窗；存量数据无该字段时为 {} 零破坏
    context: Dict[str, Any] = Field(default_factory=dict)


class SweepParam(BaseModel):
    """参数热力图扫描参数定义（阶段5）"""
    name: str = ""
    label: str = ""
    min: float = 0.0
    max: float = 1.0
    step: float = 0.1


class SweepRequest(BaseModel):
    """参数扫描请求（2 参数网格，复用 GridSearchOptimizer）"""
    strategy: str = "dual_thrust"
    symbol: str = "600519.SH"
    start_date: str = "2024-01-01"
    end_date: str = "2024-12-31"
    initial_capital: float = 1_000_000
    metric: str = "sharpe_ratio"
    x_param: SweepParam = Field(default_factory=SweepParam)
    y_param: SweepParam = Field(default_factory=SweepParam)


class SweepCell(BaseModel):
    """热力图单元格"""
    x: float
    y: float
    sharpe: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0


class SweepResult(BaseModel):
    """参数扫描结果"""
    task_id: str
    strategy: str = ""
    symbol: str = ""
    x_param: SweepParam = Field(default_factory=SweepParam)
    y_param: SweepParam = Field(default_factory=SweepParam)
    metric: str = "sharpe_ratio"
    cells: List[SweepCell] = Field(default_factory=list)
    best: Optional[SweepCell] = None
    sampled: bool = False
    total_combos: int = 0
    error: Optional[str] = None


class BacktestMetrics(BaseModel):
    """回测指标"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    final_equity: float = 0.0


class BacktestResult(BaseModel):
    """回测结果"""
    task_id: str
    status: str = "completed"
    metrics: BacktestMetrics = Field(default_factory=BacktestMetrics)
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)
    trades: List[TradeRecord] = Field(default_factory=list)
    benchmark_curve: List[Dict[str, Any]] = Field(default_factory=list)
    daily_returns: List[float] = Field(default_factory=list, description="每日收益率序列（与 equity_curve 对齐）")
    validation: Optional[Dict[str, Any]] = Field(None, description="策略验证报告（StrategyValidator 7 项检查）")
    confidence: Optional[Dict[str, Any]] = Field(None, description="置信度合成（阶段3）：{score, tier, factors} 零 LLM 确定性因子")


# ============================================================
# Agent管理
# ============================================================

class AgentInfo(BaseModel):
    """Agent信息"""
    agent_id: str
    name: str
    role: str
    status: str = "idle"  # running / idle / error
    last_active: Optional[str] = None
    message_count: int = 0
    model: str = ""


class PipelineRunRequest(BaseModel):
    """流水线执行请求"""
    symbol: str = Field(..., description="标的代码")
    market_data: Optional[Dict[str, Any]] = None
    indicators: Optional[Dict[str, Any]] = None
    current_price: Optional[float] = None
    run_id: Optional[str] = Field(None, description="流水线运行ID（复用则续跑；为空生成新ID）")
    resume: bool = Field(True, description="run_id 已存在时是否允许恢复")
    resume_on_drift: bool = Field(False, description="续跑时环境指纹漂移（模型/温度/prompt 变更）是否强制继续")


class PipelineStepResult(BaseModel):
    """流水线步骤结果"""
    step: int
    agent_name: str
    status: str = "pending"
    duration_ms: float = 0
    summary: str = ""


class PipelineRunResult(BaseModel):
    """流水线执行结果"""
    run_id: str
    symbol: str
    status: str = "pending"  # pending / running / completed / failed
    steps: List[PipelineStepResult] = Field(default_factory=list)
    final_signal: Optional[Dict[str, Any]] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    error: Optional[str] = None


# ============================================================
# 共享记忆
# ============================================================

class MemorySearchRequest(BaseModel):
    """记忆搜索请求"""
    memory_type: Optional[str] = None
    agent_id: Optional[str] = None
    keywords: Optional[List[str]] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    importance: Optional[str] = None
    limit: int = Field(50, ge=1, le=200)


class MemoryEntryResponse(BaseModel):
    """记忆条目响应"""
    id: str
    memory_type: str
    agent_id: str
    content: str
    importance: str
    timestamp: str
    tags: List[str] = Field(default_factory=list)
    decay_score: float = 1.0
    access_count: int = 0
    summary: Optional[str] = None


class MemoryStats(BaseModel):
    """记忆统计"""
    total_memories: int = 0
    total_entries_ever: int = 0
    by_type: Dict[str, int] = Field(default_factory=dict)
    by_agent: Dict[str, int] = Field(default_factory=dict)


# ============================================================
# 工具集
# ============================================================

class ToolParameterInfo(BaseModel):
    """工具参数信息"""
    name: str
    type: str
    description: str
    required: bool = True


class ToolInfo(BaseModel):
    """工具信息"""
    name: str
    description: str
    category: str
    parameters: List[ToolParameterInfo] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)


class ToolCallStats(BaseModel):
    """工具调用统计"""
    total_tools: int = 0
    total_calls: int = 0
    call_counts: Dict[str, int] = Field(default_factory=dict)
    categories: List[str] = Field(default_factory=list)
