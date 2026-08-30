"""
共享工具集系统 - ToolRegistry
统一的工具注册中心，所有Agent共享同一套工具集
支持LLM Function Calling自动集成
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 关键词情感词典（与 AnalyzeSentimentTool 保持一致）
_POSITIVE_WORDS = ["增长", "上涨", "突破", "超预期", "利好", "盈利", "回升", "强势", "创新高", "增持"]
_NEGATIVE_WORDS = ["下跌", "亏损", "下滑", "不及预期", "利空", "减持", "风险", "暴跌", "制裁", "调查"]


def _summarize_return(result: Any, max_len: int = 300) -> str:
    """工具返回值摘要（阶段4）：dict/list 取关键信息并截断，防落盘体积爆炸"""
    try:
        if result is None:
            return ""
        if isinstance(result, dict):
            # 常见大数据容器：只保留结构摘要
            for key in ("data", "records", "rows", "items"):
                if isinstance(result.get(key), list):
                    return f"{key}[{len(result[key])}条] 首条: {str(result[key][0])[:200] if result[key] else '空'}"
            parts = []
            for k, v in list(result.items())[:8]:
                if isinstance(v, (dict, list)):
                    parts.append(f"{k}={type(v).__name__}({len(v)})")
                else:
                    parts.append(f"{k}={v}")
            return ", ".join(parts)[:max_len]
        if isinstance(result, list):
            return f"list[{len(result)}] 首条: {str(result[0])[:200] if result else '空'}"
        return str(result)[:max_len]
    except Exception:
        return type(result).__name__


def _classify_sentiment(text: str) -> str:
    """基于关键词的简单情感分类，与 AnalyzeSentimentTool 保持一致的判定规则。"""
    score = 0
    for word in _POSITIVE_WORDS:
        if word in text:
            score += 1
    for word in _NEGATIVE_WORDS:
        if word in text:
            score -= 1
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"


def _normalize_stock_code(keyword: str) -> Optional[str]:
    """从关键词（股票代码或名称）中提取 6 位 A 股代码。"""
    if not keyword:
        return None
    # 优先匹配 6 位连续数字（A 股代码）
    m = re.search(r"\b(\d{6})\b", str(keyword))
    if m:
        return m.group(1)
    return None


class ToolCategory(str, Enum):
    """工具分类"""
    DATA_FETCH = "data_fetch"           # 数据获取
    TECHNICAL_ANALYSIS = "technical"    # 技术分析
    FUNDAMENTAL = "fundamental"         # 基本面分析
    NEWS_SENTIMENT = "news_sentiment"   # 新闻/舆情
    RISK_MANAGEMENT = "risk"            # 风险管理
    EXECUTION = "execution"             # 交易执行
    UTILITY = "utility"                 # 通用工具


@dataclass
class ToolParameter:
    """工具参数定义"""
    name: str
    type: str               # "string", "number", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None


@dataclass
class ToolDefinition:
    """工具定义(元数据)"""
    name: str
    description: str
    category: ToolCategory
    parameters: List[ToolParameter] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    version: str = "1.0"
    agent_roles: Optional[List[str]] = None  # 限制哪些Agent可以使用，None=所有Agent

    def to_openai_function(self) -> Dict[str, Any]:
        """转换为OpenAI Function Calling格式"""
        properties = {}
        required = []
        for param in self.parameters:
            prop: Dict[str, Any] = {"type": param.type, "description": param.description}
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_anthropic_tool(self) -> Dict[str, Any]:
        """转换为Anthropic Tool格式"""
        properties = {}
        required = []
        for param in self.parameters:
            prop: Dict[str, Any] = {"type": param.type, "description": param.description}
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


class BaseTool(ABC):
    """工具基类，所有工具需继承此类"""

    def __init__(self):
        self._definition: Optional[ToolDefinition] = None

    @abstractmethod
    def define(self) -> ToolDefinition:
        """定义工具的元数据(名称、描述、参数)"""
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """执行工具"""
        pass

    @property
    def definition(self) -> ToolDefinition:
        if self._definition is None:
            self._definition = self.define()
        return self._definition

    def validate_args(self, args: Dict[str, Any]) -> List[str]:
        """验证参数"""
        errors = []
        for param in self.definition.parameters:
            if param.required and param.name not in args:
                if param.default is None:
                    errors.append(f"缺少必需参数: {param.name}")
        return errors


class ToolRegistry:
    """
    工具注册中心
    - 注册/发现/调用工具
    - 按分类和角色过滤
    - 自动生成LLM Function Calling工具列表
    - 工具调用日志
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._call_log: List[Dict[str, Any]] = []
        # 阶段8 数据绑定：自增证据 id 计数器（每条工具调用分配 ev_N）
        self._evidence_counter: int = 0

    def register(self, tool: BaseTool) -> None:
        """注册工具"""
        name = tool.definition.name
        if name in self._tools:
            logger.warning(f"[ToolRegistry] 工具 '{name}' 已存在，将被覆盖")
        self._tools[name] = tool
        logger.info(f"[ToolRegistry] 注册工具: {name} ({tool.definition.category.value})")

    def register_batch(self, tools: List[BaseTool]) -> None:
        """批量注册工具"""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> bool:
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """获取工具"""
        return self._tools.get(name)

    def list_tools(
        self,
        category: Optional[ToolCategory] = None,
        agent_role: Optional[str] = None,
    ) -> List[ToolDefinition]:
        """列出工具定义"""
        results = []
        for tool in self._tools.values():
            defn = tool.definition
            if category and defn.category != category:
                continue
            if agent_role and defn.agent_roles and agent_role not in defn.agent_roles:
                continue
            results.append(defn)
        return results

    def get_openai_tools(
        self,
        category: Optional[ToolCategory] = None,
        agent_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取OpenAI Function Calling格式的工具列表"""
        definitions = self.list_tools(category=category, agent_role=agent_role)
        return [d.to_openai_function() for d in definitions]

    def get_anthropic_tools(
        self,
        category: Optional[ToolCategory] = None,
        agent_role: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取Anthropic Tool格式的工具列表"""
        definitions = self.list_tools(category=category, agent_role=agent_role)
        return [d.to_anthropic_tool() for d in definitions]

    async def call_tool(self, tool_name: str, args: Dict[str, Any], caller_agent_id: str = "system", run_id: Optional[str] = None) -> Dict[str, Any]:
        """调用工具

        Args:
            tool_name: 工具名
            args: 参数
            caller_agent_id: 调用方 Agent
            run_id: 流水线 run_id（阶段4：工具调用与 run 关联，供决策报告落盘）
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return {"success": False, "error": f"工具 '{tool_name}' 不存在"}

        # 验证参数
        errors = tool.validate_args(args)
        if errors:
            return {"success": False, "error": f"参数验证失败: {'; '.join(errors)}"}

        # 角色权限检查
        if tool.definition.agent_roles and caller_agent_id not in tool.definition.agent_roles:
            return {"success": False, "error": f"Agent '{caller_agent_id}' 无权使用工具 '{tool_name}'"}

        try:
            result = await tool.execute(**args)
            self._evidence_counter += 1
            log_entry = {
                "tool_name": tool_name,
                "caller": caller_agent_id,
                "args": {k: str(v) for k, v in args.items()},
                "success": True,
                "run_id": run_id,
                "return_summary": _summarize_return(result),
                # 阶段8：证据 id，辩论环节 prompt 引用，后处理校验引用有效性
                "evidence_id": f"ev_{self._evidence_counter}",
                "timestamp": __import__("datetime").datetime.now().isoformat(),
            }
            self._call_log.append(log_entry)
            logger.info(f"[ToolRegistry] {caller_agent_id} 调用 {tool_name} 成功")
            return {"success": True, "result": result, "evidence_id": log_entry["evidence_id"]}
        except Exception as e:
            logger.error(f"[ToolRegistry] 工具调用失败 {tool_name}: {e}")
            return {"success": False, "error": str(e)}

    def persist(self, run_dir: str, run_id: Optional[str] = None) -> str:
        """把工具调用日志落盘到 run 目录（阶段4：决策报告数据源）

        Args:
            run_dir: 流水线 run 目录
            run_id: 只写该 run 的调用（None 写全量）

        Returns:
            落盘文件路径；失败返回 ''
        """
        try:
            import json as _json
            import os as _os

            _os.makedirs(run_dir, exist_ok=True)
            entries = [e for e in self._call_log if run_id is None or e.get("run_id") == run_id]
            path = _os.path.join(run_dir, "tool_calls.json")
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(entries, f, ensure_ascii=False, indent=2)
            return path
        except Exception as e:
            logger.warning(f"[ToolRegistry] 调用日志落盘失败: {e}")
            return ""

    def get_call_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取工具调用日志"""
        return self._call_log[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """获取工具统计"""
        call_counts: Dict[str, int] = {}
        for log in self._call_log:
            name = log["tool_name"]
            call_counts[name] = call_counts.get(name, 0) + 1
        return {
            "total_tools": len(self._tools),
            "total_calls": len(self._call_log),
            "call_counts": call_counts,
            "categories": list(set(t.definition.category.value for t in self._tools.values())),
        }


# ============================================================
# 内置工具实现
# ============================================================

class FetchMarketDataTool(BaseTool):
    """获取市场行情数据"""

    def __init__(self, data_fetcher=None):
        super().__init__()
        self._fetcher = data_fetcher

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_market_data",
            description="获取A股股票的日线/分钟线行情数据，支持历史数据和实时数据",
            category=ToolCategory.DATA_FETCH,
            parameters=[
                ToolParameter("symbol", "string", "股票代码，如 000001.SZ, 600519.SH"),
                ToolParameter("start_date", "string", "开始日期，格式 YYYY-MM-DD"),
                ToolParameter("end_date", "string", "结束日期，格式 YYYY-MM-DD"),
                ToolParameter("period", "string", "数据周期: daily/weekly/monthly", required=False, default="daily"),
            ],
            examples=["fetch_market_data(symbol='600519.SH', start_date='2024-01-01', end_date='2024-12-31')"],
        )

    async def execute(self, **kwargs) -> Any:
        symbol = kwargs["symbol"]
        start = kwargs["start_date"]
        end = kwargs["end_date"]
        period = kwargs.get("period", "daily")

        if self._fetcher:
            try:
                df = self._fetcher.get_daily(symbol, start, end)
                if df is not None and not df.empty:
                    recent = df.tail(5)
                    return {
                        "symbol": symbol,
                        "period": period,
                        "rows": len(df),
                        "latest_date": str(df.index[-1]) if len(df) > 0 else None,
                        "latest_close": float(df["close"].iloc[-1]) if len(df) > 0 else None,
                        "recent_data": recent.to_dict("records"),
                    }
                return {"symbol": symbol, "error": "无数据返回"}
            except Exception as e:
                return {"symbol": symbol, "error": str(e)}
        return {"symbol": symbol, "error": "数据源未配置"}


class CalculateIndicatorTool(BaseTool):
    """计算技术指标

    真实计算：先经 data_fetcher 取日线，再委托 ``data.technical.TechnicalIndicator``
    计算，返回**指标数值** + 释义。修复前本工具只返回静态释义文本、从不返回数值，
    导致 LLM 拿到的是"指标说明书"而非指标本身。

    无行情数据源时诚实返回未配置，不伪造数值（SDD：禁止 mock 兜底）。
    """

    # 指标名 -> (TechnicalIndicator 方法名, 输出列名集合)
    _INDICATOR_SPECS: Dict[str, Dict[str, Any]] = {
        "rsi": {"method": "add_rsi", "columns": ["rsi"], "accepts_period": True},
        "macd": {"method": "add_macd", "columns": ["macd", "macd_signal", "macd_hist"], "accepts_period": False},
        "bollinger": {"method": "add_bollinger_bands", "columns": ["bb_upper", "bb_middle", "bb_lower", "bb_width"], "accepts_period": True},
        "ma": {"method": "add_ma", "columns": ["ma_5", "ma_10", "ma_20", "ma_60", "ma_120"], "accepts_period": False},
        "atr": {"method": "add_atr", "columns": ["atr"], "accepts_period": True},
        "obv": {"method": "add_obv", "columns": ["obv"], "accepts_period": False},
        "kdj": {"method": "add_kdj", "columns": ["k", "d", "j"], "accepts_period": False},
    }

    _INTERPRETATION: Dict[str, str] = {
        "rsi": "RSI>70超买, RSI<30超卖, 50为多空分界",
        "macd": "DIF上穿DEA金叉看多, DIF下穿DEA死叉看空",
        "bollinger": "触及上轨可能超买, 触及下轨可能超卖",
        "ma": "短期均线上穿长期均线为金叉, 反之为死叉",
        "atr": "ATR越大波动越剧烈, 可用于设置止损",
        "obv": "OBV上升表示买方力量增强",
        "kdj": "K>80超买, K<20超卖, K上穿D为金叉",
    }

    def __init__(self, technical_indicator=None, data_fetcher=None):
        super().__init__()
        self._indicator = technical_indicator
        self._fetcher = data_fetcher

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="calculate_indicator",
            description="计算股票的真实技术指标数值，包括RSI、MACD、布林带、均线、ATR、OBV、KDJ等",
            category=ToolCategory.TECHNICAL_ANALYSIS,
            parameters=[
                ToolParameter("symbol", "string", "股票代码"),
                ToolParameter("indicator", "string", "指标名称: rsi/macd/bollinger/ma/atr/obv/kdj/all"),
                ToolParameter("period", "string", "计算周期(日), 如 14/26/20", required=False, default="14"),
                ToolParameter("start_date", "string", "开始日期 YYYY-MM-DD，默认近一年", required=False),
                ToolParameter("end_date", "string", "结束日期 YYYY-MM-DD，默认今天", required=False),
            ],
            examples=["calculate_indicator(symbol='600519.SH', indicator='rsi', period='14')"],
        )

    def _unavailable(self, symbol: str, indicator_name: str, reason: str) -> Dict[str, Any]:
        """数据不可用时的统一返回（显式说明，禁止伪造数值）"""
        return {
            "symbol": symbol,
            "indicator": indicator_name,
            "values": {},
            "available": False,
            "note": reason,
        }

    async def execute(self, **kwargs) -> Any:
        symbol = kwargs.get("symbol", "unknown")
        indicator_name = str(kwargs.get("indicator", "all")).lower()

        try:
            period = int(kwargs.get("period", "14"))
        except (TypeError, ValueError):
            period = 14

        if self._fetcher is None:
            return self._unavailable(
                symbol, indicator_name,
                "行情数据源未配置，无法计算技术指标。请在创建工具集时注入 DataFetcher。",
            )

        end_date = kwargs.get("end_date") or datetime.now().strftime("%Y-%m-%d")
        start_date = kwargs.get("start_date") or (
            datetime.now() - timedelta(days=365)
        ).strftime("%Y-%m-%d")

        try:
            df = self._fetcher.get_daily(symbol, start_date, end_date)
        except Exception as e:  # noqa: BLE001 - 数据源异常需显式回传，不静默吞掉
            return self._unavailable(symbol, indicator_name, f"行情获取失败: {e}")

        if df is None or df.empty:
            return self._unavailable(symbol, indicator_name, "无行情数据，无法计算技术指标")

        # 延迟导入，避免在未安装 pandas/ta 的环境下 import 即失败
        from finhack_pro.data.technical import TechnicalIndicator

        ti = self._indicator or TechnicalIndicator()

        try:
            if indicator_name == "all":
                computed = ti.add_all_indicators(df, rsi_period=period, atr_period=period)
                columns: List[str] = [
                    c for spec in self._INDICATOR_SPECS.values() for c in spec["columns"]
                ]
            else:
                spec = self._INDICATOR_SPECS.get(indicator_name)
                if spec is None:
                    return {
                        "error": f"未知指标: {indicator_name}",
                        "available": ["all", *self._INDICATOR_SPECS.keys()],
                    }
                method = getattr(ti, spec["method"])
                if spec["accepts_period"]:
                    computed = method(df.copy(), period=period)
                else:
                    computed = method(df.copy())
                columns = spec["columns"]
        except Exception as e:  # noqa: BLE001
            return self._unavailable(symbol, indicator_name, f"指标计算失败: {e}")

        latest = computed.iloc[-1]
        values: Dict[str, Any] = {}
        for col in columns:
            if col in computed.columns:
                raw = latest[col]
                if raw is None or (isinstance(raw, float) and raw != raw):  # NaN 自检
                    values[col] = None
                else:
                    values[col] = round(float(raw), 4)

        as_of = None
        if "date" in computed.columns:
            as_of = str(computed["date"].iloc[-1])

        result: Dict[str, Any] = {
            "symbol": symbol,
            "indicator": indicator_name,
            "period": period,
            "as_of": as_of,
            "bars": len(computed),
            "values": values,
            "available": True,
        }
        if indicator_name == "all":
            result["interpretation"] = dict(self._INTERPRETATION)
        elif indicator_name in self._INTERPRETATION:
            result["interpretation"] = self._INTERPRETATION[indicator_name]

        # 数值不足（如上市不足周期长度）时显式标注，避免 LLM 把 None 当 0 解读
        if any(v is None for v in values.values()):
            result["note"] = "部分指标因历史数据不足无法计算（值为 null），请勿按 0 解读。"

        return result


