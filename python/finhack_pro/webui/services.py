"""
业务逻辑服务层

提供配置管理、回测任务管理、Agent系统交互、记忆查询、WebSocket广播等业务逻辑。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
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
    DataSourceTestResult,
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
        self._config_path = config_path or self._resolve_default_config_path()
        self._config = get_config(self._config_path, force_reload=True)

    @staticmethod
    def _resolve_default_config_path() -> Optional[str]:
        """解析默认配置文件路径

        优先级：FINHACK_CONFIG 环境变量（显式指定即优先，文件不存在时
        由 save_config 首次创建）→ 当前工作目录 config/default.yaml
        （与 save_config 的默认写入路径一致，保证保存/重启后读回同一份）。
        """
        env_path = os.environ.get("FINHACK_CONFIG")
        if env_path:
            return env_path
        cwd_path = Path.cwd() / "config" / "default.yaml"
        if cwd_path.exists():
            return str(cwd_path)
        return None

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置(隐藏敏感字段)"""
        data = self._config.model_dump()
        return self._mask_sensitive(data)

    def get_full_config(self) -> Dict[str, Any]:
        """获取完整配置(包含敏感字段，用于编辑)"""
        return self._config.model_dump(exclude_none=True)

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """更新配置

        Args:
            updates: 配置更新字典，支持 llm/data/risk/execution/agents 子配置

        Returns:
            更新后的配置
        """
        config_dict = self._config.model_dump()

        # agents 段优先处理：per-Agent LLM 配置覆盖（dict 整体替换/合并）
        if isinstance(updates.get("agents"), dict):
            agents = config_dict.get("agents") or {}
            for agent_name, agent_cfg in updates["agents"].items():
                if not isinstance(agent_cfg, dict) or not agent_cfg:
                    # 空配置 = 清除该 Agent 的覆盖（跟随全局）
                    agents.pop(agent_name, None)
                    continue
                merged = dict(agents.get(agent_name) or {})
                for k, v in agent_cfg.items():
                    if v is not None and v != "":
                        merged[k] = v
                agents[agent_name] = merged
            config_dict["agents"] = agents

        for section, values in updates.items():
            if section == "agents":
                continue
            # 兼容前端字段名：execution → backtest（后端顶层段名）
            target_section = "backtest" if section == "execution" else section
            if values and target_section in config_dict:
                if isinstance(config_dict[target_section], dict):
                    for key, value in values.items():
                        if value is not None:
                            config_dict[target_section][key] = value

        # 重新创建配置对象
        # 注意：reset_config() 会清空全局单例，但这里必须把新配置同步回全局单例，
        # 否则 strategy_routes._call_llm 等模块通过 get_config() 读到的仍是空配置，
        # 导致"已填写 API Key 仍提示请设置 API Key"。
        from finhack_pro.config import set_global_config
        reset_config()
        self._config = FinhackProConfig(**config_dict)
        set_global_config(self._config)
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
        protocol: str = "openai",
    ) -> ConnectionTestResult:
        """测试API连接（协议驱动）

        Args:
            provider: 服务商名称（仅回显，不参与路由）
            api_key: API密钥(可选，为None时使用当前配置)
            base_url: 自定义API地址
            protocol: 连接协议 openai / anthropic（未知按 openai 处理）

        Returns:
            连接测试结果
        """
        start_time = time.time()

        try:
            if protocol == "anthropic":
                return await self._test_anthropic(provider, api_key, base_url, start_time)
            # 默认 openai 协议：任意 base_url / 未知 provider 均尝试 GET {base_url}/models
            return await self._test_openai(provider, api_key, base_url, start_time)
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return ConnectionTestResult(
                provider=provider,
                success=False,
                message=f"连接异常: {str(e)}",
                latency_ms=round(latency, 2),
            )

    async def _test_openai(
        self, provider: str, api_key: Optional[str], base_url: Optional[str], start_time: float
    ) -> ConnectionTestResult:
        """测试 OpenAI 兼容端点连接（任意服务商，只测 GET /models 不传模型名）"""
        import httpx

        key = api_key or self._config.llm.openai_api_key
        url = base_url or self._config.llm.openai_base_url
        if not key:
            return ConnectionTestResult(
                provider=provider,
                success=False,
                message="OpenAI API Key 未配置",
            )
        if not url:
            return ConnectionTestResult(
                provider=provider,
                success=False,
                message="Base URL 未配置：请在 API 基础地址输入框填写（如 https://api.deepseek.com/v1）",
            )

        models_url = f"{url.rstrip('/')}/models"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    models_url,
                    headers={"Authorization": f"Bearer {key}"},
                )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return ConnectionTestResult(
                provider=provider,
                success=False,
                message=f"连接异常: {type(e).__name__}: {e}（目标: GET {models_url}）",
                latency_ms=round(latency, 2),
            )

        latency = (time.time() - start_time) * 1000

        if resp.status_code == 200:
            models = resp.json().get("data", [])
            model_names = [m["id"] for m in models[:5]]
            return ConnectionTestResult(
                provider=provider,
                success=True,
                message=f"{provider} 连接成功，可用模型: {', '.join(model_names)}",
                latency_ms=round(latency, 2),
            )
        else:
            return ConnectionTestResult(
                provider=provider,
                success=False,
                message=f"API返回错误: {resp.status_code} {resp.text[:200]}（请求: GET {models_url}）",
                latency_ms=round(latency, 2),
            )

    async def _test_anthropic(
        self, provider: str, api_key: Optional[str], base_url: Optional[str], start_time: float
    ) -> ConnectionTestResult:
        """测试 Anthropic 协议连接（支持自定义 base_url）"""
        import httpx

        key = api_key or self._config.llm.anthropic_api_key
        if not key:
            return ConnectionTestResult(
                provider=provider,
                success=False,
                message="Anthropic API Key 未配置",
            )

        url = self._normalize_anthropic_url(base_url)

        # Anthropic没有简单的list models端点，发送一个最小请求测试
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
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
                    provider=provider,
                    success=True,
                    message=f"{provider} 连接成功",
                    latency_ms=round(latency, 2),
                )
            else:
                return ConnectionTestResult(
                    provider=provider,
                    success=False,
                    message=f"API返回错误: {resp.status_code} {resp.text[:200]}",
                    latency_ms=round(latency, 2),
                )

    @staticmethod
    def _normalize_anthropic_url(base_url: Optional[str]) -> str:
        """归一化 Anthropic base_url 到完整 /v1/messages 端点"""
        url = (base_url or "https://api.anthropic.com").rstrip("/")
        if url.endswith("/v1/messages"):
            return url
        if url.endswith("/v1"):
            return f"{url}/messages"
        return f"{url}/v1/messages"

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


