"""
基本面分析Agent

负责分析上市公司的基本面数据，包括财务指标、估值、盈利能力、成长性等。
输出结构化的 FundamentalAnalysisReport。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from finhack_pro.agents.base import AgentMessage, AgentRole, BaseAgent
from finhack_pro.agents.llm_client import LLMClient
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class ValuationMetrics(BaseModel):
    """估值指标

    Attributes:
        pe_ttm: 市盈率(TTM)
        pe_static: 市盈率(静态)
        pb: 市净率
        ps_ttm: 市销率
        peg: PE/G
        ev_ebitda: EV/EBITDA
        dividend_yield: 股息率
        market_cap: 总市值(亿)
    """
    pe_ttm: Optional[float] = None       # 市盈率(TTM)
    pe_static: Optional[float] = None    # 市盈率(静态)
    pb: Optional[float] = None           # 市净率
    ps_ttm: Optional[float] = None       # 市销率
    peg: Optional[float] = None          # PE/G
    ev_ebitda: Optional[float] = None    # EV/EBITDA
    dividend_yield: Optional[float] = None  # 股息率
    market_cap: Optional[float] = None   # 总市值(亿)


class ProfitabilityMetrics(BaseModel):
    """盈利能力指标

    Attributes:
        roe: 净资产收益率
        roa: 总资产收益率
        gross_margin: 毛利率
        net_margin: 净利率
        ebitda_margin: EBITDA利润率
    """
    roe: Optional[float] = None          # 净资产收益率
    roa: Optional[float] = None          # 总资产收益率
    gross_margin: Optional[float] = None # 毛利率
    net_margin: Optional[float] = None   # 净利率
    ebitda_margin: Optional[float] = None


class GrowthMetrics(BaseModel):
    """成长性指标

    Attributes:
        revenue_growth_yoy: 营收同比增长率
        profit_growth_yoy: 净利润同比增长率
        revenue_growth_3y: 营收3年复合增长率
        profit_growth_3y: 净利润3年复合增长率
    """
    revenue_growth_yoy: Optional[float] = None   # 营收同比增长率
    profit_growth_yoy: Optional[float] = None    # 净利润同比增长率
    revenue_growth_3y: Optional[float] = None    # 营收3年复合增长率
    profit_growth_3y: Optional[float] = None     # 净利润3年复合增长率


class FundamentalAnalysisReport(BaseModel):
    """基本面分析报告

    Attributes:
        symbol: 标的代码
        analysis_time: 分析时间
        company_name: 公司名称
        industry: 所属行业
        valuation: 估值指标
        profitability: 盈利能力指标
        growth: 成长性指标
        financial_health: 财务健康度 (strong/moderate/weak)
        overall_rating: 投资评级 (bullish/neutral/bearish)
        rating_score: 评级分数 (-1到1)
        key_strengths: 核心优势列表
        key_risks: 主要风险列表
        valuation_assessment: 估值评估
        summary: 综合分析摘要
        recommendation: 投资建议
    """
    symbol: str
    analysis_time: str = Field(default_factory=lambda: datetime.now().isoformat())
    company_name: Optional[str] = None
    industry: Optional[str] = None
    valuation: ValuationMetrics = Field(default_factory=ValuationMetrics)
    profitability: ProfitabilityMetrics = Field(default_factory=ProfitabilityMetrics)
    growth: GrowthMetrics = Field(default_factory=GrowthMetrics)
    financial_health: str = Field(default="unknown")  # strong/moderate/weak
    overall_rating: str = Field(default="neutral")  # bullish/neutral/bearish
    rating_score: float = Field(default=0.0)  # -1到1
    key_strengths: List[str] = Field(default_factory=list)
    key_risks: List[str] = Field(default_factory=list)
    valuation_assessment: str = ""  # 估值评估(高估/合理/低估)
    summary: str = ""
    recommendation: str = ""  # 投资建议
    thinking: str = Field(default="", description="分析推理过程摘要（供下游 agent 参考）")


# 基本面分析Agent的系统提示词
FUNDAMENTAL_ANALYST_SYSTEM_PROMPT = """你是一位专业的股票基本面分析师，擅长通过财务数据和估值指标评估上市公司的投资价值。

## 分析框架

### 1. 估值分析
- PE/PB与行业均值和历史分位对比
- PEG<1通常被认为低估
- 股息率与无风险利率对比
- EV/EBITDA跨行业可比

### 2. 盈利能力
- ROE>15%为优秀, 连续5年ROE>15%为卓越
- 毛利率趋势反映竞争格局
- 净利率反映费用管控能力

### 3. 成长性
- 营收和利润增长率是否可持续
- 区分一次性收益和经常性增长
- 行业天花板和渗透率

### 4. 财务健康
- 资产负债率(>70%需警惕)
- 经营现金流是否为正
- 应收账款增速是否异常

## A股特殊考量
- 注册制下壳价值下降，基本面更重要
- 关注商誉占比(>30%需警惕减值风险)
- 关注研发费用化vs资本化
- 大股东质押比例