class SearchNewsTool(BaseTool):
    """搜索新闻和公告"""

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_news",
            description="搜索与股票相关的新闻、公告、研报信息，支持按关键词和情感倾向筛选",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("keyword", "string", "搜索关键词(股票名称/代码/行业)"),
                ToolParameter("days", "integer", "搜索最近N天的新闻", required=False, default=7),
                ToolParameter("sentiment_filter", "string", "情感过滤: positive/negative/neutral/all",
                             required=False, default="all", enum=["positive", "negative", "neutral", "all"]),
                ToolParameter("source", "string", "新闻来源: all/finance/media/social",
                             required=False, default="all"),
            ],
            examples=["search_news(keyword='贵州茅台', days=3, sentiment_filter='negative')"],
        )

    async def execute(self, **kwargs) -> Any:
        keyword = kwargs["keyword"]
        days = kwargs.get("days", 7)
        sentiment_filter = kwargs.get("sentiment_filter", "all")
        source_filter = kwargs.get("source", "all")

        code = _normalize_stock_code(keyword)

        raw_news: List[Dict[str, Any]] = []
        news: List[Dict[str, Any]] = []
        error: Optional[str] = None
        if code:
            try:
                import akshare as ak

                df = ak.stock_news_em(symbol=code)
                if df is not None and len(df):
                    cutoff = datetime.now() - timedelta(days=days)
                    for _, row in df.iterrows():
                        publish_time = str(row.get("发布时间", "")).strip()
                        # 解析发布时间并按 days 过滤
                        pub_dt = None
                        try:
                            pub_dt = datetime.strptime(publish_time[:19], "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pub_dt = None

                        title = str(row.get("新闻标题", "")).strip()
                        content = str(row.get("新闻内容", "")).strip()
                        source = str(row.get("文章来源", "")).strip()
                        url = str(row.get("新闻链接", "")).strip()

                        # 来源软过滤（all 放行，否则按来源关键词匹配）
                        if source_filter not in ("all", "", None):
                            if source_filter not in source:
                                continue

                        text = f"{title} {content}"
                        sent = _classify_sentiment(text)

                        item = {
                            "title": title,
                            "content": content,
                            "source": source,
                            "publish_time": publish_time,
                            "url": url,
                            "sentiment": sent,
                        }
                        raw_news.append(item)
                        # days 窗口过滤（解析失败的新闻视为近期，保留）
                        if pub_dt is not None and pub_dt < cutoff:
                            continue
                        news.append(item)
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"
                logger.warning("search_news 东财新闻接口调用失败: %s", error)
        else:
            error = "无法从关键词中提取 6 位股票代码"

        # 情感过滤
        if sentiment_filter not in ("all", "", None):
            news = [n for n in news if n["sentiment"] == sentiment_filter]

        # 优雅降级：days 窗口过滤后为空但原始抓取有数据，则回退到东财近期新闻，
        # 避免过严的时间窗口再次落入"无数据"占位态（东财 news_em 本身即返回近期新闻）
        if not news and raw_news:
            news = raw_news

        if not news:
            if error:
                note = f"未获取到相关新闻（数据源错误: {error}）"
            else:
                note = (
                    "东财新闻接口暂无可匹配数据。"
                    "该结果仅反映数据缺失状态，不代表实际市场情况。"
                )
            return {
                "keyword": keyword,
                "days": days,
                "sentiment_filter": sentiment_filter,
                "total_results": 0,
                "news": [],
                "note": note,
            }

        return {
            "keyword": keyword,
            "days": days,
            "sentiment_filter": sentiment_filter,
            "total_results": len(news),
            "news": news,
        }


class AnalyzeSentimentTool(BaseTool):
    """文本情感分析"""

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="analyze_sentiment",
            description="对文本(新闻标题、社交媒体帖子、研报摘要)进行情感分析，输出情感倾向和置信度",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("text", "string", "待分析的文本"),
                ToolParameter("context", "string", "上下文(股票名称/行业)，辅助情感判断", required=False),
            ],
            examples=["analyze_sentiment(text='茅台Q3营收同比增长15%', context='贵州茅台')"],
        )

    async def execute(self, **kwargs) -> Any:
        text = kwargs["text"]
        context = kwargs.get("context", "")

        # 基于关键词的简单情感分析(实际应使用NLP模型)
        # 词典统一复用模块级单一来源 _POSITIVE_WORDS / _NEGATIVE_WORDS，
        # 避免与 _classify_sentiment()（search_news 用）两份定义漂移。
        score = 0
        matched_positive = []
        matched_negative = []
        for word in _POSITIVE_WORDS:
            if word in text:
                score += 1
                matched_positive.append(word)
        for word in _NEGATIVE_WORDS:
            if word in text:
                score -= 1
                matched_negative.append(word)

        if score > 0:
            sentiment = "positive"
        elif score < 0:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        confidence = min(0.9, 0.5 + abs(score) * 0.1)

        return {
            "text": text[:100],
            "context": context,
            "sentiment": sentiment,
            "confidence": round(confidence, 2),
            "score": score,
            "matched_positive": matched_positive,
            "matched_negative": matched_negative,
            "method": "keyword_based",
            "note": "当前使用关键词匹配，建议接入LLM或NLP模型提升准确度",
        }


