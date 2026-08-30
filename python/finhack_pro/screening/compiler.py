"""
自然语言 → FilterSpec 编译器

**这是整个选股流程中 LLM 唯一出场的地方，且只在编译期调用一次。**

    5400 只股票 × 1 次 LLM 编译  ✅
    5400 只股票 × 1 次 LLM 评价  ❌（成本与延迟都不可行，且不可复现）

编译产出的 FilterSpec 是声明式的：可缓存、可 diff、可人工修订、可复现。
用户不满意时，改的是条件，而不是让 LLM 重新"想一想"。

四条硬约束：

1. **字段白名单**。提示词里只给出 FactorRegistry 中真实存在的因子，
   且编译结果逐条校验。LLM 编造的字段名不会被静默忽略 ——
   它要么进入 unresolved 让调用方看见，要么被判为未知字段。
2. **unresolved 显式化**。无法映射到因子的子句（如"管理层靠谱"）
   必须进 unresolved，绝不静默丢弃。"用户提了三个条件只执行两个"
   是最危险的失效模式。
3. **时间相对 as_of 解析**。"最近5天"中的"最近"必须相对决策日，
   而不是相对今天 —— 否则回测里每个时点都在用同一个"现在"的窗口。
4. **不得输出股票代码**。编译器的产出是筛选条件，不是股票列表。
   若 LLM 自作主张返回了具体标的，视为编译失败（越权）。

Usage:
    >>> compiler = ConditionCompiler(chat_fn, factors)
    >>> spec = compiler.compile("最近放量突破20日线的强势股", as_of="2024-06-30")
    >>> if not spec.ok:
    ...     print("未能解析:", spec.unresolved)
    >>> spec.validate(factors)
    >>> result = ScreenEngine(factors).screen(data, spec)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from finhack_pro.screening.spec import Condition, FilterSpec, SpecError
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

#: 聊天函数：(system, user) -> str 或其 awaitable。
#: 做成注入式是为了可测试、可替换，也避免本模块依赖具体 LLM SDK。
ChatFn = Callable[[str, str], Union[str, Awaitable[str]]]

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class CompileError(RuntimeError):
    """编译失败（LLM 不可用、返回不可解析、越权输出等）"""


class ConditionCompiler:
    """自然语言选股条件 → FilterSpec

    Args:
        chat_fn: 聊天函数，签名 (system, user) -> str
        factors: 因子注册中心（决定 LLM 可见的字段白名单）
        max_conditions: 条件数上限，防止 LLM 编出几十条导致过拟合
    """

    SYSTEM_TEMPLATE = """你是量化选股条件的编译器的核心组件。你的唯一职责是把用户的自然语言描述**翻译**成结构化的筛选条件。

你只做翻译，不做推荐。严禁输出任何股票代码或标的名称 —— 那是执行引擎的事。

可用因子（只能从下表选用，不得自创字段名）：
{factor_catalog}

输出格式（纯 JSON，不要任何其他文字）：
{{
  "conditions": [
    {{"field": "因子名", "op": "操作符", "value": 数值或[下界,上界], "description": "对应的原始中文"}}
  ],
  "logic": "all 或 any",
  "order_by": "排序因子名或 null",
  "ascending": false,
  "unresolved": ["无法映射到任何因子的原始子句"]
}}

操作符规则：
- 数值字段可用：> >= < <= == != between
- between 的值必须是 [下界, 上界] 两个数字
- logic=all 表示全部条件都满足，any 表示任一满足

关键规则：
1. 只能用上表列出的因子名。用户说到的概念若没有任何因子能表达，
   把该子句原文放进 unresolved，不要强行套一个不相关的因子。
2. 所有的"最近""近N日"都是相对决策日 {as_of} 而言，不是相对今天。
3. 不要把用户的模糊形容词（如"好公司""龙头"）硬编码成具体数值后
   假装精确 —— 能映射就映射，不能映射就放 unresolved。