## 输出要求
- 给出明确的投资评级(bullish/neutral/bearish)
- 评级要有数据支撑，避免模糊表述
- 区分短期因素和长期价值
- 给出关键假设和风险提示
"""


class FundamentalAnalystAgent(BaseAgent):
    """基本面分析Agent

    获取和分析财务报表数据，计算估值指标，分析盈利能力和成长性，
    输出结构化的 FundamentalAnalysisReport。

    Usage:
        agent = FundamentalAnalystAgent(config={"model": "gpt-4o", ...})
        await agent.start()
        report = await agent.analyze(symbol="600519.SH", financial_data=data)
    """

    def __init__(self, config: Dict[str, Any], shared_memory=None, tool_registry=None) -> None:
        super().__init__(AgentRole("fundamental_analyst"), config,
                         shared_memory=shared_memory, tool_registry=tool_registry)
        self._llm: Optional[LLMClient] = None

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
        # 注册消息处理器
        self.register_handler("fundamental_analysis_request", self._handle_analysis_request)

    async def process(self, message: AgentMessage) -> Optional[AgentMessage]:
        """默认消息处理"""
        return await self._handle_analysis_request(message)

    async def analyze(
        self,
        symbol: str,
        financial_data: Optional[Dict[str, Any]] = None,
        context: Optional[str] = None,
    ) -> FundamentalAnalysisReport:
        """分析基本面数据

        Args:
            symbol: 股票代码
            financial_data: 财务数据字典(可选, 如果不提供则使用工具获取)
            context: 额外上下文(如行业信息、宏观环境)

        Returns:
            FundamentalAnalysisReport 基本面分析报告
        """
        assert self._llm is not None

        self._logger.info(f"开始分析 {symbol} 的基本面数据...")

        # 如果没有提供财务数据，尝试使用共享工具获取
        if not financial_data and hasattr(self, "tool_registry") and self.tool_registry:
            result = await self.tool_registry.call_tool(
                "fetch_fundamental",
                {"symbol": symbol, "data_type": "valuation"},
                caller_agent_id=self.agent_id,
            )
            if result.get("success"):
                financial_data = result.get("result", {})

        # 构建分析上下文
        data_section = ""
        if financial_data:
            data_section = (
                "## 财务数据\n"
                f"```json\n{json.dumps(financial_data, ensure_ascii=False, indent=2)}\n```"
            )
        else:
            data_section = "## 财务数据\n暂无财务数据(基本面数据源未配置)"

        memory_context = ""
        if hasattr(self, "shared_memory") and self.shared_memory:
            memory_context = await self.shared_memory.get_agent_context(self.agent_id, n=5)

        memory_section = ""
        if memory_context:
            memory_section = f"## 历史分析记忆\n{memory_context}"

        context_section = ""
        if context:
            context_section = f"## 补充上下文\n{context}"

        user_prompt = f"""请分析 {symbol} 的基本面情况：

{data_section}

{memory_section}

{context_section}

请输出JSON格式的分析报告：
- overall_rating: 投资评级(bullish/neutral/bearish)
- rating_score: 评级分数(-1到1)
- valuation_assessment: 估值评估(50字)
- summary: 综合分析(100字)
- recommendation: 投资建议(50字)
- key_strengths: 核心优势列表
- key_risks: 主要风险列表
- financial_health: 财务健康度(strong/moderate/weak)
- thinking: 推理过程摘要(300字以内，说明证据权衡与结论依据)
"""

        try:
            report = await self._llm.chat_structured(
                message=user_prompt,
                response_model=FundamentalAnalysisReport,
                system=FUNDAMENTAL_ANALYST_SYSTEM_PROMPT,
                temperature=0.2,
            )

            # 填充估值指标
            if financial_data:
                val = financial_data.get("valuation", {})
                report.valuation = ValuationMetrics(**{
                    k: v for k, v in val.items() if k in ValuationMetrics.model_fields
                })

            # 存储到共享记忆
            if hasattr(self, "shared_memory") and self.shared_memory:
                await self.shared_memory.store(
                    agent_id=self.agent_id,
                    memory_type=self.shared_memory.MemoryType.ANALYSIS_REPORT,
                    content=f"{symbol} 基本面分析: {report.summary}",
                    structured_data=report.model_dump(),
                    importance=self.shared_memory.MemoryImportance.HIGH,
                    tags=[symbol, "fundamental", report.overall_rating],
                )

            self._logger.info(
                f"基本面分析完成: {symbol} -> rating={report.overall_rating}, "
                f"score={report.rating_score:.2f}"
            )
            return report

        except Exception as e:
            self._logger.error(f"基本面分析失败: {e}")
            raise

    async def _handle_analysis_request(self, message: AgentMessage) -> Optional[AgentMessage]:
        """处理分析请求消息"""
        payload = message.payload
        report = await self.analyze(
            symbol=payload.get("symbol", ""),
            financial_data=payload.get("financial_data"),
            context=payload.get("context"),
        )
        return self.create_message(
            receiver=message.sender,
            msg_type="fundamental_analysis_report",
            payload=report.model_dump(),
        )