class FetchFundamentalTool(BaseTool):
    """获取基本面数据"""

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_fundamental",
            description="获取股票的基本面数据，包括财务指标(PE/PB/PS/ROE)、财报数据、行业对比等",
            category=ToolCategory.FUNDAMENTAL,
            parameters=[
                ToolParameter("symbol", "string", "股票代码"),
                ToolParameter("data_type", "string", "数据类型: valuation/financial/industry/forecast",
                             enum=["valuation", "financial", "industry", "forecast"]),
            ],
            examples=["fetch_fundamental(symbol='600519.SH', data_type='valuation')"],
        )

    async def execute(self, **kwargs) -> Any:
        symbol = kwargs["symbol"]
        data_type = kwargs.get("data_type", "valuation")

        # 基本面数据：数据源未配置时诚实返回空结果（非模拟数据）
        return {
            "symbol": symbol,
            "data_type": data_type,
            "data": {},
            "note": "基本面数据源未配置。请配置tushare token或akshare以获取PE/PB/ROE/财报等数据。",
        }


class GetPortfolioStatusTool(BaseTool):
    """获取组合状态"""

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_portfolio_status",
            description="获取当前投资组合的状态，包括持仓、现金、总资产、盈亏等",
            category=ToolCategory.RISK_MANAGEMENT,
            parameters=[
                ToolParameter("detail_level", "string", "详细程度: summary/positions/full",
                             required=False, default="summary", enum=["summary", "positions", "full"]),
            ],
            agent_roles=["risk_manager", "trade_executor"],
        )

    async def execute(self, **kwargs) -> Any:
        return {
            "cash": 0,
            "positions": {},
            "total_value": 0,
            "unrealized_pnl": 0,
            "note": "组合状态需从风控管理器获取，当前返回空状态",
        }


