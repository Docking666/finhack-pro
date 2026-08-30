"""
筛选条件契约（FilterSpec）

这是想法 1 的**核心边界**：LLM 的产出止步于此。

    用户输入（自然语言）
        ↓  LLM，仅一次，编译期
    FilterSpec（声明式、可校验、可缓存、可审计）   ← 本模块
        ↓  确定性执行，无 LLM
    筛选结果

LLM 只负责"翻译"，不参与任何单只股票的评价。因此同一份 FilterSpec
配同一份数据，任何时候跑都得到同一结果 —— 这是回测可复现的前提，
也是成本可控的前提（5400 只股票只调用一次 LLM，而不是 5400 次）。

三条硬约束：

1. **字段白名单**。Condition 的 field 必须存在于 FactorRegistry，
   校验在 :meth:`Condition.validate` 中完成，越界即拒。
2. **unresolved 必须显式**。无法解析的子句进入 FilterSpec.unresolved，
   由调用方决定报错还是提示用户 —— 绝不静默丢弃。
   "用户提了三个条件、只执行了两个"是最危险的失效模式。
3. **NaN 不等于不满足**。因子算不出是"不可用"，不是"不满足"，
   执行引擎据此区分 failed 与 unavailable（见 engine.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 支持的操作符。数值型与分类型各自可用子集见 _NUMERIC_OPS / _CATEGORY_OPS
NUMERIC_OPS: Tuple[str, ...] = (">", ">=", "<", "<=", "==", "!=", "between")
CATEGORY_OPS: Tuple[str, ...] = ("==", "!=", "in", "not_in")
ALL_OPS: Tuple[str, ...] = NUMERIC_OPS + CATEGORY_OPS


class SpecError(ValueError):
    """筛选条件不合法"""


@dataclass
class Condition:
    """单条筛选条件"""

    field: str
    op: str
    value: Any
    description: str = ""   # 对应的原始中文，便于向用户解释与审计

    def __post_init__(self) -> None:
        self.field = (self.field or "").strip().lower()
        self.op = (self.op or "").strip()

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def validate(self, registry: Any) -> None:
        """校验字段与操作符合法、值类型与个数匹配。

        Raises:
            SpecError: 任一环节不合法
        """
        spec = registry.spec(self.field) if hasattr(registry, "spec") else None
        if spec is None:
            available = sorted(registry.names()) if hasattr(registry, "names") else []
            raise SpecError(
                f"未知字段 {self.field!r}。可用字段: {available}"
            )
        if self.op not in ALL_OPS:
            raise SpecError(
                f"字段 {self.field!r} 的操作符 {self.op!r} 不支持。"
                f"可选: {list(ALL_OPS)}"
            )
        is_numeric = getattr(spec, "dtype", "numeric") == "numeric"
        if is_numeric and self.op not in NUMERIC_OPS:
            raise SpecError(
                f"数值字段 {self.field!r} 不支持操作符 {self.op!r}，"
                f"数值字段可选: {list(NUMERIC_OPS)}"
            )
        if not is_numeric and self.op not in CATEGORY_OPS:
            raise SpecError(
                f"分类字段 {self.field!r} 不支持操作符 {self.op!r}，"
                f"分类字段可选: {list(CATEGORY_OPS)}"
            )
        self._validate_value(is_numeric)

    def _validate_value(self, is_numeric: bool) -> None:
        if self.op == "between":
            if not isinstance(self.value, (list, tuple)) or len(self.value) != 2:
                raise SpecError(
                    f"between 需要 [下界, 上界] 两个值，收到 {self.value!r}"
                )
            lo, hi = self.value
            if is_numeric:
                try:
                    lo, hi = float(lo), float(hi)
                except (TypeError, ValueError) as e:
                    raise SpecError(f"between 的界值必须为数值: {self.value!r}") from e
                if lo > hi:
                    raise SpecError(f"between 下界 {lo} 大于上界 {hi}")
            return
        if self.op in ("in", "not_in"):
            if not isinstance(self.value, (list, tuple)) or not self.value:
                raise SpecError(f"{self.op} 需要非空列表，收到 {self.value!r}")
            return
        if is_numeric:
            try:
                float(self.value)
            except (TypeError, ValueError) as e:
                raise SpecError(
                    f"数值字段 {self.field!r} 的值必须是数字，收到 {self.value!r}"
                ) from e

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def evaluate(self, value: float) -> bool:
        """对单个因子值判定。调用前须确保 value 可用（非 NaN）。"""
        if self.op == "between":
            lo, hi = float(self.value[0]), float(self.value[1])
            return lo <= value <= hi
        if self.op in ("in", "not_in"):
            hit = value in self.value
            return hit if self.op == "in" else not hit
        threshold = float(self.value)
        return {
            ">": value > threshold,
            ">=": value >= threshold,
            "<": value < threshold,
            "<=": value <= threshold,
            "==": value == threshold,
            "!=": value != threshold,
        }[self.op]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "op": self.op,
            "value": self.value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Condition":
        return cls(
            field=payload.get("field", ""),
            op=payload.get("op", ""),
            value=payload.get("value"),
            description=payload.get("description", ""),
        )

    def __str__(self) -> str:
        base = f"{self.field} {self.op} {self.value}"
        return f"{base}（{self.description}）" if self.description else base


@dataclass
class FilterSpec:
    """一份完整的筛选条件（LLM 编译产物）"""

    conditions: List[Condition] = field(default_factory=list)
    logic: str = "all"                    # all(全部满足) / any(任一满足)
    order_by: Optional[str] = None        # 排序字段，须为已注册因子
    ascending: bool = False
    limit: int = 0                        # >0 时截断
    as_of: str = ""                       # 决策日，用于时间语义解析与审计
    unresolved: List[str] = field(default_factory=list)  # 无法解析的原始子句
    raw_query: str = ""

    @property
    def ok(self) -> bool:
        """是否有未解析项。False 时调用方应提示用户，而非照常执行。"""
        return not self.unresolved and bool(self.conditions)

    def validate(self, registry: Any) -> None:
        """校验全部条件、排序字段与 logic。"""
        if self.logic not in ("all", "any"):
            raise SpecError(f"logic 必须是 all/any，收到 {self.logic!r}")
        if not self.conditions:
            raise SpecError("筛选条件为空")
        for cond in self.conditions:
            cond.validate(registry)
        if self.order_by and not registry.spec(self.order_by):
            raise SpecError(f"排序字段 {self.order_by!r} 未注册")

    def required_fields(self) -> List[str]:
        """执行本 spec 需要计算的因子（去重、保序）。"""
        fields: List[str] = []
        for cond in self.conditions:
            if cond.field not in fields:
                fields.append(cond.field)
        if self.order_by and self.order_by not in fields:
            fields.append(self.order_by)
        return fields

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conditions": [c.to_dict() for c in self.conditions],
            "logic": self.logic,
            "order_by": self.order_by,
            "ascending": self.ascending,
            "limit": self.limit,
            "as_of": self.as_of,
            "unresolved": list(self.unresolved),
            "raw_query": self.raw_query,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "FilterSpec":
        return cls(
            conditions=[
                Condition.from_dict(c) for c in payload.get("conditions", []) or []
            ],
            logic=payload.get("logic", "all"),
            order_by=payload.get("order_by") or None,
            ascending=bool(payload.get("ascending", False)),
            limit=int(payload.get("limit", 0) or 0),
            as_of=payload.get("as_of", ""),
            unresolved=list(payload.get("unresolved", []) or []),
            raw_query=payload.get("raw_query", ""),
        )

    def summary(self) -> str:
        """一行人类可读摘要，用于日志与向用户回显。"""
        joiner = " 且 " if self.logic == "all" else " 或 "
        parts = [str(c) for c in self.conditions]
        text = joiner.join(parts) if parts else "（无条件）"
        if self.unresolved:
            text += f"；未解析: {self.unresolved}"
        return text


__all__ = [
    "Condition",
    "FilterSpec",
    "SpecError",
    "NUMERIC_OPS",
    "CATEGORY_OPS",
    "ALL_OPS",
]