class DataSourceTester:
    """数据源连接测试（akshare / tushare）——独立于 LLM 协议测试"""

    def __init__(self, config: Optional[FinhackProConfig] = None):
        self._config = config or get_config()

    async def test_connection(
        self,
        source: str,
        tushare_token: Optional[str] = None,
    ) -> DataSourceTestResult:
        """测试数据源连通性

        Args:
            source: akshare / tushare
            tushare_token: tushare token（可选，为None时用当前配置）

        Returns:
            数据源测试结果
        """
        start_time = time.time()
        try:
            if source == "tushare":
                return await self._test_tushare(tushare_token, start_time)
            if source == "akshare":
                return await self._test_akshare(start_time)
            return DataSourceTestResult(
                source=source,
                success=False,
                message=f"不支持的数据源: {source}",
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return DataSourceTestResult(
                source=source,
                success=False,
                message=f"连接异常: {str(e)}",
                latency_ms=round(latency, 2),
            )

    async def _test_tushare(
        self, token: Optional[str], start_time: float
    ) -> DataSourceTestResult:
        """测试 Tushare 连接（真实 trade_cal 业务调用）"""
        key = token or self._config.data.tushare_token
        if not key:
            return DataSourceTestResult(
                source="tushare",
                success=False,
                message="Tushare Token 未配置",
            )

        def _probe() -> Any:
            import tushare as ts

            ts.set_token(key)
            pro = ts.pro_api()
            return pro.trade_cal(exchange="SSE", start_date="20240101", end_date="20240110")

        try:
            df = await asyncio.to_thread(_probe)
            latency = (time.time() - start_time) * 1000

            if df is not None and len(df) > 0:
                return DataSourceTestResult(
                    source="tushare",
                    success=True,
                    message=f"连接成功，获取到 {len(df)} 条交易日历数据",
                    latency_ms=round(latency, 2),
                )
            return DataSourceTestResult(
                source="tushare",
                success=False,
                message="连接成功但未获取到数据，请检查Token权限",
                latency_ms=round(latency, 2),
            )
        except ImportError:
            return DataSourceTestResult(
                source="tushare",
                success=False,
                message="tushare包未安装，请执行: pip install tushare",
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return DataSourceTestResult(
                source="tushare",
                success=False,
                message=f"Tushare连接失败: {str(e)}",
                latency_ms=round(latency, 2),
            )

    async def _test_akshare(self, start_time: float) -> DataSourceTestResult:
        """测试 AkShare 连接（真实网络探测：轻量单标的日线）"""
        try:
            import akshare as ak

            def _probe() -> Any:
                import socket

                socket.setdefaulttimeout(10)  # 防挂起
                today = datetime.now().strftime("%Y%m%d")
                start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
                return ak.stock_zh_a_hist(
                    symbol="600519",
                    period="daily",
                    start_date=start,
                    end_date=today,
                )

            df = await asyncio.to_thread(_probe)
            latency = (time.time() - start_time) * 1000

            if df is not None and len(df) > 0:
                return DataSourceTestResult(
                    source="akshare",
                    success=True,
                    message=f"连接成功，获取到 {len(df)} 条行情数据",
                    latency_ms=round(latency, 2),
                )
            return DataSourceTestResult(
                source="akshare",
                success=False,
                message="连接成功但未获取到数据",
                latency_ms=round(latency, 2),
            )
        except ImportError:
            return DataSourceTestResult(
                source="akshare",
                success=False,
                message="akshare包未安装，请执行: pip install akshare",
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return DataSourceTestResult(
                source="akshare",
                success=False,
                message=f"AkShare连接失败: {str(e)}",
                latency_ms=round(latency, 2),
            )


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
            logger.info(f"[Backtest {task_id}] 开始执行真实回测: {request.symbols}")

            # 导入回测引擎和策略
            from finhack_pro.backtest.runner import BacktestRunner
            from finhack_pro.data.fetcher import DataFetcher
            from finhack_pro.strategies.dual_thrust import DualThrustStrategy

            # 获取市场数据（使用配置的数据源：akshare 失败可 fallback tushare）
            cfg = get_config()
            fetcher = DataFetcher(
                source=cfg.data.source,
                tushare_token=cfg.data.tushare_token,
                cache_dir=cfg.data.cache_dir,
            )
            symbol = request.symbols[0] if request.symbols else "000001.SZ"
            
            if stream_callback:
                await stream_callback({
                    "type": "backtest_progress",
                    "task_id": task_id,
                    "progress": 10,
                    "message": f"正在获取 {symbol} 历史数据...",
                })

            # 获取数据（失败即抛错，由外层 except 统一处理为任务失败）
            data = fetcher.get_daily(
                symbol=symbol,
                start_date=request.start_date,
                end_date=request.end_date,
            )
            if data is None or data.empty:
                raise ValueError(
                    f"无法获取 {symbol} 的数据：请检查网络连接、数据源可用性、"
                    f"标的代码是否正确（如 600519.SH）、日期区间是否有交易日"
                )

            if stream_callback:
                await stream_callback({
                    "type": "backtest_progress",
                    "task_id": task_id,
                    "progress": 30,
                    "message": f"获取到 {len(data)} 条数据，准备回测...",
                })

            # 创建策略实例
            strategy = DualThrustStrategy()
            if request.strategy_params:
                for key, value in request.strategy_params.items():
                    if hasattr(strategy, key):
                        setattr(strategy, key, value)

            # 创建回测运行器
            runner = BacktestRunner()

            if stream_callback:
                await stream_callback({
                    "type": "backtest_progress",
                    "task_id": task_id,
                    "progress": 50,
                    "message": "正在执行回测计算...",
                })

            # 执行回测
            import asyncio
            loop = asyncio.get_event_loop()
            backtest_result = await loop.run_in_executor(
                None,
                lambda: runner.run(
                    strategy=strategy,
                    symbol=symbol,
                    data=data,
                    initial_capital=request.initial_capital,
                    commission_rate=request.commission_rate,
                    stamp_tax_rate=request.stamp_tax_rate,
                    slippage=request.slippage,
                )
            )

            if stream_callback:
                await stream_callback({
                    "type": "backtest_progress",
                    "task_id": task_id,
                    "progress": 90,
                    "message": "回测完成，生成报告...",
                })

            # 转换结果格式
            total_bars = len(data)
            status.total_bars = total_bars
            status.current_bar = total_bars
            status.progress = 100.0

            # 构建权益曲线
            equity_curve = backtest_result.equity_curve
            if not equity_curve and backtest_result.daily_returns:
                equity = request.initial_capital
                equity_curve = []
                for i, ret in enumerate(backtest_result.daily_returns):
                    equity *= (1 + ret)
                    equity_curve.append({
                        "date": data['date'].iloc[i] if i < len(data) else str(i),
                        "equity": round(equity, 2),
                    })

            # 构建基准曲线
            benchmark_curve = []
            if len(data) > 0:
                start_price = data['close'].iloc[0]
                for i, row in data.iterrows():
                    benchmark_curve.append({
                        "date": row['date'],
                        "equity": round(request.initial_capital * (row['close'] / start_price), 2),
                    })

            # 转换交易记录
            trades = []
            for t in backtest_result.trades:
                trades.append(TradeRecord(
                    date=t.get('date', ''),
                    symbol=t.get('symbol', symbol),
                    direction=t.get('direction', 'buy'),
                    price=t.get('price', 0),
                    volume=t.get('volume', 0),
                    commission=t.get('commission', 0),
                    pnl=t.get('pnl', 0),
                    reason=t.get('reason', '策略信号'),
                ))

            metrics = BacktestMetrics(
                total_return=round(backtest_result.total_return * 100, 2),
                annual_return=round(backtest_result.annual_return * 100, 2),
                sharpe_ratio=round(backtest_result.sharpe_ratio, 2),
                sortino_ratio=0.0,  # 需要额外计算
                max_drawdown=round(backtest_result.max_drawdown * 100, 2),
                win_rate=round(backtest_result.win_rate * 100, 2),
                profit_loss_ratio=round(backtest_result.profit_loss_ratio, 2),
                total_trades=backtest_result.total_trades,
                final_equity=round(backtest_result.final_capital, 2),
            )

            result = BacktestResult(
                task_id=task_id,
                status="completed",
                metrics=metrics,
                equity_curve=equity_curve,
                trades=[t.model_dump() for t in trades],
                benchmark_curve=benchmark_curve,
            )

            logger.info(f"[Backtest {task_id}] 回测完成: 总收益 {metrics.total_return}%, 交易次数 {metrics.total_trades}")

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
        import json as _json

        # run_id 语义：调用方显式传入则复用（续跑/幂等重试），否则生成新 ID
        run_id = request.run_id or uuid.uuid4().hex[:12]
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
                logger.info(f"[Pipeline {run_id}] 开始执行真实分析流水线: {request.symbol}")

                # L5d：前端仅传 symbol 时 market_data=None，此处真实取数。
                # 数据源连接/获取失败会直接抛出，由下方 except 标为流水线失败（不盲跑、不伪造）。
                if request.market_data is None:
                    request.market_data = self._coordinator._fetch_real_market_data(request.symbol)
                    request.indicators = request.indicators or {}

                # 真正调用coordinator的分析流水线（透传市场数据与 run_id/resume）
                pipeline_result = await self._coordinator.run_analysis_pipeline(
                    symbol=request.symbol,
                    market_data=request.market_data,
                    indicators=request.indicators,
                    current_price=request.current_price,
                    run_id=request.run_id,
                    resume=request.resume,
                )

                logger.info(f"[Pipeline {run_id}] Coordinator流水线完成")

                # 将coordinator的7步结果映射为PipelineStepResult并推送
                step_mappings = [
                    (1, "市场分析(技术面)", "market_analyzer", "analysis"),
                    (2, "新闻社媒分析", "news_analyst", "news_analysis"),
                    (3, "基本面分析", "fundamental_analyst", "fundamental_analysis"),
                    (4, "微观事件分析", "micro_event_agent", "micro_event_analysis"),
                    (5, "策略生成(多空辩论)", "strategy_generator", "signal"),
                    (6, "风控审批", "risk_manager", "risk_decision"),
                    (7, "交易执行", "trade_executor", "execution"),
                ]

                for step_num, step_name, agent_id, result_key in step_mappings:
                    step_start = time.time()
                    step_result = PipelineStepResult(
                        step=step_num,
                        agent_name=step_name,
                        status="running",
                    )
                    result.steps.append(step_result)

                    # 推送"正在思考"状态
                    if stream_callback:
                        await stream_callback({
                            "type": "agent_thinking",
                            "run_id": run_id,
                            "step": step_num,
                            "agent_id": agent_id,
                            "agent_name": step_name,
                            "content": f"## {step_name}\n\n正在分析 {request.symbol} ...",
                        })

                    # 获取coordinator该步骤的真实结果
                    step_data = pipeline_result.get(result_key)

                    if step_data is not None:
                        step_result.status = "completed"
                        step_result.duration_ms = round((time.time() - step_start) * 1000, 2)

                        # 生成人类可读的摘要
                        summary = self._summarize_step(step_name, step_data)
                        step_result.summary = summary

                        # 推送真实的分析内容
                        if stream_callback:
                            content = self._format_step_content(step_name, step_data, step_result.duration_ms)
                            await stream_callback({
                                "type": "agent_thought",
                                "run_id": run_id,
                                "step": step_num,
                                "agent_id": agent_id,
                                "agent_name": step_name,
                                "content": content,
                                "duration_ms": step_result.duration_ms,
                            })
                        logger.info(f"[Pipeline {run_id}] Step {step_num}/7 {step_name} 完成")
                    else:
                        step_result.status = "skipped"
                        step_result.duration_ms = 0
                        step_result.summary = f"{step_name} 跳过(信号为HOLD或前置步骤无结果)"
                        logger.info(f"[Pipeline {run_id}] Step {step_num}/7 {step_name} 跳过")

                # 设置最终信号（仅在流水线真实产出信号时填充，禁止伪造 hold/0.5）
                signal_data = pipeline_result.get("signal")
                if signal_data:
                    result.final_signal = {
                        "direction": signal_data.get("direction", "hold"),
                        "confidence": signal_data.get("confidence", 0.0),
                        "reason": signal_data.get("reasoning", ""),
                    }
                else:
                    result.final_signal = None

                # 真实映射流水线状态：coordinator 返回 error 即失败（失败即终止，不伪造完成）
                if pipeline_result.get("error"):
                    result.status = "failed"
                    result.error = pipeline_result["error"]
                    if not any(s.status == "failed" for s in result.steps):
                        result.steps.append(PipelineStepResult(
                            step=len(result.steps) + 1,
                            agent_name="流水线",
                            status="failed",
                            duration_ms=0,
                            summary=f"分析失败: {pipeline_result['error'][:200]}",
                        ))
                    if stream_callback:
                        await stream_callback({
                            "type": "pipeline_error",
                            "run_id": run_id,
                            "error": pipeline_result["error"],
                        })
                else:
                    result.status = "completed"
                result.end_time = datetime.now().isoformat()
                logger.info(f"[Pipeline {run_id}] 流水线状态: {result.status}, final_signal={result.final_signal}")

            except Exception as e:
                result.status = "failed"
                result.error = str(e)
                result.end_time = datetime.now().isoformat()
                logger.error(f"[Pipeline {run_id}] 流水线执行失败: {e}", exc_info=True)

                # 记录失败步骤，便于前端日志面板展示
                if not any(s.status == "failed" for s in result.steps):
                    fail_step = PipelineStepResult(
                        step=len(result.steps) + 1,
                        agent_name="流水线",
                        status="failed",
                        duration_ms=0,
                        summary=f"流水线执行失败: {str(e)[:200]}",
                    )
                    result.steps.append(fail_step)

                if stream_callback:
                    await stream_callback({
                        "type": "agent_thought",
                        "run_id": run_id,
                        "step": len(result.steps),
                        "agent_id": "system",
                        "agent_name": "流水线",
                        "content": f"## ❌ 流水线执行失败\n\n**错误信息**\n\n```\n{str(e)}\n```\n\n请检查 API Key 配置后重试。",
                        "duration_ms": 0,
                    })
                    await stream_callback({
                        "type": "pipeline_error",
                        "run_id": run_id,
                        "error": str(e),
                    })
        else:
            # 无coordinator: 明确告知用户需要配置API
            logger.warning(f"[Pipeline {run_id}] 无coordinator，无法执行真实分析")

            # 记录一条系统步骤，让前端日志面板能看到原因
            sys_step = PipelineStepResult(
                step=1,
                agent_name="系统",
                status="failed",
                duration_ms=0,
                summary="未配置LLM API，无法执行分析",
            )
            result.steps.append(sys_step)

            if stream_callback:
                await stream_callback({
                    "type": "agent_thinking",
                    "run_id": run_id,
                    "step": 0,
                    "agent_id": "system",
                    "agent_name": "系统提示",
                    "content": "## ⚠️ 未配置LLM API\n\n请在「API配置」页面填写 API Key 和 Base URL，\n然后点击「测试连接」确认可用。\n\n配置完成后重新运行分析流水线。",
                })
                await stream_callback({
                    "type": "agent_thought",
                    "run_id": run_id,
                    "step": 0,
                    "agent_id": "system",
                    "agent_name": "系统提示",
                    "content": "## ⚠️ 未配置LLM API\n\n请在「API配置」页面填写 API Key 和 Base URL，\n然后点击「测试连接」确认可用。\n\n配置完成后重新运行分析流水线。",
                    "duration_ms": 0,
                })

            result.status = "failed"
            result.error = "未配置LLM API，无法执行分析。请在API配置页面填写API Key。"
            result.end_time = datetime.now().isoformat()

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
            "error": result.error,
            "steps_completed": len([s for s in result.steps if s.status == "completed"]),
            "steps_total": len(result.steps),
            "steps": [s.model_dump() for s in result.steps],
            "final_signal": result.final_signal,
            "start_time": result.start_time,
            "end_time": result.end_time,
        })

        # 清理运行中的记录
        self._running_pipelines.pop(run_id, None)

        return result

    def _summarize_step(self, step_name: str, data: Any) -> str:
        """从步骤数据中提取摘要"""
        try:
            if step_name == "市场分析(技术面)":
                return f"趋势: {data.get('trend_direction', 'N/A')}, 状态: {data.get('market_state', 'N/A')}"
            elif step_name == "新闻社媒分析":
                return f"情感: {data.get('overall_sentiment', 'N/A')}, 分数: {data.get('sentiment_score', 0):.2f}"
            elif step_name == "基本面分析":
                return f"评级: {data.get('overall_rating', 'N/A')}, 分数: {data.get('rating_score', 0):.2f}"
            elif step_name == "微观事件分析":
                return f"事件数: {data.get('events_count', 0)}, 情绪变化: {data.get('sentiment_shift', 'N/A')}"
            elif step_name == "策略生成(多空辩论)":
                return f"方向: {data.get('direction', 'N/A')}, 置信度: {data.get('confidence', 0):.2f}"
            elif step_name == "风控审批":
                return "通过" if data.get("approved") else f"拒绝: {data.get('reasoning', '')[:50]}"
            elif step_name == "交易执行":
                return f"动作: {data.get('action', 'N/A')}, 价格: {data.get('price', 'N/A')}"
            return f"{step_name} 完成"
        except Exception:
            return f"{step_name} 完成"

    def _format_step_content(self, step_name: str, data: Any, duration_ms: float) -> str:
        """将步骤数据格式化为可读的思考内容"""
        try:
            import json as _json
            content = f"## {step_name}\n\n"
            content += f"**耗时**: {duration_ms:.0f}ms\n\n"

            if step_name == "策略生成(多空辩论)":
                content += f"**方向**: {data.get('direction', 'N/A')}\n"
                content += f"**置信度**: {data.get('confidence', 0):.2f}\n"
                content += f"**理由**: {data.get('reasoning', 'N/A')}\n"
            elif step_name == "风控审批":
                content += f"**结果**: {'✅ 通过' if data.get('approved') else '❌ 拒绝'}\n"
                content += f"**理由**: {data.get('reasoning', 'N/A')}\n"
            elif step_name == "交易执行":
                content += f"**动作**: {data.get('action', 'N/A')}\n"
                content += f"**价格**: {data.get('price', 'N/A')}\n"
                content += f"**数量**: {data.get('quantity', 'N/A')}\n"
            else:
                # 其他步骤：输出结构化数据的可读版本
                for key, value in data.items():
                    if key not in ("raw_data", "metadata"):
                        content += f"**{key}**: {value}\n"
            return content
        except Exception:
            return f"## {step_name}\n\n分析完成，耗时 {duration_ms:.0f}ms"

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
