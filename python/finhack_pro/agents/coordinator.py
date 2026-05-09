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
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

from finhack_pro.agents.base import AgentMessage, BaseAgent
from finhack_pro.agents.fundamental_analyst import FundamentalAnalystAgent
from finhack_pro.agents.market_analyzer import MarketAnalyzerAgent
from finhack_pro.agents.micro_event_agent import MicroEventAgent
from finhack_pro.agents.news_analyst import NewsAnalystAgent
from finhack_pro.agents.risk_manager import RiskManagerAgent
from finhack_pro.agents.shared_memory import SharedMemory
from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent
from finhack_pro.agents.tool_registry import ToolRegistry, create_default_toolkit
from finhack_pro.agents.trade_executor import TradeExecutorAgent
from finhack_pro.agents.alternative_data_tools import register_alternative_data_tools
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


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
        self._logger = get_logger("coordinator")

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

        def _merge_config(agent_specific: Dict[str, Any]) -> Dict[str, Any]:
            """合并LLM全局配置和Agent专属配置"""
            merged = {**llm_config, **agent_specific}
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

        按顺序初始化并启动每个Agent，如果某个Agent启动失败则抛出异常。
        """
        self._logger.info("正在启动Agent协调器...")

        # 启动所有Agent
        for name, agent in self._agents.items():
            try:
                await agent.start()
                self._logger.info(f"Agent [{name}] 启动成功")
            except Exception as e:
                self._logger.error(f"Agent [{name}] 启动失败: {e}")
                raise

        self._running = True
        self._logger.info("Agent协调器启动完成，所有Agent就绪")

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
                self._logger.info(f"Agent [{name}] 已停止")
            except Exception as e:
                self._logger.error(f"Agent [{name}] 停止失败: {e}")

        self._running = False
        self._logger.info("Agent协调器已停止")

    # ============================================================
    # 分析流水线
    # ============================================================

    async def run_analysis_pipeline(
        self,
        symbol: str,
        market_data: Optional[Dict[str, Any]] = None,
        indicators: Optional[Dict[str, Any]] = None,
        current_price: Optional[float] = None,
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

        每一步的输出都存储到共享记忆中。

        Args:
            symbol: 标的代码
            market_data: 市场数据
            indicators: 技术指标
            current_price: 当前价格

        Returns:
            包含各阶段结果的字典
        """
        self._logger.info(f"========== 开始分析流水线: {symbol} ==========")
        result: Dict[str, Any] = {"symbol": symbol}

        try:
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

            # 并行执行4个分析任务 (SharedMemory内部有asyncio.Lock保护并发写入)
            analysis_tasks = [
                asyncio.create_task(_run_market_analysis(), name="market"),
                asyncio.create_task(_run_news_analysis(), name="news"),
                asyncio.create_task(_run_fundamental_analysis(), name="fundamental"),
                asyncio.create_task(_run_micro_event_analysis(), name="micro_event"),
            ]

            # 收集结果，单个任务失败不影响其他
            analysis_results = {}
            for task in analysis_tasks:
                try:
                    report = await task
                    analysis_results[task.get_name()] = report
                except Exception as e:
                    self._logger.error(f"分析任务 [{task.get_name()}] 失败: {e}")
                    analysis_results[task.get_name()] = None

            analysis_report = analysis_results.get("market")
            news_report = analysis_results.get("news")
            fundamental_report = analysis_results.get("fundamental")
            micro_event_report = analysis_results.get("micro_event")

            # 记录结果
            if analysis_report:
                result["analysis"] = analysis_report.model_dump()
                self._logger.info(
                    f"[Step 1/7] 市场分析完成: 状态={analysis_report.market_state.value}, "
                    f"趋势={analysis_report.trend_direction.value}"
                )
            else:
                self._logger.warning("[Step 1/7] 市场分析失败，使用空报告")
                result["analysis"] = None

            if news_report:
                result["news_analysis"] = news_report.model_dump()
                self._logger.info(
                    f"[Step 2/7] 新闻分析完成: 情感={news_report.overall_sentiment}, "
                    f"分数={news_report.sentiment_score:.2f}"
                )
            else:
                self._logger.warning("[Step 2/7] 新闻分析失败，使用空报告")
                result["news_analysis"] = None

            if fundamental_report:
                result["fundamental_analysis"] = fundamental_report.model_dump()
                self._logger.info(
                    f"[Step 3/7] 基本面分析完成: 评级={fundamental_report.overall_rating}, "
                    f"分数={fundamental_report.rating_score:.2f}"
                )
            else:
                self._logger.warning("[Step 3/7] 基本面分析失败，使用空报告")
                result["fundamental_analysis"] = None

            if micro_event_report:
                result["micro_event_analysis"] = micro_event_report.model_dump()
                self._logger.info(
                    f"[Step 4/7] 微观事件分析完成: 发现{micro_event_report.events_count}个事件, "
                    f"情绪变化={micro_event_report.sentiment_shift}"
                )
            else:
                self._logger.warning("[Step 4/7] 微观事件分析失败，使用空报告")
                result["micro_event_analysis"] = None

            self._logger.info("[Phase 1] 并行分析阶段完成")

            # ---- 第5步: 策略生成(多空辩论) ----
            self._logger.info("[Step 5/7] 策略生成(多空辩论)...")
            strategy_signal = await self._generate_strategy_with_debate(
                symbol=symbol,
                analysis_report=analysis_report,
                news_report=news_report,
                fundamental_report=fundamental_report,
                micro_event_report=micro_event_report,
                current_price=current_price,
            )
            result["signal"] = strategy_signal.model_dump()
            self._logger.info(
                f"策略生成完成: 方向={strategy_signal.direction.value}, "
                f"置信度={strategy_signal.confidence:.2f}"
            )

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

            # 如果方向是HOLD，直接结束
            if strategy_signal.direction.value == "hold":
                self._logger.info("策略信号为HOLD，流水线结束")
                result["risk_decision"] = None
                result["execution"] = None
                return result

            # ---- 第6步: 风控审批 ----
            self._logger.info("[Step 6/7] 风控审批...")
            risk_decision = await self.risk_manager.evaluate_risk(
                signal=strategy_signal,
            )
            result["risk_decision"] = risk_decision.model_dump()
            self._logger.info(
                f"风控审批完成: {'通过' if risk_decision.approved else '拒绝'}"
            )

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
                return result

            # ---- 第7步: 交易执行 ----
            self._logger.info("[Step 7/7] 交易执行...")
            execution_report = await self.trade_executor.execute(
                signal=strategy_signal,
                decision=risk_decision,
                current_price=current_price,
            )
            result["execution"] = execution_report.model_dump()
            self._logger.info(
                f"交易执行完成: 状态={execution_report.status}, "
                f"成交={execution_report.filled_volume}股"
            )

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

        except Exception as e:
            self._logger.error(f"分析流水线异常: {e}", exc_info=True)
            result["error"] = str(e)

            # 记录异常到共享记忆
            await self.shared_memory.store(
                agent_id="coordinator",
                memory_type=self.shared_memory.MemoryType.SYSTEM_EVENT,
                content=f"{symbol} 分析流水线异常: {str(e)}",
                importance=self.shared_memory.MemoryImportance.HIGH,
                tags=[symbol, "error", "pipeline"],
            )

        self._logger.info(f"========== 分析流水线完成: {symbol} ==========")
        return result

    async def _generate_strategy_with_debate(
        self,
        symbol: str,
        analysis_report: Any,
        news_report: Any,
        fundamental_report: Any,
        micro_event_report: Any = None,
        current_price: Optional[float] = None,
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
        """将DataFrame转换为市场数据字典

        Args:
            df: pandas DataFrame

        Returns:
            市场数据字典
        """
        try:
            import pandas as pd

            recent_bars = []
            for _, row in df.tail(10).iterrows():
                bar = {
                    "date": str(row.get("date", row.name)),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)),
                }
                recent_bars.append(bar)

            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else last
            current = {
                "close": float(last.get("close", 0)),
                "change_pct": float(
                    (last.get("close", 0) - prev.get("close", 0))
                    / max(prev.get("close", 1), 0.01)
                    * 100
                ),
            }

            return {"recent_bars": recent_bars, "current": current}

        except Exception:
            return {}
