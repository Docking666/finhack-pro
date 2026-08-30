"""
筛选执行引擎

本模块**完全确定性**，不含任何 LLM 调用。输入 FilterSpec + 行情数据，
输出命中列表。同一份输入在任何时刻都得到同一份输出。

与"不可用"相关的三条纪律：

1. **NaN 不等于不满足**。因子算不出（历史不足、缺列、除零）是
   "不可用"，记为 unavailable，而非塞进 failed。
   把"算不出"当成"不满足"会让股票池系统性剔除新上市/长期停牌标的 ——
   这正是想法 1 里最需要警惕的偏差来源。
2. **失败标的必须可见**。``ScreenResult.skipped`` 记录每个被跳过标的及原因，
   调用方据此判断覆盖率。全市场扫描若悄悄少了 300 只，结论就不可信。
3. **调用方负责截断**。引擎不对输入序列做截断 —— as_of 的语义由调用方保证。
   若传入含未来的数据，筛选结果同样含未来函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from finhack_pro.screening.spec import FilterSpec, SpecError
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ScreenHit:
    """一只命中股票"""

    symbol: str
    values: Dict[str, float] = field(default_factory=dict)  # field -> 因子值
    matched: List[str] = field(default_factory=list)        # 满足的条件
    failed: List[str] = field(default_factory=list)         # 不满足的条件

    def explains(self) -> str:
        """命中原因，用于向用户解释（可审计：LLM 说了什么、机器做了什么）。"""
        hit = "、".join(self.matched) if self.matched else "（无条件）"
        return f"{self.symbol}: 满足 {hit}"


@dataclass
class ScreenResult:
    """一次筛选的完整结果"""

    spec: FilterSpec
    hits: List[ScreenHit] = field(default_factory=list)
    #: symbol -> 跳过原因。被跳过的标的既不进 hits 也不算"不满足"，
    #: 覆盖率 = len(hits) + len(未命中的已评估标的) 才是真实的评估基数。
    skipped: Dict[str, str] = field(default_factory=dict)
    evaluated: int = 0

    @property
    def symbols(self) -> List[str]:
        return [h.symbol for h in self.hits]

    @property
    def coverage_rate(self) -> float:
        """成功评估（含未命中）占请求总数的比例。"""
        total = self.evaluated + len(self.skipped)
        if total == 0:
            return 0.0
        return round(self.evaluated / total, 4)

    def summary(self) -> str:
        return (
            f"筛选完成: 请求={self.evaluated + len(self.skipped)}, "
            f"评估={self.evaluated}, 跳过={len(self.skipped)}, "
            f"命中={len(self.hits)}, 覆盖率={self.coverage_rate:.2%}"
        )


class ScreenEngine:
    """筛选执行引擎

    Args:
        factors: 因子注册中心
        max_skipped_warn: 跳过数超过该阈值时告警（全市场扫描的健康信号）
    """

    def __init__(self, factors: Any, max_skipped_warn: int = 50) -> None:
        self.factors = factors
        self.max_skipped_warn = max_skipped_warn

    def screen(
        self,
        data: Dict[str, pd.DataFrame],
        spec: FilterSpec,
        limit: int = 0,
    ) -> ScreenResult:
        """按 FilterSpec 筛选。

        Args:
            data: {symbol: OHLCV DataFrame}，必须已截断至 as_of
            spec: 筛选条件（调用前应先用 spec.validate 校验）
            limit: >0 时覆盖 spec.limit

        Returns:
            ScreenResult
        """
        if not spec.conditions:
            # 空条件会让所有标的"通过"，等价于没有筛选 —— 必须显式报错，
            # 否则用户以为筛过了，实际拿到的是全市场
            raise SpecError("筛选条件为空：请先用 FilterSpec.validate() 校验，或检查编译结果")

        result = ScreenResult(spec=spec)
        needed = spec.required_fields()

        for symbol, df in data.items():
            if df is None or len(df) == 0:
                result.skipped[symbol] = "无数据"
                continue

            values: Dict[str, float] = {}
            missing: Optional[str] = None
            for fname in needed:
                value = self.factors.compute(fname, df)
                if value != value:  # NaN 自检，不用 math.isnan（避免非 float）
                    missing = fname
                    break
                values[fname] = value

            if missing is not None:
                result.skipped[symbol] = f"因子 {missing} 不可用（历史不足或数据缺失）"
                continue

            result.evaluated += 1
            hit = ScreenHit(symbol=symbol, values=values)
            for cond in spec.conditions:
                if cond.evaluate(values[cond.field]):
                    hit.matched.append(cond.field)
                else:
                    hit.failed.append(cond.field)

            # all: 没有条件不满足；any: 至少有一个条件满足
            passed = not hit.failed if spec.logic == "all" else bool(hit.matched)
            if passed:
                result.hits.append(hit)

        # 排序：优先用 spec.order_by，其次按命中条件数
        if spec.order_by and spec.order_by in needed:
            result.hits.sort(
                key=lambda h: h.values.get(spec.order_by, 0.0),
                reverse=not spec.ascending,
            )
        else:
            result.hits.sort(key=lambda h: len(h.matched), reverse=True)

        effective_limit = limit or spec.limit
        if effective_limit and effective_limit > 0:
            result.hits = result.hits[:effective_limit]

        if len(result.skipped) > self.max_skipped_warn:
            logger.warning(
                "{} 只标的因因子不可用被跳过 —— 覆盖率过低会让股票池系统性偏离",
                len(result.skipped),
            )
        logger.info(result.summary())
        return result


__all__ = ["ScreenEngine", "ScreenHit", "ScreenResult"]