4. 条件不要超过 {max_conditions} 条，保留最具区分度的那些。
5. 不要输出股票代码。若你发现自己要推荐具体标的，说明任务理解有误。"""

    def __init__(
        self,
        chat_fn: ChatFn,
        factors: Any,
        max_conditions: int = 8,
    ) -> None:
        if not callable(chat_fn):
            raise ValueError("chat_fn 必须可调用")
        self.chat_fn = chat_fn
        self.factors = factors
        self.max_conditions = max(1, int(max_conditions))

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def compile(self, query: str, as_of: str = "") -> FilterSpec:
        """同步编译。已有事件循环时会在其中等待（由调用方保证不嵌套）。"""
        coro = self.acompile(query, as_of=as_of)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        if loop.is_running():
            raise CompileError(
                "已在运行事件循环中，请改用 await compiler.acompile(...)"
            )
        return asyncio.run(coro)

    async def acompile(self, query: str, as_of: str = "") -> FilterSpec:
        """异步编译。"""
        query = (query or "").strip()
        if not query:
            raise ValueError("待编译的查询不能为空")

        system = self._render_system(as_of)
        raw = self.chat_fn(system, query)
        if inspect.isawaitable(raw):
            raw = await raw
        text = (raw or "").strip()
        if not text:
            raise CompileError("LLM 返回为空，无法编译筛选条件")

        payload = self._extract_json(text)
        spec = self._to_spec(payload, query=query, as_of=as_of)
        self._guard_no_symbols(spec, text)
        logger.info("条件编译完成: {}", spec.summary())
        return spec

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _render_system(self, as_of: str) -> str:
        return self.SYSTEM_TEMPLATE.format(
            factor_catalog=self.factors.describe_for_prompt(),
            as_of=as_of or "（未提供，按数据最后一日计）",
            max_conditions=self.max_conditions,
        )

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        """从 LLM 输出中提取 JSON 对象。

        容忍三种常见噪声：```json 围栏、JSON 前的客套话、JSON 后的补充说明。
        第三点尤其常见（"……另外我觉得 600519 也不错"），若只做整体解析会
        直接判为解析失败，反而让后面"越权输出股票代码"的检查失去机会。
        """
        decoder = json.JSONDecoder()
        candidates = list(_JSON_BLOCK.findall(text)) + [text]
        for cand in candidates:
            cand = cand.strip()
            if not cand:
                continue
            # 整体就是一个 JSON 对象
            try:
                parsed = json.loads(cand)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
            # 从每个 '{' 起做局部解析，容忍尾部附加文字
            for i, ch in enumerate(cand):
                if ch != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(cand, i)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        raise CompileError(
            f"LLM 输出中未找到合法 JSON 对象。原始输出前 300 字符: {text[:300]}"
        )

    def _to_spec(
        self, payload: Dict[str, Any], query: str, as_of: str
    ) -> FilterSpec:
        """把 JSON 载荷转成 FilterSpec，并逐条校验字段白名单。

        非法条件**不静默丢弃**：记入 unresolved 并告警，让调用方看见。
        """
        raw_conditions = payload.get("conditions") or []
        if not isinstance(raw_conditions, list):
            raise CompileError(f"conditions 必须是列表，收到 {type(raw_conditions).__name__}")

        conditions: List[Condition] = []
        unresolved: List[str] = list(payload.get("unresolved") or [])

        for item in raw_conditions:
            if not isinstance(item, dict):
                unresolved.append(str(item))
                continue
            try:
                cond = Condition.from_dict(item)
                cond.validate(self.factors)
            except (SpecError, TypeError, ValueError) as e:
                # 关键：不静默丢弃。用户说了但没生效，必须能看见。
                desc = str(item.get("description") or item)
                unresolved.append(desc)
                logger.warning("条件校验失败，记入 unresolved: {} -> {}", item, e)
                continue
            conditions.append(cond)

        if not conditions:
            raise CompileError(
                f"没有任何条件通过校验，无法编译。未解析项: {unresolved}"
            )

        if len(conditions) > self.max_conditions:
            logger.warning(
                "条件数 {} 超过上限 {}，仅保留前 {} 条",
                len(conditions),
                self.max_conditions,
                self.max_conditions,
            )
            dropped = conditions[self.max_conditions :]
            conditions = conditions[: self.max_conditions]
            unresolved.extend(f"条件数超限被丢弃: {c}" for c in dropped)

        spec = FilterSpec(
            conditions=conditions,
            logic=payload.get("logic", "all") or "all",
            order_by=payload.get("order_by") or None,
            ascending=bool(payload.get("ascending", False)),
            limit=int(payload.get("limit", 0) or 0),
            as_of=as_of,
            unresolved=unresolved,
            raw_query=query,
        )
        if spec.logic not in ("all", "any"):
            logger.warning("logic=%r 不合法，回退为 all", spec.logic)
            spec.logic = "all"
        return spec

    @staticmethod
    def _guard_no_symbols(spec: FilterSpec, raw_text: str) -> None:
        """编译器的产出必须是条件，不能是标的。

        LLM 偶尔会"好心"顺带推荐几只股票。若放任，这些标的会绕过
        筛选逻辑直接进入结果 —— 那正是我们要避免的"LLM 逐股评判"。
        """
        # 只在 unresolved 为空时才检查：若本来就有未解析项，
        # LLM 可能只是在解释为什么解析不了，其中会提到示例
        if spec.unresolved:
            return
        hits = re.findall(r"\b[0-9]{6}\b", raw_text)
        if hits:
            raise CompileError(
                f"编译器输出中包含股票代码 {sorted(set(hits))[:5]} —— "
                f"编译器的产出只能是筛选条件，不得推荐具体标的。"
            )


def chat_fn_from_llm_client(client: Any, timeout: Optional[int] = None) -> ChatFn:
    """把项目内的 LLMClient 适配成 ChatFn。

    LLMClient.chat 是异步且参数较多，这里收口成 (system, user) -> str，
    使编译器不依赖具体 LLM 实现（便于测试与替换）。
    """
    if not hasattr(client, "chat"):
        raise ValueError("LLM 客户端必须提供 chat() 方法")

    async def _call(system: str, user: str) -> str:
        kwargs: Dict[str, Any] = {"system": system}
        if timeout is not None:
            kwargs["timeout"] = timeout
        return await client.chat(user, **kwargs)

    return _call


__all__ = [
    "ConditionCompiler",
    "CompileError",
    "ChatFn",
    "chat_fn_from_llm_client",
]
