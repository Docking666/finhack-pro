"""
Agent协调器

管理所有Agent的生命周期，定义Agent间的消息流转:
1. 定时触发市场分析Agent -> 技术面分析报告
2. 新闻社媒分析Agent -> 新闻情感报告
3. 基本面分析Agent -> 基本面分析报告
4. 微观事件Agent -> 微观事件分析报告(新增)
5. 策略生成Agent(多空研究员) -> 综合多方报告+多空辩论 -> 策略信号
6. 风险管理Agent -> 风控决策
7. 交易执行Agent -> 执行报告

所有Agent共享 SharedMemory(共享记忆) 和 ToolRegistry(共享工具集)。
支持微观事件驱动和另类数据分析。

优化:
- Agent启动优雅降级：非核心Agent失败时系统继续运行
- 系统状态追踪：记录各Agent健康状态
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger

# ============================================================
# 各步骤超时配置（秒）—— 防止单步 LLM 调用/网络抖动永久阻塞流水线
# ============================================================
_STEP_TIMEOUTS: Dict[int, int] = {
    1: 120,   # 市场分析：单次 LLM 调用 + 数据准备
    2: 120,   # 新闻社媒：工具调用(akshare) + 单次 LLM
    3: 120,   # 基本面：单次 LLM
    4: 150,   # 微观事件：多次工具调用(龙虎榜/大宗/北向等) + LLM
    5: 600,   # 多空辩论：4 次串行 LLM（多头→空头→裁判→信号），最耗时
    6: 90,    # 风控审批：单次 LLM
    7: 90,    # 交易执行：单次 LLM
}

# 思维链 JSON 泄漏检测模式（LLM reasoning_content 回显工具原始数据时触发）
_JSON_LEAK_RE = re.compile(
    r'"(?:impact_level|url|tags|title|source|publish_time|sentiment|news_id)"\s*:',
    re.IGNORECASE,
)

from finhack_pro.agents.alternative_data_tools import register_alternative_data_tools
from finhack_pro.agents.base import AgentMessage, BaseAgent
from finhack_pro.agents.fundamental_analyst import (
    FundamentalAnalysisReport,
    FundamentalAnalystAgent,
)
from finhack_pro.agents.market_analyzer import MarketAnalysisReport, MarketAnalyzerAgent
from finhack_pro.agents.micro_event_agent import MicroEventAgent, MicroEventReport
from finhack_pro.agents.news_analyst import NewsAnalysisReport, NewsAnalystAgent
from finhack_pro.agents.risk_manager import RiskDecision, RiskManagerAgent
from finhack_pro.agents.shared_memory import SharedMemory
from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent, StrategySignal
from finhack_pro.agents.tool_registry import ToolRegistry, create_default_toolkit
from finhack_pro.agents.trade_executor import ExecutionReport, TradeExecutorAgent
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class PipelineResumeError(RuntimeError):
    """流水线恢复相关错误基类"""


class EnvironmentDriftError(PipelineResumeError):
    """环境指纹漂移：模型/温度/prompt 变更后拒绝恢复"""


class RunIdConflictError(PipelineResumeError):
    """run_id 已存在但 resume=False，拒绝覆盖冻结产物"""


class PipelineBusyError(RuntimeError):
    """已有流水线在运行：并发隔离（同一时间仅允许一个流水线）

    多个流水线并发会共享同一批 Agent/LLMClient 实例，导致流回调互相覆盖、
    思考链事件串 run、LLM 请求互相排队等输出混淆问题，故从源头串行化。
    """


class PipelineCancelledError(RuntimeError):
    """用户主动取消流水线（协作式取消）

    cancel_check 回调在步骤边界被调用，检测到取消标志时抛出，
    由 run_analysis_pipeline 捕获后落盘 cancelled 终态（已 done 步骤保留）。
    """


# 步骤 → (报告名称, Pydantic 模型) 映射，用于 JSON 落盘与恢复重建
_STEP_MODELS = {
    1: ("market_analysis", MarketAnalysisReport),
    2: ("news_analysis", NewsAnalysisReport),
    3: ("fundamental_analysis", FundamentalAnalysisReport),
    4: ("micro_event_analysis", MicroEventReport),
    5: ("strategy_signal", StrategySignal),
    6: ("risk_decision", RiskDecision),
    7: ("execution_report", ExecutionReport),
}


class AgentHealthStatus(str, Enum):
    """Agent健康状态"""
    HEALTHY = "healthy"           # 正常运行
    DEGRADED = "degraded"         # 降级运行（启动失败但非核心）
    FAILED = "failed"             # 失败
    STOPPED = "stopped"           # 已停止


# 核心Agent集合：这些Agent失败会导致系统不可用
CRITICAL_AGENTS: Set[str] = {"strategy_generator", "risk_manager", "trade_executor"}

# 分析Agent集合：这些Agent失败时系统可降级运行
ANALYSIS_AGENTS: Set[str] = {"market_analyzer", "news_analyst", "fundamental_analyst", "micro_event_agent"}


class AgentCoordinator:
    """Agent协调器

    管理所有Agent的生命周期和消息流转，实现多Agent协作决策。
    所有Agent共享同一个 SharedMemory 实例和 ToolRegistry 实例。

    消息流转流程:
    1. 市场分析Agent -> 技术面分析报告
    2. 新闻社媒分析Agent -> 新闻情感报告
    3. 基本面分析Agent -> 基本面分析报告
    4. 策略生成Agent(多空研究员) -> 综合三方报告+多空辩论 -> 策略信号
    5. 风险管理Agent -> 风控决策
    6. 交易执行Agent -> 执行报告

    Usage:
        coordinator = AgentCoordinator(config)
        await coordinator.start()
        result = await coordinator.run_analysis_pipeline("600519.SH", market_data)
        await coordinator.stop()
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """初始化协调器

        创建共享基础设施(SharedMemory、ToolRegistry)和所有Agent实例。
        Agent在 start() 时才会真正初始化和启动。

        Args:
            config: 全局配置字典，包含各Agent、共享记忆、工具集的配置
        """
        self.config = config
        self._agents: Dict[str, BaseAgent] = {}
        self._running = False
        self._analysis_tasks: List[asyncio.Task] = []
        # 流水线并发门禁：同一时间仅允许一个流水线运行（防流回调覆盖/事件串 run）
        self._pipeline_active: bool = False
        self._logger = get_logger("coordinator")
        
        # Agent健康状态追踪
        self._agent_health: Dict[str, AgentHealthStatus] = {}

        # ---- 创建共享基础设施 ----

        # 共享记忆系统
        memory_config = config.get("shared_memory", {})
        self.shared_memory = SharedMemory(
            persist_dir=memory_config.get("persist_dir", "./data/memory"),
            max_short_term=memory_config.get("max_short_term", 1000),
        )
        self._decay_hours = memory_config.get("decay_hours", 24)

        # 共享工具集
        self.tool_registry = create_default_toolkit()
        
        # 注册另类数据工具
        register_alternative_data_tools(self.tool_registry)

        # ---- 创建所有Agent实例 ----
        agent_config = self.config.get("agents", {})
        llm_config = self.config.get("llm", {})
        
        # 调试日志：检查LLM配置是否正确传递
        self._logger.info(f"LLM配置: provider={llm_config.get('provider')}, "
                         f"api_key={'***' + llm_config.get('openai_api_key', '')[-4:] if llm_config.get('openai_api_key') else 'None'}, "
                         f"base_url={llm_config.get('openai_base_url')}, "
                         f"model={llm_config.get('model')}")

        def _merge_config(agent_specific: Dict[str, Any]) -> Dict[str, Any]:
            """合并LLM全局配置和Agent专属配置

            仅覆盖非空字段：per-Agent 配置中 None/空串/空 dict 表示跟随全局。
            """
            # 过滤掉空值字段（None、空串、空 dict、空 list）
            overrides = {
                k: v for k, v in (agent_specific or {}).items()
                if v is not None and v != "" and v != {} and v != []
            }
            merged = {**llm_config, **overrides}
            # 将 openai_api_key 映射为 api_key（Agent统一使用 api_key）
            if "openai_api_key" in merged and "api_key" not in merged:
                merged["api_key"] = merged["openai_api_key"]
            if "openai_base_url" in merged and "base_url" not in merged:
                merged["base_url"] = merged["openai_base_url"]
            return merged

        self._agents["market_analyzer"] = MarketAnalyzerAgent(
            config=_merge_config(agent_config.get("market_analyzer", {})),
            shared_memory=self.shared_memory,
            tool_registry=self.tool_registry,
        )
        self._agents["news_analyst"] = NewsAnalystAgent(
            config=_merge_config(agent_config.get("news_analyst", {})),
            shared_memory=self.shared_memory,
            tool_registry=self.tool_registry,
        )
        self._agents["fundamental_analyst"] = FundamentalAnalystAgent(
            config=_merge_config(agent_config.get("fundamental_analyst", {})),
            shared_memory=self.shared_memory,
            tool_registry=self.tool_registry,
        )
        # 新增: 微观事件Agent
        self._agents["micro_event_agent"] = MicroEventAgent(
            config=_merge_config(agent_config.get("micro_event_agent", {})),
            shared_memory=self.shared_memory,
            tool_registry=self.tool_registry,
        )
        self._agents["strategy_generator"] = StrategyGeneratorAgent(
            config=_merge_config(agent_config.get("strategy_generator", {})),
            shared_memory=self.shared_memory,
            tool_registry=self.tool_registry,
        )
        self._agents["risk_manager"] = RiskManagerAgent(
            config=_merge_config(agent_config.get("risk_manager", {})),
            shared_memory=self.shared_memory,
            tool_registry=self.tool_registry,
        )
        self._agents["trade_executor"] = TradeExecutorAgent(
            config=_merge_config(agent_config.get("trade_executor", {})),
            shared_memory=self.shared_memory,
            tool_registry=self.tool_registry,
        )

        self._logger.info(
            f"协调器初始化完成: {len(self._agents)} 个Agent, "
            f"共享记忆和工具集已就绪(含另类数据工具)"
        )

    # ============================================================
    # Agent属性访问器
    # ============================================================

    @property
    def market_analyzer(self) -> MarketAnalyzerAgent:
        """获取市场分析Agent"""
        return self._agents["market_analyzer"]  # type: ignore

    @property
    def news_analyst(self) -> NewsAnalystAgent:
        """获取新闻社媒分析Agent"""
        return self._agents["news_analyst"]  # type: ignore

    @property
    def fundamental_analyst(self) -> FundamentalAnalystAgent:
        """获取基本面分析Agent"""
        return self._agents["fundamental_analyst"]  # type: ignore

    @property
    def micro_event_agent(self) -> MicroEventAgent:
        """获取微观事件Agent"""
        return self._agents["micro_event_agent"]  # type: ignore

    @property
    def strategy_generator(self) -> StrategyGeneratorAgent:
        """获取策略生成Agent"""
        return self._agents["strategy_generator"]  # type: ignore

    @property
    def risk_manager(self) -> RiskManagerAgent:
        """获取风险管理Agent"""
        return self._agents["risk_manager"]  # type: ignore

    @property
    def trade_executor(self) -> TradeExecutorAgent:
        """获取交易执行Agent"""
        return self._agents["trade_executor"]  # type: ignore

    # ============================================================
    # 生命周期管理
    # ============================================================

    async def start(self) -> None:
        """启动所有Agent

        实现优雅降级：
        - 核心Agent（strategy_generator, risk_manager, trade_executor）失败时抛出异常
        - 分析Agent失败时系统以降级模式继续运行
        """
        self._logger.info("正在启动Agent协调器...")

        # 初始化健康状态
        for name in self._agents:
            self._agent_health[name] = AgentHealthStatus.STOPPED

        failed_critical: List[str] = []
        degraded_agents: List[str] = []

        # 启动所有Agent
        for name, agent in self._agents.items():
            try:
                await agent.start()
                self._agent_health[name] = AgentHealthStatus.HEALTHY
                self._logger.info(f"Agent [{name}] 启动成功")
            except Exception as e:
                self._agent_health[name] = AgentHealthStatus.FAILED
                
                if name in CRITICAL_AGENTS:
                    # 核心Agent失败，记录并继续检查其他Agent
                    self._logger.error(f"核心Agent [{name}] 启动失败: {e}")
                    failed_critical.append(name)
                else:
                    # 非核心Agent失败，标记为降级
                    self._agent_health[name] = AgentHealthStatus.DEGRADED
                    degraded_agents.append(name)
                    self._logger.warning(
                        f"非核心Agent [{name}] 启动失败，系统将以降级模式运行: {e}"
                    )

        # 如果有核心Agent失败，抛出异常
        if failed_critical:
            error_msg = f"核心Agent启动失败，系统不可用: {', '.join(failed_critical)}"
            self._logger.error(error_msg)
            raise RuntimeError(error_msg)

        # 记录降级状态
        if degraded_agents:
            self._logger.warning(
                f"系统以降级模式运行，以下Agent不可用: {', '.join(degraded_agents)}"
            )

        self._running = True
        self._logger.info("Agent协调器启动完成")

    def get_health_status(self) -> Dict[str, Any]:
        """获取系统健康状态
        
        Returns:
            包含整体状态和各Agent状态的字典
        """
        healthy_count = sum(1 for s in self._agent_health.values() if s == AgentHealthStatus.HEALTHY)
        degraded_count = sum(1 for s in self._agent_health.values() if s == AgentHealthStatus.DEGRADED)
        failed_count = sum(1 for s in self._agent_health.values() if s == AgentHealthStatus.FAILED)
        
        # 确定整体状态
        if failed_count > 0 and any(
            self._agent_health.get(name) == AgentHealthStatus.FAILED 
            for name in CRITICAL_AGENTS
        ):
            overall = "critical"
        elif degraded_count > 0:
            overall = "degraded"
        else:
            overall = "healthy"
        
        return {
            "overall": overall,
            "running": self._running,
            "agents": {
                name: status.value 
                for name, status in self._agent_health.items()
            },
            "summary": {
                "healthy": healthy_count,
                "degraded": degraded_count,
                "failed": failed_count,
                "total": len(self._agent_health),
            },
        }

    async def stop(self) -> None:
        """停止所有Agent

        取消所有定时分析任务，然后依次停止每个Agent。
        """
        self._logger.info("正在停止Agent协调器...")

        # 取消所有分析任务
        for task in self._analysis_tasks:
            if not task.done():
                task.cancel()
        self._analysis_tasks.clear()

        # 停止所有Agent
        for name, agent in self._agents.items():
            try:
                await agent.stop()
                self._agent_health[name] = AgentHealthStatus.STOPPED
                self._logger.info(f"Agent [{name}] 已停止")
            except Exception as e:
                self._logger.error(f"Agent [{name}] 停止失败: {e}")

        self._running = False
        self._logger.info("Agent协调器已停止")

    # ============================================================
    # 分析流水线
    # ============================================================

    def _get_pipeline_dir(self, run_id: str) -> str:
        """获取流水线产物目录（报告 md 落盘）"""
        base = self.config.get("pipeline", {}).get("output_dir", "data/pipeline")
        run_dir = os.path.join(base, run_id)
        os.makedirs(run_dir, exist_ok=True)
        return run_dir

    def _write_report_md(self, run_id: str, step: int, name: str, report: Any) -> str:
        """将报告落盘为 Markdown 文件

        三层上下文架构的第二层：完整报告写文件，供跨模型读取、人工审阅、
        崩溃恢复。返回文件路径，调用方可将其写入 SharedMemory 引用。

        Args:
            run_id: 流水线运行ID
            step: 步骤号
            name: 报告名称(如 market_analysis)
            report: 报告对象(Pydantic模型)或 dict

        Returns:
            md 文件绝对路径；写入失败返回空串
        """
        try:
            run_dir = self._get_pipeline_dir(run_id)
            path = os.path.join(run_dir, f"step{step}_{name}.md")

            # 转 dict
            data = report.model_dump() if hasattr(report, "model_dump") else (
                dict(report) if isinstance(report, dict) else {"value": str(report)}
            )

            # 渲染 Markdown
            lines: List[str] = [f"# {name}", ""]
            for key, value in data.items():
                lines.append(f"## {key}")
                lines.append("")
                if isinstance(value, (dict, list)):
                    import json as _json
                    rendered = _json.dumps(value, ensure_ascii=False, indent=2, default=str)
                    lines.append(f"```json\n{rendered}\n```")
                else:
                    lines.append(str(value))
                lines.append("")

            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return path
        except Exception as e:
            self._logger.warning(f"[Pipeline {run_id}] 报告落盘失败 ({name}): {e}")
            return ""

    # ============================================================
    # 断点恢复辅助方法（步骤级）
    # ============================================================

    def _atomic_write_json(self, path: str, data: Any) -> None:
        """原子写 JSON：tempfile 同目录临时文件 + os.replace"""
        import json as _json
        import tempfile

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    def _write_report_json(self, run_id: str, step: int, name: str, report: Any) -> str:
        """写 step{N}_{name}.json（可重建 Pydantic 报告），返回路径；失败返回 ''"""
        try:
            run_dir = self._get_pipeline_dir(run_id)
            path = os.path.join(run_dir, f"step{step}_{name}.json")
            data = report.model_dump() if hasattr(report, "model_dump") else (
                dict(report) if isinstance(report, dict) else {"value": str(report)}
            )
            self._atomic_write_json(path, data)
            return path
        except Exception as e:
            self._logger.warning(f"[Pipeline {run_id}] 报告 JSON 落盘失败 ({name}): {e}")
            return ""

    def _save_input_snapshot(
        self,
        run_id: str,
        symbol: str,
        market_data: Optional[Dict[str, Any]],
        indicators: Optional[Dict[str, Any]],
        current_price: Optional[float],
    ) -> None:
        """写 input_snapshot.json（point-in-time 数据快照，恢复时复用，禁止重拉）"""
        run_dir = self._get_pipeline_dir(run_id)
        self._atomic_write_json(os.path.join(run_dir, "input_snapshot.json"), {
            "symbol": symbol,
            "market_data": market_data,
            "indicators": indicators,
            "current_price": current_price,
            "created_at": time.time(),
        })

    def _load_input_snapshot(self, run_id: str) -> Optional[Dict[str, Any]]:
        """读 input_snapshot.json；不存在返回 None"""
        import json as _json

        run_dir = self._get_pipeline_dir(run_id)
        path = os.path.join(run_dir, "input_snapshot.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception as e:
            self._logger.warning(f"[Pipeline {run_id}] 读取输入快照失败: {e}")
            return None

    def _is_step_done(self, run_id: str, step: int) -> bool:
        """step{N}.done 标记是否存在"""
        run_dir = self._get_pipeline_dir(run_id)
        return os.path.exists(os.path.join(run_dir, f"step{step}.done"))

    def _mark_step_done(self, run_id: str, step: int, name: str) -> None:
        """原子写 step{step}.done（提交点：JSON+MD 已写入后才标记）"""
        run_dir = self._get_pipeline_dir(run_id)
        self._atomic_write_json(os.path.join(run_dir, f"step{step}.done"), {
            "step": step,
            "name": name,
            "run_id": run_id,
            "completed_at": time.time(),
        })

    def _load_step_report(self, run_id: str, step: int) -> Optional[Any]:
        """从 step{N}_{name}.json 重建 Pydantic 报告对象"""
        import json as _json

        name, model = _STEP_MODELS.get(step, (None, None))
        if not name or not model:
            return None
        run_dir = self._get_pipeline_dir(run_id)
        path = os.path.join(run_dir, f"step{step}_{name}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            return model.model_validate(data)
        except Exception as e:
            self._logger.warning(f"[Pipeline {run_id}] 重建 Step {step} 报告失败: {e}")
            return None

    def _save_pipeline_state(self, run_id: str, status: str, terminal: Optional[str] = None) -> None:
        """写 pipeline_state.json（记录终态：hold/risk_rejected/executed）"""
        run_dir = self._get_pipeline_dir(run_id)
        self._atomic_write_json(os.path.join(run_dir, "pipeline_state.json"), {
            "status": status,
            "terminal": terminal,
            "updated_at": time.time(),
        })

    def _load_pipeline_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        """读 pipeline_state.json"""
        import json as _json

        run_dir = self._get_pipeline_dir(run_id)
        path = os.path.join(run_dir, "pipeline_state.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception as e:
            self._logger.warning(f"[Pipeline {run_id}] 读取流水线状态失败: {e}")
            return None

    def _compute_env_fingerprint(self) -> Dict[str, Any]:
        """收集每个 Agent 的环境指纹（模型/温度/provider/base_url + prompt sha256）

        Returns:
            {"version": 1, "agents": {...}, "prompts": {...}}
        """
        import hashlib as _hashlib

        agents_fp: Dict[str, Dict[str, Any]] = {}
        prompts_fp: Dict[str, str] = {}

        for name, agent in self._agents.items():
            cfg = agent.config
            agents_fp[name] = {
                "model": cfg.get("model", ""),
                "temperature": cfg.get("temperature", 0.3),
                "provider": cfg.get("provider", ""),
                "base_url": cfg.get("base_url") or cfg.get("openai_base_url", ""),
            }
            # 各 agent 的 system prompt（模块级常量）hash
            mod = getattr(agent, "__class__", None)
            if mod is not None:
                mod = mod.__module__
            prompts_fp[name] = ""

        # 从模块级常量收集 prompt（尽力而为，缺失不影响核心指纹）
        try:
            from finhack_pro.agents import fundamental_analyst as _fa
            from finhack_pro.agents import market_analyzer as _ma
            from finhack_pro.agents import micro_event_agent as _me
            from finhack_pro.agents import news_analyst as _na
            from finhack_pro.agents import strategy_generator as _sg

            prompt_map = {
                "market_analyzer": getattr(_ma, "MARKET_ANALYZER_SYSTEM_PROMPT", ""),
                "news_analyst": getattr(_na, "NEWS_ANALYST_SYSTEM_PROMPT", ""),
                "fundamental_analyst": getattr(_fa, "FUNDAMENTAL_ANALYST_SYSTEM_PROMPT", ""),
                "micro_event_agent": getattr(_me, "MICRO_EVENT_SYSTEM_PROMPT", ""),
                "strategy_generator_bull": getattr(_sg, "BULL_ANALYST_SYSTEM_PROMPT", ""),
                "strategy_generator_bear": getattr(_sg, "BEAR_ANALYST_SYSTEM_PROMPT", ""),
                "strategy_generator_judge": getattr(_sg, "DEBATE_JUDGE_SYSTEM_PROMPT", ""),
                "strategy_generator_final": getattr(_sg, "STRATEGY_GENERATOR_SYSTEM_PROMPT", ""),
            }
            prompts_fp = {k: _hashlib.sha256(v.encode()).hexdigest() for k, v in prompt_map.items()}
        except Exception as e:
            self._logger.warning(f"收集 prompt 指纹失败: {e}")

        return {"version": 1, "agents": agents_fp, "prompts": prompts_fp}

    def _save_env_fingerprint(self, run_id: str) -> None:
        """写 env_fingerprint.json"""
        run_dir = self._get_pipeline_dir(run_id)
        fp = self._compute_env_fingerprint()
        fp["created_at"] = time.time()
        self._atomic_write_json(os.path.join(run_dir, "env_fingerprint.json"), fp)

    def _check_env_fingerprint(self, run_id: str) -> bool:
        """与已存指纹比对（忽略 created_at），一致返回 True"""
        import json as _json

        run_dir = self._get_pipeline_dir(run_id)
        path = os.path.join(run_dir, "env_fingerprint.json")
        if not os.path.exists(path):
            return True  # 无指纹可查 → 视为一致
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = _json.load(f)
            current = self._compute_env_fingerprint()
            # 仅比较 agents 与 prompts，忽略 created_at/version 差异
            return saved.get("agents") == current.get("agents") and \
                saved.get("prompts") == current.get("prompts")
        except Exception as e:
            self._logger.warning(f"[Pipeline {run_id}] 环境指纹校验失败: {e}")
            return False

    def _get_resume_plan(self, run_id: str) -> Dict[str, Any]:
        """扫描 run 目录，返回 {input_snapshot, done_steps, pending_steps, state}"""
        run_dir = self._get_pipeline_dir(run_id)
        done_steps: Set[int] = {s for s in _STEP_MODELS if self._is_step_done(run_id, s)}
        return {
            "run_dir": run_dir,
            "input_snapshot": self._load_input_snapshot(run_id),
            "done_steps": done_steps,
            "pending_steps": sorted(set(_STEP_MODELS.keys()) - done_steps),
            "state": self._load_pipeline_state(run_id),
        }

    async def _run_step(
        self,
        run_id: str,
        step: int,
        name: str,
        runner: Any,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> Any:
        """执行单个步骤（原子单元）

        已 done → 从 JSON 重建返回；否则执行 runner()，成功后写
        json → md → done（done 是提交点）。步内异常上抛，不写 done。

        加 asyncio.wait_for 超时保护（见 _STEP_TIMEOUTS），防止 LLM 长思考/
        网络抖动/重试循环导致整条流水线永久阻塞。

        cancel_check: 协作式取消回调（每次步骤边界调用一次）。已取消 →
        抛 PipelineCancelledError（步骤内 LLM 调用不中断，粒度=步骤）。
        """
        if cancel_check is not None:
            cancel_check()

        if self._is_step_done(run_id, step):
            report = self._load_step_report(run_id, step)
            if report is not None:
                self._logger.info(f"[Pipeline {run_id}] 跳过已完成 Step {step}（恢复）")
                return report
            self._logger.warning(f"[Pipeline {run_id}] Step {step} done 标记存在但 JSON 损坏，整步重跑")

        timeout = _STEP_TIMEOUTS.get(step, 120)
        try:
            report = await asyncio.wait_for(runner(), timeout=timeout)
        except asyncio.TimeoutError:
            self._logger.error(
                f"[Pipeline {run_id}] Step {step} ({name}) 超时 ({timeout}s)，"
                f"可能原因：LLM 长思考 / 网络延迟 / 推理模型 reasoning_content 过长"
            )
            raise
        if report is not None:
            self._write_report_json(run_id, step, name, report)
            self._write_report_md(run_id, step, name, report)
            self._mark_step_done(run_id, step, name)
            self._logger.info(f"[Pipeline {run_id}] Step {step} 完成并落盘 ({name})")
        return report

    def _rebuild_completed_result(self, run_id: str, symbol: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """终态恢复：从 JSON 重建完整 result（已全部完成的 run 直接返回）"""
        result: Dict[str, Any] = {
            "symbol": symbol,
            "run_id": run_id,
            "report_dir": self._get_pipeline_dir(run_id),
            "resumed_from_checkpoint": True,
        }
        terminal = state.get("terminal")

        report1 = self._load_step_report(run_id, 1)
        report2 = self._load_step_report(run_id, 2)
        report3 = self._load_step_report(run_id, 3)
        report4 = self._load_step_report(run_id, 4)
        report5 = self._load_step_report(run_id, 5)
        report6 = self._load_step_report(run_id, 6)
        report7 = self._load_step_report(run_id, 7)

        if report1:
            result["analysis"] = report1.model_dump()
            p = os.path.join(self._get_pipeline_dir(run_id), "step1_market_analysis.md")
            if os.path.exists(p):
                result["analysis_report_path"] = p
        if report2:
            result["news_analysis"] = report2.model_dump()
            p = os.path.join(self._get_pipeline_dir(run_id), "step2_news_analysis.md")
            if os.path.exists(p):
                result["news_report_path"] = p
        if report3:
            result["fundamental_analysis"] = report3.model_dump()
            p = os.path.join(self._get_pipeline_dir(run_id), "step3_fundamental_analysis.md")
            if os.path.exists(p):
                result["fundamental_report_path"] = p
        if report4:
            result["micro_event_analysis"] = report4.model_dump()
            p = os.path.join(self._get_pipeline_dir(run_id), "step4_micro_event_analysis.md")
            if os.path.exists(p):
                result["micro_event_report_path"] = p
        if report5:
            result["signal"] = report5.model_dump()
        if report6:
            result["risk_decision"] = report6.model_dump()
        if report7:
            result["execution"] = report7.model_dump()

        if terminal == "hold":
            result.setdefault("risk_decision", None)
            result.setdefault("execution", None)
        elif terminal == "risk_rejected":
            result.setdefault("execution", None)

        return result

    async def _run_analysis_pipeline_impl(
        self,
        symbol: str,
        market_data: Optional[Dict[str, Any]] = None,
        indicators: Optional[Dict[str, Any]] = None,
        current_price: Optional[float] = None,
        run_id: Optional[str] = None,
        resume: bool = True,
        event_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """运行完整的分析流水线

        新的7步流水线:
        1. 市场分析Agent -> 技术面分析报告
        2. 新闻社媒分析Agent -> 新闻情感报告
        3. 基本面分析Agent -> 基本面分析报告
        4. 微观事件Agent -> 微观事件分析报告(新增)
        5. 策略生成Agent(多空研究员) -> 综合多方报告+多空辩论 -> 策略信号
        6. 风险管理Agent -> 风控决策
        7. 交易执行Agent -> 执行报告

        每一步的输出都存储到共享记忆中，并落盘为 JSON/MD（断点恢复）。

        Args:
            symbol: 标的代码
            market_data: 市场数据
            indicators: 技术指标
            current_price: 当前价格
            run_id: 显式运行ID。为 None 生成新 run（旧行为）；
                已存在目录 + resume=True + 环境指纹一致 → 恢复（跳过已完成步骤）
            resume: run_id 已存在时是否允许恢复。False 且 run_id 已存在 → RunIdConflictError
            event_callback: 可选实时事件回调（async (event: Dict) -> None）。
                流水线每步开始/完成时触发，用于 WebUI 实时展示思考链（agent_thinking/agent_thought）。

        Returns:
            包含各阶段结果的字典

        Raises:
            EnvironmentDriftError: 环境指纹漂移且不允许恢复
            RunIdConflictError: run_id 已存在且 resume=False
            PipelineResumeError: 快照 symbol 不一致等恢复错误
        """
        self._logger.info(f"========== 开始分析流水线: {symbol} ==========")
        result: Dict[str, Any] = {"symbol": symbol}

        # ---- run_id 解析与断点恢复 ----
        if run_id is None:
            # 新 run（沿用 md5 生成，向后兼容）
            run_id = hashlib.md5(f"{symbol}:{time.time()}".encode()).hexdigest()[:12]
            is_resume = False
        else:
            run_dir = self._get_pipeline_dir(run_id)
            run_exists = os.path.isdir(run_dir) and os.listdir(run_dir) != []
            if run_exists:
                if not resume:
                    raise RunIdConflictError(
                        f"run_id '{run_id}' 已存在且 resume=False，拒绝覆盖冻结产物；"
                        f"如需续跑请传 resume=True"
                    )
                # 环境指纹校验：漂移则拒绝恢复（除非配置 resume_on_drift）
                if not self._check_env_fingerprint(run_id):
                    resume_on_drift = self.config.get("pipeline", {}).get("resume_on_drift", False)
                    if not resume_on_drift:
                        raise EnvironmentDriftError(
                            f"run_id '{run_id}' 环境指纹漂移（模型/温度/prompt 变更），"
                            f"无法安全续跑；请改用新 run_id 或设置 pipeline.resume_on_drift=true"
                        )
                    self._logger.warning(f"[Pipeline {run_id}] 环境指纹漂移，resume_on_drift=true，降级继续")
                    result["resume_drift_warning"] = "环境指纹漂移，已按 resume_on_drift 配置继续"
                is_resume = True
            else:
                is_resume = False

        result["run_id"] = run_id
        result["report_dir"] = self._get_pipeline_dir(run_id)

        # ---- 终态恢复：若已全部完成，直接重建结果返回 ----
        state = self._load_pipeline_state(run_id)
        if state and state.get("status") == "completed" and state.get("terminal"):
            self._logger.info(f"[Pipeline {run_id}] 流水线已完成 (terminal={state.get('terminal')})，直接返回重建结果")
            return self._rebuild_completed_result(run_id, symbol, state)

        # ---- 新 run：写输入快照 + 环境指纹 ----
        if not is_resume:
            self._save_input_snapshot(run_id, symbol, market_data, indicators, current_price)
            self._save_env_fingerprint(run_id)
            self._save_pipeline_state(run_id, "running")
        else:
            # 恢复：复用快照数据（point-in-time，禁止重拉），校验 symbol
            snapshot = self._load_input_snapshot(run_id)
            if snapshot is not None:
                snap_symbol = snapshot.get("symbol")
                if snap_symbol != symbol:
                    raise PipelineResumeError(
                        f"run_id '{run_id}' 快照标的 '{snap_symbol}' 与请求 '{symbol}' 不一致，拒绝恢复"
                    )
                market_data = snapshot.get("market_data")
                indicators = snapshot.get("indicators")
                current_price = snapshot.get("current_price")
                self._logger.info(f"[Pipeline {run_id}] 恢复：复用输入快照（point-in-time）")

        try:
            # ---- 实时事件推送 helper（思考链：每步开始/完成触发）----
            async def _emit(event: Dict[str, Any]) -> None:
                if event_callback is None:
                    return
                try:
                    await event_callback(event)
                except Exception:
                    self._logger.warning("事件回调推送失败", exc_info=True)

            # ---- 注入 LLM 流式/推理回调：实时把模型思考推给前端 ----
            # 方案A：思维链 Prompt 产出的正文 token（流式）实时可见
            # 方案B：DeepSeek reasoner 等模型的 reasoning_content 原生推理文本
            # 同步回调（LLM token 到达）→ 节流后经事件循环转异步推送，避免 task 风暴
            _thinking_buf: Dict[int, List[str]] = {}
            _last_emit_ts = {"t": 0.0}
            _loop = asyncio.get_running_loop()

            def _push_thinking(step: int, agent_id: str, agent_name: str) -> None:
                buf = _thinking_buf.get(step)
                if not buf:
                    return
                text = "".join(buf)
                _thinking_buf[step] = []
                if not text.strip():
                    return

                # ---- 思维链 JSON 泄漏检测与净化 ----
                # 某些推理模型（DeepSeek R1 等）会在 reasoning_content 中回显输入上下文，
                # 导致 search_news 等工具的原始 JSON 被推送到前端"思考过程"区域。
                # 检测特征：连续 ≥3 个工具字段键（impact_level/url/tags/title/source 等）
                if _JSON_LEAK_RE.search(text) and text.count('":') >= 3:
                    # 提取记录条数用于友好提示
                    count = text.count('"title"') or text.count("title")
                    text = f"[数据分析中，共加载 {max(count, 1)} 条记录]"

                if event_callback is None:
                    return
                try:
                    _loop.create_task(event_callback({
                        "type": "agent_thinking",
                        "run_id": run_id,
                        "step": step,
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "thinking": text,
                        "content": f"## {agent_name}\n\n{text}",
                    }))
                except Exception:
                    self._logger.warning("思考流推送失败", exc_info=True)

            def _make_llm_cb(step: int, agent_id: str, agent_name: str):
                def _on_piece(piece: str) -> None:
                    buf = _thinking_buf.setdefault(step, [])
                    buf.append(piece)
                    now = time.monotonic()
                    joined_len = sum(len(p) for p in buf)
                    # 节流：>400ms 或累积 >400 字符推送一次
                    if now - _last_emit_ts["t"] > 0.4 or joined_len > 400:
                        _last_emit_ts["t"] = now
                        _push_thinking(step, agent_id, agent_name)
                return _on_piece

            def _flush_step_reasoning(step: int) -> str:
                """步骤完成时取出该步骤累积的完整推理文本（供 agent_thought 携带）"""
                buf = _thinking_buf.pop(step, None)
                return "".join(buf) if buf else ""

            def _summarize_phase1_report(agent_name: str, report: Any, sym: str) -> str:
                """把各子智能体的分析报告转成真实结果摘要（非占位文案）"""
                try:
                    if agent_name == "市场分析(技术面)":
                        return (f"状态={report.market_state.value}, "
                                f"趋势={report.trend_direction.value}, "
                                f"置信度={report.confidence:.2f}")
                    if agent_name == "新闻社媒分析":
                        return (f"总体情感={report.overall_sentiment}, "
                                f"情绪分数={report.sentiment_score:.2f}")
                    if agent_name == "基本面分析":
                        return (f"投资评级={report.overall_rating}, "
                                f"评级分数={report.rating_score:.2f}")
                    if agent_name == "微观事件分析":
                        return (f"发现 {report.events_count} 个事件, "
                                f"情绪变化={report.sentiment_shift}")
                except Exception:
                    self._logger.warning("分析报告摘要生成失败，降级为原始字段", exc_info=True)
                return str(report)[:200]

            _AGENT_STEP_META = {
                "market_analyzer": (1, "market_analyzer", "市场分析(技术面)"),
                "news_analyst": (2, "news_analyst", "新闻社媒分析"),
                "fundamental_analyst": (3, "fundamental_analyst", "基本面分析"),
                "micro_event_agent": (4, "micro_event_agent", "微观事件分析"),
                "strategy_generator": (5, "strategy_generator", "策略生成(多空辩论)"),
                "risk_manager": (6, "risk_manager", "风控审批"),
            }
            # 保存各 agent 原有的流回调，结束恢复（防并发覆盖/泄漏到后续调用）
            _saved_stream_callbacks: Dict[str, tuple] = {}
            for _aname, _agent in self._agents.items():
                if hasattr(_agent, "get_llm_stream_callbacks"):
                    _saved_stream_callbacks[_aname] = _agent.get_llm_stream_callbacks()
            for _aname, _agent in self._agents.items():
                _meta = _AGENT_STEP_META.get(_aname)
                if not _meta:
                    continue
                _step, _aid, _an = _meta
                if hasattr(_agent, "set_llm_stream_callbacks"):
                    _agent.set_llm_stream_callbacks(
                        on_token=_make_llm_cb(_step, _aid, _an),
                        on_reasoning=_make_llm_cb(_step, _aid, _an),
                    )
                # 注入子步骤进度回调（Agent 内部 emit_progress → agent_thinking 事件）
                if hasattr(_agent, "set_progress_callback"):
                    def _make_progress_cb(step_i: int, aid_i: str, an_i: str):
                        def _cb(msg: str) -> None:
                            try:
                                _loop.create_task(event_callback({
                                    "type": "agent_thinking",
                                    "run_id": run_id,
                                    "step": step_i,
                                    "agent_id": aid_i,
                                    "agent_name": an_i,
                                    "thinking": msg,
                                    "content": f"## {an_i}\n\n{msg}",
                                }))
                            except Exception:
                                pass
                        return _cb
                    _agent.set_progress_callback(_make_progress_cb(_step, _aid, _an))

            # ---- 断点恢复透明化：已完成的步骤按顺序推送"已恢复"事件 ----
            # 避免 resume 时 Step1-4 静默跳过导致前端"直接跳到多空辩论"的观感
            if is_resume:
                _RESUME_STEP_META = {
                    1: ("market_analyzer", "市场分析(技术面)"),
                    2: ("news_analyst", "新闻社媒分析"),
                    3: ("fundamental_analyst", "基本面分析"),
                    4: ("micro_event_agent", "微观事件分析"),
                    5: ("strategy_generator", "策略生成(多空辩论)"),
                    6: ("risk_manager", "风控审批"),
                    7: ("trade_executor", "交易执行"),
                }
                for _s in sorted(_RESUME_STEP_META):
                    if not self._is_step_done(run_id, _s):
                        continue
                    _aid, _an = _RESUME_STEP_META[_s]
                    await _emit({
                        "type": "agent_thought",
                        "run_id": run_id,
                        "step": _s,
                        "agent_id": _aid,
                        "agent_name": _an,
                        "content": "已从断点恢复（历史结果已落盘，跳过重跑）",
                        "duration_ms": 0,
                    })

            # ---- 第1阶段: 并行执行 Step 1-4 (市场/新闻/基本面/微观事件) ----
            self._logger.info("[Phase 1] 并行执行市场分析、新闻分析、基本面分析、微观事件分析...")

            async def _run_market_analysis():
                report = await self.market_analyzer.analyze(
                    symbol=symbol,
                    market_data=market_data,
                    indicators=indicators,
                )
                await self.shared_memory.store(
                    agent_id=self.market_analyzer.agent_id,
                    memory_type=self.shared_memory.MemoryType.ANALYSIS_REPORT,
                    content=f"{symbol} 技术面分析: 趋势={report.trend_direction.value}, "
                            f"状态={report.market_state.value}",
                    structured_data=report.model_dump(),
                    importance=self.shared_memory.MemoryImportance.HIGH,
                    tags=[symbol, "technical", "analysis_report"],
                )
                return report

            async def _run_news_analysis():
                report = await self.news_analyst.analyze(symbol=symbol)
                await self.shared_memory.store(
                    agent_id=self.news_analyst.agent_id,
                    memory_type=self.shared_memory.MemoryType.NEWS_EVENT,
                    content=f"{symbol} 新闻分析: 情感={report.overall_sentiment}, "
                            f"分数={report.sentiment_score:.2f}",
                    structured_data=report.model_dump(),
                    importance=self.shared_memory.MemoryImportance.HIGH,
                    tags=[symbol, "news", "sentiment"],
                )
                return report

            async def _run_fundamental_analysis():
                report = await self.fundamental_analyst.analyze(symbol=symbol)
                await self.shared_memory.store(
                    agent_id=self.fundamental_analyst.agent_id,
                    memory_type=self.shared_memory.MemoryType.ANALYSIS_REPORT,
                    content=f"{symbol} 基本面分析: 评级={report.overall_rating}, "
                            f"分数={report.rating_score:.2f}",
                    structured_data=report.model_dump(),
                    importance=self.shared_memory.MemoryImportance.HIGH,
                    tags=[symbol, "fundamental", "analysis_report"],
                )
                return report

            async def _run_micro_event_analysis():
                report = await self.micro_event_agent.scan_events(
                    symbol=symbol, days=7,
                )
                await self.shared_memory.store(
                    agent_id=self.micro_event_agent.agent_id,
                    memory_type=self.shared_memory.MemoryType.MICRO_EVENT,
                    content=f"{symbol} 微观事件分析: 发现{report.events_count}个事件, "
                            f"情绪变化={report.sentiment_shift}",
                    structured_data=report.model_dump(),
                    importance=self.shared_memory.MemoryImportance.HIGH,
                    tags=[symbol, "micro_event", "alternative_data"],
                )
                return report

            # 并行执行4个分析任务（仅未完成步骤；已完成步骤恢复时从 JSON 重建）
            async def _run_market_analysis():
                return await self._run_step(run_id, 1, "market_analysis", lambda: self.market_analyzer.analyze(
                    symbol=symbol, market_data=market_data, indicators=indicators,
                ), cancel_check=cancel_check)

            async def _run_news_analysis():
                return await self._run_step(run_id, 2, "news_analysis", lambda: self.news_analyst.analyze(symbol=symbol), cancel_check=cancel_check)

            async def _run_fundamental_analysis():
                return await self._run_step(run_id, 3, "fundamental_analysis", lambda: self.fundamental_analyst.analyze(symbol=symbol), cancel_check=cancel_check)

            async def _run_micro_event_analysis():
                return await self._run_step(run_id, 4, "micro_event_analysis", lambda: self.micro_event_agent.scan_events(symbol=symbol, days=7), cancel_check=cancel_check)

            # 只对未完成步骤创建 task（断点恢复核心：跳过已 done 步骤）
            _PENDING = [s for s in (1, 2, 3, 4) if not self._is_step_done(run_id, s)]
            _STEP_RUNNERS = {
                1: ("market", _run_market_analysis),
                2: ("news", _run_news_analysis),
                3: ("fundamental", _run_fundamental_analysis),
                4: ("micro_event", _run_micro_event_analysis),
            }
            analysis_tasks = [
                asyncio.create_task(_STEP_RUNNERS[s][1](), name=_STEP_RUNNERS[s][0])
                for s in _PENDING
            ]

            # 收集结果，记录失败任务（失败不吞异常，真实传播）
            analysis_results = {}
            step_errors: Dict[str, str] = {}
            _PHASE1_META = {
                "market": (1, "market_analyzer", "市场分析(技术面)"),
                "news": (2, "news_analyst", "新闻社媒分析"),
                "fundamental": (3, "fundamental_analyst", "基本面分析"),
                "micro_event": (4, "micro_event_agent", "微观事件分析"),
            }
            for task in analysis_tasks:
                task_name = task.get_name()
                try:
                    report = await task
                    analysis_results[task_name] = report
                    # 实时推送：该分析步骤完成（展示各子智能体的真实分析结果摘要，
                    # 而非"XX 分析完成"占位；推理过程见 reasoning 字段）
                    _meta = _PHASE1_META.get(task_name)
                    if _meta and report is not None:
                        step_num, agent_id, agent_name = _meta
                        _reasoning = _flush_step_reasoning(step_num)
                        await _emit({
                            "type": "agent_thought",
                            "run_id": run_id,
                            "step": step_num,
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "reasoning": _reasoning,
                            "content": _summarize_phase1_report(agent_name, report, symbol),
                        })
                except Exception as e:
                    self._logger.error(f"分析任务 [{task_name}] 失败: {e}")
                    analysis_results[task_name] = None
                    step_errors[task_name] = str(e)

            # Phase1 任一分析步骤失败 → 流水线整体失败并终止（决策#1：失败即终止）
            if step_errors:
                error_msg = "分析步骤失败: " + "; ".join(step_errors.values())
                self._logger.error(f"[Pipeline {run_id}] {error_msg}")
                result["error"] = error_msg
                result["step_errors"] = step_errors
                self._save_pipeline_state(run_id, "failed")
                return result

            # 已完成步骤（恢复）从 JSON 重建，未完成的用本次结果
            analysis_report = analysis_results.get("market")
            news_report = analysis_results.get("news")
            fundamental_report = analysis_results.get("fundamental")
            micro_event_report = analysis_results.get("micro_event")

            # 恢复场景：已 done 的步骤用 JSON 重建报告（不重复调用 LLM）
            def _resolved(name_key, step):
                if name_key in analysis_results and analysis_results[name_key] is not None:
                    return analysis_results[name_key]
                if self._is_step_done(run_id, step):
                    return self._load_step_report(run_id, step)
                return None

            analysis_report = _resolved("market", 1)
            news_report = _resolved("news", 2)
            fundamental_report = _resolved("fundamental", 3)
            micro_event_report = _resolved("micro_event", 4)

            # 记录结果（md/json/done 已由 _run_step 落盘）
            if analysis_report:
                result["analysis"] = analysis_report.model_dump()
                report_path = self._get_pipeline_dir(run_id) + os.sep + "step1_market_analysis.md"
                if os.path.exists(report_path):
                    result["analysis_report_path"] = report_path
                self._logger.info(
                    f"[Step 1/7] 市场分析完成: 状态={analysis_report.market_state.value}, "
                    f"趋势={analysis_report.trend_direction.value}"
                )
            else:
                self._logger.warning("[Step 1/7] 市场分析失败，使用空报告")
                result["analysis"] = None

            if news_report:
                result["news_analysis"] = news_report.model_dump()
                report_path = self._get_pipeline_dir(run_id) + os.sep + "step2_news_analysis.md"
                if os.path.exists(report_path):
                    result["news_report_path"] = report_path
                self._logger.info(
                    f"[Step 2/7] 新闻分析完成: 情感={news_report.overall_sentiment}, "
                    f"分数={news_report.sentiment_score:.2f}"
                )
            else:
                self._logger.warning("[Step 2/7] 新闻分析失败，使用空报告")
                result["news_analysis"] = None

            if fundamental_report:
                result["fundamental_analysis"] = fundamental_report.model_dump()
                report_path = self._get_pipeline_dir(run_id) + os.sep + "step3_fundamental_analysis.md"
                if os.path.exists(report_path):
                    result["fundamental_report_path"] = report_path
                self._logger.info(
                    f"[Step 3/7] 基本面分析完成: 评级={fundamental_report.overall_rating}, "
                    f"分数={fundamental_report.rating_score:.2f}"
                )
            else:
                self._logger.warning("[Step 3/7] 基本面分析失败，使用空报告")
                result["fundamental_analysis"] = None

            if micro_event_report:
                result["micro_event_analysis"] = micro_event_report.model_dump()
                report_path = self._get_pipeline_dir(run_id) + os.sep + "step4_micro_event_analysis.md"
                if os.path.exists(report_path):
                    result["micro_event_report_path"] = report_path
                self._logger.info(
                    f"[Step 4/7] 微观事件分析完成: 发现{micro_event_report.events_count}个事件, "
                    f"情绪变化={micro_event_report.sentiment_shift}"
                )
            else:
                self._logger.warning("[Step 4/7] 微观事件分析失败，使用空报告")
                result["micro_event_analysis"] = None

            # 三层架构第3层：将报告 md 路径引用写入共享记忆（供检索/复盘）
            try:
                ref_paths = {k: v for k, v in {
                    "analysis": result.get("analysis_report_path"),
                    "news": result.get("news_report_path"),
                    "fundamental": result.get("fundamental_report_path"),
                    "micro_event": result.get("micro_event_report_path"),
                }.items() if v}
                if ref_paths:
                    await self.shared_memory.store(
                        agent_id="coordinator",
                        memory_type=self.shared_memory.MemoryType.SYSTEM_EVENT,
                        content=(
                            f"{symbol} 分析报告落盘 (run_id={run_id}): "
                            + ", ".join(f"{k}={v}" for k, v in ref_paths.items())
                        ),
                        structured_data={"report_paths": ref_paths, "run_id": run_id, "symbol": symbol},
                        importance=self.shared_memory.MemoryImportance.HIGH,
                        tags=[symbol, "pipeline_reports", run_id],
                    )
            except Exception as e:
                self._logger.warning(f"[Pipeline {run_id}] 报告路径写入共享记忆失败: {e}")

            self._logger.info("[Phase 1] 并行分析阶段完成")

            # ---- 第5步: 策略生成(多空辩论) ----
            self._logger.info("[Step 5/7] 策略生成(多空辩论)...")
            await _emit({
                "type": "agent_thinking",
                "run_id": run_id,
                "step": 5,
                "agent_id": "strategy_generator",
                "agent_name": "策略生成(多空辩论)",
                "content": f"## 策略生成(多空辩论)\n\n正在综合 {symbol} 的市场/新闻/基本面/微观事件报告，并进行多空研究员辩论...",
            })
            # 组装报告 md 落盘路径（三层架构第2层，供辩论读取全文）
            report_paths = {
                k: v for k, v in {
                    "analysis": result.get("analysis_report_path"),
                    "news": result.get("news_report_path"),
                    "fundamental": result.get("fundamental_report_path"),
                    "micro_event": result.get("micro_event_report_path"),
                }.items() if v
            }

            async def _run_strategy():
                return await self._generate_strategy_with_debate(
                    symbol=symbol,
                    analysis_report=analysis_report,
                    news_report=news_report,
                    fundamental_report=fundamental_report,
                    micro_event_report=micro_event_report,
                    current_price=current_price,
                    report_paths=report_paths,
                )

            strategy_signal = await self._run_step(run_id, 5, "strategy_signal", _run_strategy, cancel_check=cancel_check)
            result["signal"] = strategy_signal.model_dump()
            self._logger.info(
                f"策略生成完成: 方向={strategy_signal.direction.value}, "
                f"置信度={strategy_signal.confidence:.2f}"
            )
            _reasoning_5 = _flush_step_reasoning(5)
            await _emit({
                "type": "agent_thought",
                "run_id": run_id,
                "step": 5,
                "agent_id": "strategy_generator",
                "agent_name": "策略生成(多空辩论)",
                "reasoning": _reasoning_5,
                "content": f"方向={strategy_signal.direction.value}, "
                           f"置信度={strategy_signal.confidence:.2f}",
            })

            # 存储到共享记忆
            await self.shared_memory.store(
                agent_id=self.strategy_generator.agent_id,
                memory_type=self.shared_memory.MemoryType.STRATEGY_DECISION,
                content=f"{symbol} 策略信号: 方向={strategy_signal.direction.value}, "
                        f"置信度={strategy_signal.confidence:.2f}",
                structured_data=strategy_signal.model_dump(),
                importance=self.shared_memory.MemoryImportance.CRITICAL,
                tags=[symbol, "strategy", strategy_signal.direction.value],
            )

            # 如果方向是HOLD，直接结束（记录终态）
            if strategy_signal.direction.value == "hold":
                self._logger.info("策略信号为HOLD，流水线结束")
                result["risk_decision"] = None
                result["execution"] = None
                self._save_pipeline_state(run_id, "completed", terminal="hold")
                return result

            # ---- 第6步: 风控审批 ----
            self._logger.info("[Step 6/7] 风控审批...")
            await _emit({
                "type": "agent_thinking",
                "run_id": run_id,
                "step": 6,
                "agent_id": "risk_manager",
                "agent_name": "风控审批",
                "content": f"## 风控审批\n\n正在评估 {symbol} 的策略信号风险...",
            })

            async def _run_risk():
                # 传入真实组合状态：初始资金 + 空仓（系统暂无组合管理模块，
                # 初始组合是真实的起点状态；future 接入持仓后可 update_portfolio 覆盖）
                from finhack_pro.agents.risk_manager import PortfolioState
                _initial = self.config.get("risk", {}).get("initial_capital", 1_000_000)
                return await self.risk_manager.evaluate_risk(
                    signal=strategy_signal,
                    portfolio=PortfolioState(total_value=_initial, cash=_initial),
                )

            risk_decision = await self._run_step(run_id, 6, "risk_decision", _run_risk, cancel_check=cancel_check)
            result["risk_decision"] = risk_decision.model_dump()
            self._logger.info(
                f"风控审批完成: {'通过' if risk_decision.approved else '拒绝'}"
            )
            _reasoning_6 = _flush_step_reasoning(6)
            await _emit({
                "type": "agent_thought",
                "run_id": run_id,
                "step": 6,
                "agent_id": "risk_manager",
                "agent_name": "风控审批",
                "reasoning": _reasoning_6,
                "content": f"{'通过' if risk_decision.approved else '拒绝'}：{risk_decision.reasoning[:200]}",
            })

            # 存储到共享记忆
            await self.shared_memory.store(
                agent_id=self.risk_manager.agent_id,
                memory_type=self.shared_memory.MemoryType.RISK_DECISION,
                content=f"{symbol} 风控决策: "
                        f"{'通过' if risk_decision.approved else '拒绝'}",
                structured_data=risk_decision.model_dump(),
                importance=self.shared_memory.MemoryImportance.CRITICAL,
                tags=[symbol, "risk", "approved" if risk_decision.approved else "rejected"],
            )

            if not risk_decision.approved:
                self._logger.warning(f"信号被风控拒绝: {risk_decision.reasoning}")
                result["execution"] = None
                self._save_pipeline_state(run_id, "completed", terminal="risk_rejected")
                return result

            # ---- 第7步: 交易执行 ----
            self._logger.info("[Step 7/7] 交易执行...")
            await _emit({
                "type": "agent_thinking",
                "run_id": run_id,
                "step": 7,
                "agent_id": "trade_executor",
                "agent_name": "交易执行",
                "content": f"## 交易执行\n\n正在为 {symbol} 生成执行计划...",
            })

            async def _run_execution():
                return await self.trade_executor.execute(
                    signal=strategy_signal,
                    decision=risk_decision,
                    current_price=current_price,
                )

            execution_report = await self._run_step(run_id, 7, "execution_report", _run_execution, cancel_check=cancel_check)
            result["execution"] = execution_report.model_dump()
            self._logger.info(
                f"交易执行完成: 状态={execution_report.status}, "
                f"成交={execution_report.filled_volume}股"
            )
            _reasoning_7 = _flush_step_reasoning(7)
            await _emit({
                "type": "agent_thought",
                "run_id": run_id,
                "step": 7,
                "agent_id": "trade_executor",
                "agent_name": "交易执行",
                "reasoning": _reasoning_7,
                "content": f"状态={execution_report.status}, 成交={execution_report.filled_volume}股",
            })

            # 存储到共享记忆
            await self.shared_memory.store(
                agent_id=self.trade_executor.agent_id,
                memory_type=self.shared_memory.MemoryType.EXECUTION_RECORD,
                content=f"{symbol} 交易执行: 状态={execution_report.status}, "
                        f"成交={execution_report.filled_volume}股",
                structured_data=execution_report.model_dump(),
                importance=self.shared_memory.MemoryImportance.CRITICAL,
                tags=[symbol, "execution", execution_report.status],
            )

            self._save_pipeline_state(run_id, "completed", terminal="executed")

        except PipelineCancelledError:
            # 用户取消（协作式标志路径）：落盘 cancelled 终态（已 done 步骤保留）
            self._logger.warning(f"[Pipeline {run_id}] 用户取消流水线")
            self._save_pipeline_state(run_id, "cancelled")
            result["status"] = "cancelled"
            result["error"] = "用户取消流水线"

        except asyncio.CancelledError:
            # 用户取消（task.cancel() 即时中断路径）：同样落盘 cancelled 终态。
            # 不 re-raise：吞掉后正常收尾（恢复流回调 + 返回结果），task 受控结束。
            self._logger.warning(f"[Pipeline {run_id}] 用户取消流水线（task.cancel）")
            self._save_pipeline_state(run_id, "cancelled")
            result["status"] = "cancelled"
            result["error"] = "用户取消流水线"

        except Exception as e:
            self._logger.error(f"分析流水线异常: {e}", exc_info=True)
            result["error"] = str(e)
            # 异常即失败：落盘 failed 状态，避免 pipeline_state.json 卡在 running
            self._save_pipeline_state(run_id, "failed")

            # 记录异常到共享记忆
            await self.shared_memory.store(
                agent_id="coordinator",
                memory_type=self.shared_memory.MemoryType.SYSTEM_EVENT,
                content=f"{symbol} 分析流水线异常: {str(e)}",
                importance=self.shared_memory.MemoryImportance.HIGH,
                tags=[symbol, "error", "pipeline"],
            )

        # 恢复各 agent 注入前的 LLM 流回调（流水线结束；恢复而非清空，
        # 防并发场景下把其他调用方的回调一并清掉）
        try:
            for _aname, _agent in self._agents.items():
                if hasattr(_agent, "set_llm_stream_callbacks"):
                    _saved = _saved_stream_callbacks.get(_aname, (None, None))
                    _agent.set_llm_stream_callbacks(
                        on_token=_saved[0] if len(_saved) > 0 else None,
                        on_reasoning=_saved[1] if len(_saved) > 1 else None,
                    )
        except Exception:
            self._logger.warning("恢复 LLM 流回调失败", exc_info=True)

        self._logger.info(f"========== 分析流水线完成: {symbol} ==========")
        return result

    async def run_analysis_pipeline(
        self,
        symbol: str,
        market_data: Optional[Dict[str, Any]] = None,
        indicators: Optional[Dict[str, Any]] = None,
        current_price: Optional[float] = None,
        run_id: Optional[str] = None,
        resume: bool = True,
        event_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        cancel_check: Optional[Callable[[], None]] = None,
    ) -> Dict[str, Any]:
        """执行分析流水线（并发隔离门面）

        同一时间仅允许一个流水线运行：并发任务会共享同一批 Agent/LLMClient
        实例，导致流回调互相覆盖、思考链事件串 run、LLM 请求互相排队。
        已有一个流水线在运行 → 抛 PipelineBusyError（由上层转为明确提示）。

        cancel_check: 协作式取消回调（每步骤边界调用一次，抛
        PipelineCancelledError 中止流水线并落盘 cancelled 状态）。
        """
        if self._pipeline_active:
            raise PipelineBusyError(
                "已有分析流水线正在运行，请等待其完成后再启动新的分析"
                "（并发运行会导致思考链串流与输出混淆）"
            )
        self._pipeline_active = True
        try:
            return await self._run_analysis_pipeline_impl(
                symbol=symbol,
                market_data=market_data,
                indicators=indicators,
                current_price=current_price,
                run_id=run_id,
                resume=resume,
                event_callback=event_callback,
                cancel_check=cancel_check,
            )
        finally:
            self._pipeline_active = False

    async def _generate_strategy_with_debate(
        self,
        symbol: str,
        analysis_report: Any,
        news_report: Any,
        fundamental_report: Any,
        micro_event_report: Any = None,
        current_price: Optional[float] = None,
        report_paths: Optional[Dict[str, str]] = None,
    ) -> Any:
        """使用多空辩论模式生成策略

        综合技术面、新闻面、基本面、微观事件四方报告，通过策略生成Agent的
        多空辩论机制生成最终策略信号。

        Args:
            symbol: 标的代码
            analysis_report: 技术面分析报告
            news_report: 新闻分析报告
            fundamental_report: 基本面分析报告
            micro_event_report: 微观事件分析报告(新增)
            current_price: 当前价格
            report_paths: 各报告 md 落盘路径字典（三层架构第2层），
                如 {"analysis": ".../step1_market_analysis.md", ...}

        Returns:
            StrategySignal 策略信号
        """
        # 优先尝试使用 debate 方法(如果策略生成Agent支持)
        if hasattr(self.strategy_generator, "debate"):
            self._logger.info("使用多空辩论模式生成策略...")
            try:
                signal = await self.strategy_generator.debate(
                    analysis_report=analysis_report,
                    news_report=news_report,
                    fundamental_report=fundamental_report,
                    micro_event_report=micro_event_report,
                    current_price=current_price,
                    report_paths=report_paths,
                )
                return signal
            except Exception as e:
                self._logger.warning(f"多空辩论失败，回退到普通策略生成: {e}")

        # 回退: 使用传统的 generate_strategy 方法
        self._logger.info("使用传统策略生成模式...")
        signal = await self.strategy_generator.generate_strategy(
            analysis=analysis_report,
            current_price=current_price,
        )
        return signal

    async def run_multi_symbol_pipeline(
        self,
        symbols: List[str],
        market_data_map: Optional[Dict[str, Dict[str, Any]]] = None,
        indicators_map: Optional[Dict[str, Dict[str, Any]]] = None,
        max_concurrent: int = 5,
    ) -> Dict[str, Dict[str, Any]]:
        """并行处理多个标的的分析流水线

        Args:
            symbols: 标的代码列表
            market_data_map: 各标的市场数据
            indicators_map: 各标的的技术指标
            max_concurrent: 最大并发数

        Returns:
            各标的的分析结果字典
        """
        self._logger.info(f"开始并行分析 {len(symbols)} 个标的...")

        semaphore = asyncio.Semaphore(max_concurrent)
        results: Dict[str, Dict[str, Any]] = {}

        async def _analyze_one(sym: str) -> None:
            async with semaphore:
                try:
                    mkt_data = (market_data_map or {}).get(sym)
                    inds = (indicators_map or {}).get(sym)
                    result = await self.run_analysis_pipeline(
                        symbol=sym,
                        market_data=mkt_data,
                        indicators=inds,
                    )
                    results[sym] = result
                except Exception as e:
                    self._logger.error(f"分析 {sym} 失败: {e}")
                    results[sym] = {"symbol": sym, "error": str(e)}

        tasks = [asyncio.create_task(_analyze_one(s)) for s in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)

        self._logger.info(f"并行分析完成: {len(results)}/{len(symbols)} 个标的")
        return results

    # ============================================================
    # 定时分析
    # ============================================================

    async def start_scheduled_analysis(
        self,
        symbols: List[str],
        interval: int = 300,
        data_fetcher: Optional[Any] = None,
    ) -> None:
        """启动定时分析循环

        Args:
            symbols: 监控标的列表
            interval: 分析间隔(秒)
            data_fetcher: 数据获取器(可选)
        """
        self._logger.info(
            f"启动定时分析: 标的={symbols}, 间隔={interval}秒"
        )

        async def _analysis_loop() -> None:
            while self._running:
                try:
                    self._logger.info("--- 定时分析触发 ---")

                    # 执行记忆衰减
                    try:
                        decayed = await self.shared_memory.decay(
                            hours=self._decay_hours
                        )
                        if decayed > 0:
                            self._logger.debug(f"记忆衰减: {decayed} 条")
                    except Exception as e:
                        self._logger.debug(f"记忆衰减执行失败: {e}")

                    for symbol in symbols:
                        try:
                            # 获取最新数据
                            market_data = {}
                            indicators = {}
                            if data_fetcher:
                                df = await data_fetcher.get_daily(symbol)
                                if df is not None and not df.empty:
                                    market_data = self._df_to_market_data(df)

                            result = await self.run_analysis_pipeline(
                                symbol=symbol,
                                market_data=market_data,
                                indicators=indicators,
                            )
                            self._logger.info(f"定时分析完成: {symbol}")

                        except Exception as e:
                            self._logger.error(f"定时分析 {symbol} 失败: {e}")

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self._logger.error(f"定时分析循环异常: {e}")

                await asyncio.sleep(interval)

        task = asyncio.create_task(_analysis_loop())
        self._analysis_tasks.append(task)

    # ============================================================
    # 共享记忆与工具集代理方法
    # ============================================================

    async def get_memory_stats(self) -> Dict[str, Any]:
        """获取共享记忆统计信息

        Returns:
            包含记忆总数、分类统计、Agent统计等的字典
        """
        return await self.shared_memory.get_stats()

    async def get_tool_stats(self) -> Dict[str, Any]:
        """获取工具集统计信息

        Returns:
            包含工具总数、调用次数、分类等的字典
        """
        return self.tool_registry.get_stats()

    async def get_agent_status(self) -> Dict[str, Any]:
        """获取所有Agent的状态信息

        Returns:
            包含各Agent运行状态、角色、ID等的字典
        """
        status: Dict[str, Any] = {
            "coordinator_running": self._running,
            "agents": {},
        }
        for name, agent in self._agents.items():
            status["agents"][name] = {
                "role": agent.role.value,
                "agent_id": agent.agent_id,
                "running": agent.is_running,
                "has_shared_memory": agent.shared_memory is not None,
                "has_tool_registry": agent.tool_registry is not None,
            }
        return status

    async def search_memory(
        self,
        memory_type: Optional[str] = None,
        agent_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        importance: Optional[str] = None,
        limit: int = 50,
    ) -> List[Any]:
        """代理共享记忆的检索

        Args:
            memory_type: 记忆类型过滤(字符串, 如 "analysis_report")
            agent_id: Agent ID过滤
            tags: 标签列表过滤
            keywords: 关键词列表过滤
            start_time: 起始时间(ISO格式)
            end_time: 结束时间(ISO格式)
            importance: 最低重要性过滤(如 "high")
            limit: 返回数量上限

        Returns:
            MemoryEntry列表
        """
        from finhack_pro.agents.shared_memory import MemoryImportance, MemoryType

        # 将字符串参数转换为枚举
        mem_type = None
        if memory_type:
            try:
                mem_type = MemoryType(memory_type)
            except ValueError:
                self._logger.warning(f"未知的记忆类型: {memory_type}")

        imp = None
        if importance:
            try:
                imp = MemoryImportance(importance)
            except ValueError:
                self._logger.warning(f"未知的重要性级别: {importance}")

        return await self.shared_memory.retrieve(
            memory_type=mem_type,
            agent_id=agent_id,
            tags=tags,
            keywords=keywords,
            start_time=start_time,
            end_time=end_time,
            importance=imp,
            limit=limit,
        )

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _df_to_market_data(df: Any) -> Dict[str, Any]:
        """将DataFrame转换为市场数据字典（使用真实价格，不伪造零值）

        Args:
            df: pandas DataFrame（须含 date/open/high/low/close/volume 列）

        Returns:
            市场数据字典

        Raises:
            ValueError: 数据为空或缺失必要列时抛出，交由上层真实失败（不静默返回 {}）
        """
        import pandas as pd

        if not isinstance(df, pd.DataFrame) or df.empty:
            raise ValueError("市场数据为空，无法转换为分析上下文")

        required = ["date", "open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"市场数据缺失必要列: {missing}（无法继续分析）")

        recent_bars = []
        for _, row in df.tail(10).iterrows():
            bar = {
                "date": str(row["date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            recent_bars.append(bar)

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        prev_close = float(prev["close"])
        current = {
            "close": float(last["close"]),
            "change_pct": float(
                (float(last["close"]) - prev_close) / max(prev_close, 0.01) * 100
            ),
        }

        return {"recent_bars": recent_bars, "current": current}

    def _fetch_real_market_data(self, symbol: str) -> Dict[str, Any]:
        """L5d：从配置的数据源真实拉取行情并转换为分析上下文。

        数据源连接/获取失败会直接抛出（不伪造空行情），交由上层标为流水线失败。
        注：实时 WebUI 入口在 WebUIService.run_pipeline 中调用本方法取数后传入，
        避免在单元测试（无数据源配置）下触发隐式联网。
        """
        import datetime as _dt

        from finhack_pro.data.fetcher import DataFetcher

        data_cfg = (self.config or {}).get("data", {})
        fetcher = DataFetcher(
            source=data_cfg.get("source", "akshare"),
            tushare_token=data_cfg.get("tushare_token", "") or "",
            cache_dir=data_cfg.get("cache_dir", "data/cache"),
            sources=data_cfg.get("sources") or None,
            custom_source=data_cfg.get("custom_source", "") or "",
        )
        end_date = _dt.date.today().strftime("%Y-%m-%d")
        start_date = (_dt.date.today() - _dt.timedelta(days=180)).strftime("%Y-%m-%d")
        df = fetcher.get_daily(symbol=symbol, start_date=start_date, end_date=end_date)
        return self._df_to_market_data(df)
