"""
业务逻辑服务层

提供配置管理、回测任务管理、Agent系统交互、记忆查询、WebSocket广播等业务逻辑。
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger

from finhack_pro.config import FinhackProConfig, get_config, reset_config
from finhack_pro.webui.models import (
    AgentInfo,
    BacktestMetrics,
    BacktestRequest,
    BacktestResult,
    BacktestStatus,
    ConnectionTestResult,
    MemoryEntryResponse,
    MemorySearchRequest,
    MemoryStats,
    PipelineRunResult,
    PipelineStepResult,
    SystemInfo,
    ToolCallStats,
    ToolInfo,
    ToolParameterInfo,
    TradeRecord,
)

# ============================================================
# 配置服务
# ============================================================

class ConfigService:
    """配置管理服务

    负责配置的读取、更新、持久化和API连接测试。
    """

    # 需要隐藏的敏感字段
    _sensitive_fields = {"openai_api_key", "anthropic_api_key", "tushare_token", "api_key"}

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path
        self._config = get_config(config_path)

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置(隐藏敏感字段)"""
        data = self._config.model_dump()
        return self._mask_sensitive(data)

    def get_full_config(self) -> Dict[str, Any]:
        """获取完整配置(包含敏感字段，用于编辑)"""
        return self._config.model_dump()

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """更新配置

        Args:
            updates: 配置更新字典，支持 llm/data/risk/execution 子配置

        Returns:
            更新后的配置
        """
        config_dict = self._config.model_dump()

        for section, values in updates.items():
            if values and section in config_dict:
                if isinstance(config_dict[section], dict):
                    for key, value in values.items():
                        if value is not None:
                            config_dict[section][key] = value

        # 重新创建配置对象
        reset_config()
        self._config = FinhackProConfig(**config_dict)
        logger.info("配置已更新")
        return self._mask_sensitive(self._config.model_dump())

    def save_config(self, path: Optional[str] = None) -> str:
        """保存配置到文件

        Args:
            path: 保存路径，为None时使用默认路径

        Returns:
            保存的文件路径
        """
        save_path = path or self._config_path or "config/default.yaml"
        self._config.save_to_yaml(save_path)
        logger.info(f"配置已保存到: {save_path}")
        return save_path

    async def test_connection(
        self,
        provider: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> ConnectionTestResult:
        """测试API连接

        Args:
            provider: 服务商名称 (openai / anthropic / tushare)
            api_key: API密钥(可选，为None时使用当前配置)
            base_url: 自定义API地址

        Returns:
            连接测试结果
        """
        start_time = time.time()

        try:
            if provider == "openai":
                return await self._test_openai(api_key, base_url, start_time)
            elif provider == "anthropic":
                return await self._test_anthropic(api_key, start_time)
            elif provider == "tushare":
                return await self._test_tushare(api_key, start_time)
            else:
                return ConnectionTestResult(
                    provider=provider,
                    success=False,
                    message=f"不支持的服务商: {provider}",
                )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return ConnectionTestResult(
                provider=provider,
                success=False,
                message=f"连接异常: {str(e)}",
                latency_ms=round(latency, 2),
            )

    async def _test_openai(
        self, api_key: Optional[str], base_url: Optional[str], start_time: float
    ) -> ConnectionTestResult:
        """测试OpenAI连接"""
        import httpx

        key = api_key or self._config.llm.openai_api_key
        url = base_url or self._config.llm.openai_base_url
        if not key:
            return ConnectionTestResult(
                provider="openai",
                success=False,
                message="OpenAI API Key 未配置",
            )

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            latency = (time.time() - start_time) * 1000

            if resp.status_code == 200:
                models = resp.json().get("data", [])
                model_names = [m["id"] for m in models[:5]]
                return ConnectionTestResult(
                    provider="openai",
                    success=True,
                    message=f"连接成功，可用模型: {', '.join(model_names)}",
                    latency_ms=round(latency, 2),
                )
            else:
                return ConnectionTestResult(
                    provider="openai",
                    success=False,
                    message=f"API返回错误: {resp.status_code} {resp.text[:200]}",
                    latency_ms=round(latency, 2),
                )

    async def _test_anthropic(
        self, api_key: Optional[str], start_time: float
    ) -> ConnectionTestResult:
        """测试Anthropic连接"""
        import httpx

        key = api_key or self._config.llm.anthropic_api_key
        if not key:
            return ConnectionTestResult(
                provider="anthropic",
                success=False,
                message="Anthropic API Key 未配置",
            )

        # Anthropic没有简单的list models端点，发送一个最小请求测试
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-haiku-20240307",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            latency = (time.time() - start_time) * 1000

            if resp.status_code == 200:
                return ConnectionTestResult(
                    provider="anthropic",
                    success=True,
                    message="连接成功",
                    latency_ms=round(latency, 2),
                )
            else:
                return ConnectionTestResult(
                    provider="anthropic",
                    success=False,
                    message=f"API返回错误: {resp.status_code} {resp.text[:200]}",
                    latency_ms=round(latency, 2),
                )

    async def _test_tushare(
        self, api_key: Optional[str], start_time: float
    ) -> ConnectionTestResult:
        """测试Tushare连接"""
        key = api_key or self._config.data.tushare_token
        if not key:
            return ConnectionTestResult(
                provider="tushare",
                success=False,
                message="Tushare Token 未配置",
            )

        try:
            import tushare as ts

            ts.set_token(key)
            pro = ts.pro_api()
            # 获取交易日历测试连接
            df = pro.trade_cal(exchange="SSE", start_date="20240101", end_date="20240110")
            latency = (time.time() - start_time) * 1000

            if df is not None and len(df) > 0:
                return ConnectionTestResult(
                    provider="tushare",
                    success=True,
                    message=f"连接成功，获取到 {len(df)} 条交易日历数据",
                    latency_ms=round(latency, 2),
                )
            else:
                return ConnectionTestResult(
                    provider="tushare",
                    success=False,
                    message="连接成功但未获取到数据，请检查Token权限",
                    latency_ms=round(latency, 2),
                )
        except ImportError:
            return ConnectionTestResult(
                provider="tushare",
                success=False,
                message="tushare包未安装，请执行: pip install tushare",
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return ConnectionTestResult(
                provider="tushare",
                success=False,
                message=f"Tushare连接失败: {str(e)}",
                latency_ms=round(latency, 2),
            )

    def _mask_sensitive(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """递归隐藏敏感字段"""
        result = {}
        for key, value in data.items():
            if key in self._sensitive_fields:
                if value and isinstance(value, str) and len(value) > 4:
                    result[key] = value[:2] + "****" + value[-2:]
                elif value:
                    result[key] = "****"
                else:
                    result[key] = ""
            elif isinstance(value, dict):
                result[key] = self._mask_sensitive(value)
            else:
                result[key] = value
        return result


# ============================================================
# 回测服务
# ============================================================

class BacktestService:
    """回测任务管理服务

    管理回测任务的创建、执行、状态查询和结果获取。
    使用asyncio在后台运行回测任务。
    """

    def __init__(self):
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._results: Dict[str, BacktestResult] = {}
        self._history: List[Dict[str, Any]] = []

    def create_task(self, request: BacktestRequest) -> BacktestStatus:
        """创建回测任务

        Args:
            request: 回测请求参数

        Returns:
            回测任务状态
        """
        task_id = uuid.uuid4().hex[:12]
        status = BacktestStatus(
            task_id=task_id,
            status="pending",
            start_time=datetime.now().isoformat(),
        )
        self._tasks[task_id] = {
            "request": request,
            "status": status,
            "created_at": time.time(),
        }
        logger.info(f"回测任务已创建: {task_id}")
        return status

    async def run_task(self, task_id: str, stream_callback: Optional[Callable] = None) -> None:
        """在后台执行回测任务

        Args:
            task_id: 任务ID
            stream_callback: 进度回调函数(用于WebSocket推送)
        """
        task_info = self._tasks.get(task_id)
        if not task_info:
            logger.error(f"回测任务不存在: {task_id}")
            return

        request: BacktestRequest = task_info["request"]
        status: BacktestStatus = task_info["status"]
        status.status = "running"

        try:
            # 模拟回测过程(实际应调用回测引擎)
            total_bars = 242  # 模拟一年交易日
            status.total_bars = total_bars

            equity_curve = []
            benchmark_curve = []
            trades = []
            equity = request.initial_capital

            import random
            random.seed(hash(task_id))

            for i in range(total_bars):
                # 模拟每日收益
                daily_return = random.gauss(0.0003, 0.015)
                benchmark_return = random.gauss(0.0001, 0.012)
                equity *= (1 + daily_return)

                equity_curve.append({
                    "date": f"2024-{(i // 22 + 1):02d}-{(i % 22 + 1):02d}",
                    "equity": round(equity, 2),
                })
                benchmark_curve.append({
                    "date": f"2024-{(i // 22 + 1):02d}-{(i % 22 + 1):02d}",
                    "equity": round(request.initial_capital * (1 + benchmark_return * (i + 1)), 2),
                })

                # 模拟随机交易
                if random.random() < 0.05:
                    direction = "buy" if random.random() > 0.5 else "sell"
                    price = equity / 1000 * random.uniform(0.9, 1.1)
                    volume = random.randint(100, 1000)
                    trades.append(TradeRecord(
                        date=f"2024-{(i // 22 + 1):02d}-{(i % 22 + 1):02d}",
                        symbol=request.symbols[0] if request.symbols else "000001.SZ",
                        direction=direction,
                        price=round(price, 2),
                        volume=volume,
                        commission=round(price * volume * 0.0003, 2),
                        pnl=round(random.uniform(-500, 800), 2),
                        reason="策略信号触发",
                    ))

                # 更新进度
                status.current_bar = i + 1
                status.progress = round((i + 1) / total_bars * 100, 1)
                status.message = f"正在处理第 {i + 1}/{total_bars} 个交易日"

                # 推送进度
                if stream_callback:
                    await stream_callback({
                        "type": "backtest_progress",
                        "task_id": task_id,
                        "progress": status.progress,
                        "current_bar": status.current_bar,
                        "total_bars": total_bars,
                        "equity": round(equity, 2),
                    })

                # 模拟处理延迟
                await asyncio.sleep(0.02)

            # 计算回测指标
            total_return = (equity - request.initial_capital) / request.initial_capital
            annual_return = total_return * (242 / total_bars) * (242 / total_bars) if total_bars > 0 else 0

            winning_trades = [t for t in trades if t.pnl > 0]
            win_rate = len(winning_trades) / len(trades) if trades else 0
            avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
            losing_trades = [t for t in trades if t.pnl <= 0]
            avg_loss = abs(sum(t.pnl for t in losing_trades) / len(losing_trades)) if losing_trades else 1

            metrics = BacktestMetrics(
                total_return=round(total_return * 100, 2),
                annual_return=round(annual_return * 100, 2),
                sharpe_ratio=round(total_return / max(0.015, 0.001) * (242 ** 0.5), 2),
                sortino_ratio=round(total_return / max(0.01, 0.001) * (242 ** 0.5), 2),
                max_drawdown=round(random.uniform(5, 20), 2),
                win_rate=round(win_rate * 100, 2),
                profit_loss_ratio=round(avg_win / avg_loss, 2) if avg_loss > 0 else 0,
                total_trades=len(trades),
                final_equity=round(equity, 2),
            )

            result = BacktestResult(
                task_id=task_id,
                status="completed",
                metrics=metrics,
                equity_curve=equity_curve,
                trades=[t.model_dump() for t in trades],
                benchmark_curve=benchmark_curve,
            )

            status.status = "completed"
            status.progress = 100
            status.message = "回测完成"
            status.end_time = datetime.now().isoformat()
            self._results[task_id] = result

            # 添加到历史记录
            self._history.append({
                "task_id": task_id,
                "symbol": ", ".join(request.symbols),
                "strategy": request.strategy.value,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "total_return": metrics.total_return,
                "sharpe_ratio": metrics.sharpe_ratio,
                "status": "completed",
                "created_at": status.start_time,
            })

            # 推送完成事件
            if stream_callback:
                await stream_callback({
                    "type": "backtest_completed",
                    "task_id": task_id,
                    "metrics": metrics.model_dump(),
                })

            logger.info(f"回测任务完成: {task_id}, 收益率={metrics.total_return}%")

        except Exception as e:
            status.status = "failed"
            status.message = f"回测失败: {str(e)}"
            status.end_time = datetime.now().isoformat()
            logger.error(f"回测任务失败: {task_id}, 错误: {e}")

            if stream_callback:
                await stream_callback({
                    "type": "backtest_failed",
                    "task_id": task_id,
                    "error": str(e),
                })

    def get_task_status(self, task_id: str) -> Optional[BacktestStatus]:
        """获取任务状态"""
        task_info = self._tasks.get(task_id)
        return task_info["status"] if task_info else None

    def get_task_result(self, task_id: str) -> Optional[BacktestResult]:
        """获取任务结果"""
        return self._results.get(task_id)

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取历史回测记录"""
        return self._history[-limit:]


# ============================================================
# Agent服务
# ============================================================

class AgentService:
    """Agent系统交互服务

    管理Agent状态查询和流水线执行。
    """

    # Agent中文名称映射
    _agent_names = {
        "market_analyzer": "市场分析Agent",
        "news_analyst": "新闻社媒Agent",
        "fundamental_analyst": "基本面Agent",
        "strategy_generator": "多空研究员",
        "risk_manager": "风险管理Agent",
        "trade_executor": "交易执行Agent",
    }

    def __init__(self):
        self._coordinator = None
        self._pipeline_history: List[Dict[str, Any]] = []
        self._running_pipelines: Dict[str, Dict[str, Any]] = {}

    def set_coordinator(self, coordinator: Any) -> None:
        """设置Agent协调器实例"""
        self._coordinator = coordinator

    async def get_agent_list(self) -> List[AgentInfo]:
        """获取所有Agent列表和状态"""
        agents = []

        if self._coordinator:
            try:
                status = await self._coordinator.get_agent_status()
                for name, info in status.get("agents", {}).items():
                    agents.append(AgentInfo(
                        agent_id=info.get("agent_id", name),
                        name=self._agent_names.get(name, name),
                        role=info.get("role", name),
                        status="running" if info.get("running") else "idle",
                        message_count=0,
                    ))
            except Exception as e:
                logger.error(f"获取Agent状态失败: {e}")
        else:
            # 未连接协调器时返回默认Agent列表
            for agent_id, name in self._agent_names.items():
                agents.append(AgentInfo(
                    agent_id=agent_id,
                    name=name,
                    role=agent_id,
                    status="idle",
                ))

        return agents

    async def get_agent_status(self, agent_id: str) -> Optional[AgentInfo]:
        """获取单个Agent状态"""
        agents = await self.get_agent_list()
        for agent in agents:
            if agent.agent_id == agent_id:
                return agent
        return None

    async def run_pipeline(
        self,
        request: Any,
        stream_callback: Optional[Callable] = None,
    ) -> PipelineRunResult:
        """执行分析流水线

        Args:
            request: 流水线执行请求
            stream_callback: 流式回调(用于WebSocket推送)

        Returns:
            流水线执行结果
        """
        run_id = uuid.uuid4().hex[:12]
        result = PipelineRunResult(
            run_id=run_id,
            symbol=request.symbol,
            status="running",
            start_time=datetime.now().isoformat(),
        )

        self._running_pipelines[run_id] = {"result": result, "request": request}

        if stream_callback:
            await stream_callback({
                "type": "pipeline_started",
                "run_id": run_id,
                "symbol": request.symbol,
            })

        if self._coordinator:
            try:
                # 定义流水线步骤
                steps = [
                    (1, "市场分析(技术面)", "market_analyzer"),
                    (2, "新闻社媒分析", "news_analyst"),
                    (3, "基本面分析", "fundamental_analyst"),
                    (4, "策略生成(多空辩论)", "strategy_generator"),
                    (5, "风控审批", "risk_manager"),
                    (6, "交易执行", "trade_executor"),
                ]

                for step_num, step_name, agent_id in steps:
                    step_start = time.time()
                    step_result = PipelineStepResult(
                        step=step_num,
                        agent_name=step_name,
                        status="running",
                    )
                    result.steps.append(step_result)

                    if stream_callback:
                        await stream_callback({
                            "type": "agent_thinking",
                            "run_id": run_id,
                            "step": step_num,
                            "agent_id": agent_id,
                            "agent_name": step_name,
                            "content": f"正在执行 {step_name}...",
                        })

                    # 模拟步骤执行(实际应调用coordinator)
                    await asyncio.sleep(0.5)

                    step_result.status = "completed"
                    step_result.duration_ms = round((time.time() - step_start) * 1000, 2)
                    step_result.summary = f"{step_name} 完成"

                    if stream_callback:
                        await stream_callback({
                            "type": "agent_thought",
                            "run_id": run_id,
                            "step": step_num,
                            "agent_id": agent_id,
                            "agent_name": step_name,
                            "content": f"## {step_name}\n\n分析完成，耗时 {step_result.duration_ms}ms",
                            "duration_ms": step_result.duration_ms,
                        })

                result.status = "completed"
                result.end_time = datetime.now().isoformat()
                result.final_signal = {
                    "direction": "hold",
                    "confidence": 0.5,
                    "reason": "综合分析后建议观望",
                }

            except Exception as e:
                result.status = "failed"
                result.error = str(e)
                result.end_time = datetime.now().isoformat()
                logger.error(f"流水线执行失败: {e}")
        else:
            # 无协调器时模拟执行
            steps = [
                (1, "市场分析(技术面)", "market_analyzer"),
                (2, "新闻社媒分析", "news_analyst"),
                (3, "基本面分析", "fundamental_analyst"),
                (4, "策略生成(多空辩论)", "strategy_generator"),
                (5, "风控审批", "risk_manager"),
                (6, "交易执行", "trade_executor"),
            ]

            for step_num, step_name, agent_id in steps:
                step_start = time.time()
                step_result = PipelineStepResult(
                    step=step_num,
                    agent_name=step_name,
                    status="running",
                )
                result.steps.append(step_result)

                if stream_callback:
                    await stream_callback({
                        "type": "agent_thinking",
                        "run_id": run_id,
                        "step": step_num,
                        "agent_id": agent_id,
                        "agent_name": step_name,
                        "content": f"正在执行 {step_name}...",
                    })

                await asyncio.sleep(0.3)

                step_result.status = "completed"
                step_result.duration_ms = round((time.time() - step_start) * 1000, 2)
                step_result.summary = f"{step_name} 完成"

                if stream_callback:
                    await stream_callback({
                        "type": "agent_thought",
                        "run_id": run_id,
                        "step": step_num,
                        "agent_id": agent_id,
                        "agent_name": step_name,
                        "content": f"## {step_name}\n\n模拟分析完成，耗时 {step_result.duration_ms}ms。\n\n"
                                   f"当前市场状态: 震荡\n趋势方向: 横盘\n建议操作: 观望",
                        "duration_ms": step_result.duration_ms,
                    })

            result.status = "completed"
            result.end_time = datetime.now().isoformat()
            result.final_signal = {
                "direction": "hold",
                "confidence": 0.5,
                "reason": "综合分析后建议观望",
            }

        if stream_callback:
            await stream_callback({
                "type": "pipeline_completed",
                "run_id": run_id,
                "symbol": request.symbol,
                "status": result.status,
                "final_signal": result.final_signal,
            })

        # 添加到历史
        self._pipeline_history.append({
            "run_id": run_id,
            "symbol": request.symbol,
            "status": result.status,
            "steps_completed": len([s for s in result.steps if s.status == "completed"]),
            "start_time": result.start_time,
            "end_time": result.end_time,
        })

        # 清理运行中的记录
        self._running_pipelines.pop(run_id, None)

        return result

    def get_pipeline_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取流水线执行历史"""
        return self._pipeline_history[-limit:]


# ============================================================
# 记忆服务
# ============================================================

class MemoryService:
    """共享记忆查询服务"""

    def __init__(self):
        self._shared_memory = None

    def set_shared_memory(self, shared_memory: Any) -> None:
        """设置共享记忆实例"""
        self._shared_memory = shared_memory

    async def get_stats(self) -> MemoryStats:
        """获取记忆统计"""
        if self._shared_memory:
            try:
                stats = await self._shared_memory.get_stats()
                return MemoryStats(
                    total_memories=stats.get("total_memories", 0),
                    total_entries_ever=stats.get("total_entries_ever", 0),
                    by_type=stats.get("by_type", {}),
                    by_agent=stats.get("by_agent", {}),
                )
            except Exception as e:
                logger.error(f"获取记忆统计失败: {e}")

        return MemoryStats()

    async def search(self, request: MemorySearchRequest) -> List[MemoryEntryResponse]:
        """搜索记忆"""
        if self._shared_memory:
            try:
                entries = await self._shared_memory.retrieve(
                    memory_type=request.memory_type,
                    agent_id=request.agent_id,
                    keywords=request.keywords,
                    start_time=request.start_time,
                    end_time=request.end_time,
                    importance=request.importance,
                    limit=request.limit,
                )
                return [
                    MemoryEntryResponse(
                        id=e.id,
                        memory_type=e.memory_type.value,
                        agent_id=e.agent_id,
                        content=e.content,
                        importance=e.importance.value,
                        timestamp=e.timestamp,
                        tags=e.tags,
                        decay_score=e.decay_score,
                        access_count=e.access_count,
                        summary=e.summary,
                    )
                    for e in entries
                ]
            except Exception as e:
                logger.error(f"搜索记忆失败: {e}")

        return []

    async def get_recent(self, limit: int = 20) -> List[MemoryEntryResponse]:
        """获取最近记忆"""
        if self._shared_memory:
            try:
                entries = await self._shared_memory.get_recent(n=limit)
                return [
                    MemoryEntryResponse(
                        id=e.id,
                        memory_type=e.memory_type.value,
                        agent_id=e.agent_id,
                        content=e.content,
                        importance=e.importance.value,
                        timestamp=e.timestamp,
                        tags=e.tags,
                        decay_score=e.decay_score,
                        access_count=e.access_count,
                        summary=e.summary,
                    )
                    for e in entries
                ]
            except Exception as e:
                logger.error(f"获取最近记忆失败: {e}")

        return []

    async def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        if self._shared_memory:
            try:
                return await self._shared_memory.delete(memory_id)
            except Exception as e:
                logger.error(f"删除记忆失败: {e}")
        return False


# ============================================================
# 流式广播服务
# ============================================================

class StreamService:
    """WebSocket消息广播管理

    管理WebSocket连接和消息广播，支持按频道分组。
    
    优化:
    - 心跳检测：定期发送ping，检测僵尸连接
    - 连接超时：自动清理无响应连接
    """

    def __init__(
        self,
        heartbeat_interval: float = 30.0,   # 心跳间隔(秒)
        heartbeat_timeout: float = 60.0,    # 心跳超时(秒)
    ):
        # 按频道分组的连接管理
        self._channels: Dict[str, Set[Any]] = {
            "backtest": set(),
            "agents": set(),
            "system": set(),
        }
        
        # 心跳配置
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        
        # 连接元数据: {websocket: {"last_pong": timestamp, "channel": str}}
        self._connection_meta: Dict[Any, Dict[str, Any]] = {}
        
        # 心跳任务
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False

    async def start_heartbeat(self) -> None:
        """启动心跳检测任务"""
        if self._running:
            return
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"WebSocket心跳检测已启动, 间隔={self._heartbeat_interval}s")

    async def stop_heartbeat(self) -> None:
        """停止心跳检测任务"""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("WebSocket心跳检测已停止")

    async def _heartbeat_loop(self) -> None:
        """心跳检测循环"""
        import time
        while self._running:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                await self._check_connections()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳检测异常: {e}")

    async def _check_connections(self) -> None:
        """检查所有连接，清理超时连接，发送心跳"""
        import time
        current_time = time.time()
        
        for channel in list(self._channels.keys()):
            dead_connections = set()
            
            for ws in list(self._channels[channel]):
                # 获取连接元数据
                meta = self._connection_meta.get(ws, {})
                last_pong = meta.get("last_pong", current_time)
                
                # 检查超时
                if current_time - last_pong > self._heartbeat_timeout:
                    dead_connections.add(ws)
                    logger.warning(f"WebSocket连接超时，已清理: channel={channel}")
                    continue
                
                # 发送心跳
                try:
                    await ws.send_json({"type": "ping", "timestamp": current_time})
                except Exception:
                    dead_connections.add(ws)
            
            # 清理死连接
            for ws in dead_connections:
                await self._disconnect_internal(channel, ws)

    def on_pong(self, websocket: Any) -> None:
        """处理pong响应，更新最后响应时间"""
        import time
        if websocket in self._connection_meta:
            self._connection_meta[websocket]["last_pong"] = time.time()

    async def connect(self, channel: str, websocket: Any) -> None:
        """注册WebSocket连接到指定频道"""
        import time
        if channel not in self._channels:
            self._channels[channel] = set()
        
        self._channels[channel].add(websocket)
        
        # 初始化连接元数据
        self._connection_meta[websocket] = {
            "last_pong": time.time(),
            "channel": channel,
        }
        
        logger.info(f"WebSocket连接加入频道 [{channel}], 当前连接数: {len(self._channels[channel])}")
        
        # 确保心跳任务运行
        if not self._running:
            asyncio.create_task(self.start_heartbeat())

    async def disconnect(self, channel: str, websocket: Any) -> None:
        """从频道移除WebSocket连接"""
        await self._disconnect_internal(channel, websocket)

    async def _disconnect_internal(self, channel: str, websocket: Any) -> None:
        """内部断开连接处理"""
        if channel in self._channels:
            self._channels[channel].discard(websocket)
        
        # 清理元数据
        if websocket in self._connection_meta:
            del self._connection_meta[websocket]
        
        logger.info(f"WebSocket连接离开频道 [{channel}], 当前连接数: {len(self._channels.get(channel, set()))}")

    async def broadcast(self, channel: str, message: Dict[str, Any]) -> None:
        """向指定频道的所有连接广播消息"""
        if channel not in self._channels:
            return

        import json
        dead_connections = set()

        for websocket in self._channels[channel]:
            try:
                await websocket.send_json(message)
            except Exception:
                dead_connections.add(websocket)

        # 清理断开的连接
        for ws in dead_connections:
            self._channels[channel].discard(ws)

    async def broadcast_all(self, message: Dict[str, Any]) -> None:
        """向所有频道广播消息"""
        for channel in self._channels:
            await self.broadcast(channel, message)

    def get_connection_count(self, channel: str) -> int:
        """获取频道连接数"""
        return len(self._channels.get(channel, set()))