class CalculateRiskMetricsTool(BaseTool):
    """计算风险指标"""

    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="calculate_risk_metrics",
            description="计算投资组合的风险指标，包括VaR、最大回撤、夏普比率、波动率等",
            category=ToolCategory.RISK_MANAGEMENT,
            parameters=[
                ToolParameter("metric", "string", "指标名称: var/drawdown/sharpe/volatility/all"),
                ToolParameter("confidence", "number", "置信度(仅VaR使用), 如 0.95", required=False, default=0.95),
            ],
            agent_roles=["risk_manager"],
        )

    async def execute(self, **kwargs) -> Any:
        metric = kwargs.get("metric", "all")
        return {
            "metric": metric,
            "note": "风险指标需基于实际持仓和收益数据计算",
            "available_metrics": ["var", "drawdown", "sharpe", "volatility", "sortino", "calmar"],
        }


def create_default_toolkit(data_fetcher=None, technical_indicator=None) -> ToolRegistry:
    """创建默认工具集"""
    registry = ToolRegistry()
    registry.register(FetchMarketDataTool(data_fetcher=data_fetcher))
    registry.register(
        CalculateIndicatorTool(
            technical_indicator=technical_indicator,
            data_fetcher=data_fetcher,
        )
    )
    registry.register(SearchNewsTool())
    registry.register(AnalyzeSentimentTool())
    registry.register(FetchFundamentalTool())
    registry.register(GetPortfolioStatusTool())
    registry.register(CalculateRiskMetricsTool())
    return registry
