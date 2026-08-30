"""
选股因子注册表

为什么必须先有注册表
--------------------
想法 1 要求"LLM 把自然语言条件编译成筛选条件"。这一步能否可信，取决于
LLM 能否被约束在**已知可用**的因子集合内。没有白名单时，LLM 会编出
注册表里不存在的因子名，执行时要么崩溃，要么被静默忽略 ——
后者更危险：用户以为条件生效了，实际上完全没有。

因此本模块提供：
- 因子契约（name / description / compute / unit / 语义方向）
- 可逆注册（与 :mod:`finhack_pro.data.registry` 同一套 disposer 语义）
- ``describe_for_prompt()``：把因子目录渲染成给 LLM 看的中文说明，
  这是编译器能正确选用因子的前提

compute 的约定（PIT）
--------------------
``compute(df) -> float``：返回**最后一根 bar** 上的因子值。

调用方必须传入截至决策时点的**截断序列**。因子函数一律取最后一根 bar，
因此只要调用方截断了，就不存在未来函数；反之若传入整段历史，
所有因子都会带上未来信息 —— 责任在调用方，文档在此明示。

计算不出时返回 ``float("nan")``，由执行引擎归入 unavailable 而非"不满足"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

FactorFn = Callable[[pd.DataFrame], float]


@dataclass
class FactorSpec:
    """一个选股因子的声明"""

    name: str
    description: str              # 给 LLM 看的中文说明，直接决定它能否选对
    compute: FactorFn
    unit: str = ""                # 量纲提示："元" / "%" / "倍" / "天" / ""
    higher_means: str = ""        # 语义方向提示，如 "成交量越放大越大"
    dtype: str = "numeric"        # numeric | category
    min_bars: int = 0             # 至少需要多少根 K 线，不足则 unavailable
    origin: str = "builtin"


class FactorRegistry:
    """选股因子注册中心（注册可逆，语义同 DataSourceRegistry）"""

    def __init__(self) -> None:
        self._factors: Dict[str, FactorSpec] = {}

    def register(
        self,
        name: str,
        description: str,
        compute: FactorFn,
        *,
        unit: str = "",
        higher_means: str = "",
        dtype: str = "numeric",
        min_bars: int = 0,
        origin: str = "builtin",
        replace: bool = False,
    ) -> Callable[[], None]:
        """注册因子，返回 disposer（调用即还原注册前状态）。"""
        key = (name or "").strip().lower()
        if not key:
            raise ValueError("因子名不能为空")
        if not callable(compute):
            raise ValueError(f"因子 {key!r} 的 compute 不可调用")
        if key in self._factors and not replace:
            raise ValueError(
                f"因子 {key!r} 已注册。确需覆盖请显式传 replace=True。"
            )

        previous = self._factors.get(key)
        self._factors[key] = FactorSpec(
            name=key,
            description=description,
            compute=compute,
            unit=unit,
            higher_means=higher_means,
            dtype=dtype,
            min_bars=min_bars,
            origin=origin,
        )

        def dispose() -> None:
            if previous is None:
                self._factors.pop(key, None)
            elif self._factors.get(key) is not None:
                self._factors = {
                    k: (previous if k == key else v) for k, v in self._factors.items()
                }

        return dispose

    def unregister(self, name: str) -> bool:
        return self._factors.pop((name or "").strip().lower(), None) is not None

    def spec(self, name: str) -> Optional[FactorSpec]:
        return self._factors.get((name or "").strip().lower())

    def names(self) -> List[str]:
        return list(self._factors.keys())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and (name or "").strip().lower() in self._factors

    def __len__(self) -> int:
        return len(self._factors)

    def compute(self, name: str, df: pd.DataFrame) -> float:
        """计算单个因子值（取最后一根 bar）。数据不足返回 NaN。"""
        spec = self.spec(name)
        if spec is None:
            raise KeyError(f"未注册的因子: {name}")
        if spec.min_bars and len(df) < spec.min_bars:
            return float("nan")
        try:
            value = spec.compute(df)
        except Exception as e:
            logger.debug("因子 {} 计算失败: {}", name, e)
            return float("nan")
        if value is None:
            return float("nan")
        return float(value)

    def describe_for_prompt(self) -> str:
        """渲染成给 LLM 的因子目录。描述质量直接决定编译正确率。"""
        lines: List[str] = []
        for spec in self._factors.values():
            unit = f"，单位：{spec.unit}" if spec.unit else ""
            hint = f"；{spec.higher_means}" if spec.higher_means else ""
            lines.append(f"- {spec.name}：{spec.description}{unit}{hint}")
        return "\n".join(lines)


# ============================================================================
# 内置因子
# ============================================================================


def _col(df: pd.DataFrame, name: str) -> Optional[np.ndarray]:
    if name not in df.columns:
        return None
    return df[name].to_numpy(dtype=float)


def _nan() -> float:
    return float("nan")


def _last(arr: Optional[np.ndarray]) -> float:
    if arr is None or len(arr) == 0:
        return _nan()
    value = arr[-1]
    return float(value) if np.isfinite(value) else _nan()


def _pct_change(arr: Optional[np.ndarray], n: int) -> float:
    """n 日收益率（%）。历史不足 n+1 根则 NaN。"""
    if arr is None or len(arr) <= n or arr[-n - 1] == 0:
        return _nan()
    return (arr[-1] / arr[-n - 1] - 1.0) * 100.0


def _ma(arr: Optional[np.ndarray], n: int) -> float:
    if arr is None or len(arr) < n:
        return _nan()
    return float(np.mean(arr[-n:]))


def _builtin_factors() -> List[Tuple[str, str, FactorFn, str, str, int]]:
    """(name, description, compute, unit, higher_means, min_bars)"""

    return [
        ("close", "最新收盘价", lambda df: _last(_col(df, "close")), "元", "", 1),
        ("volume", "最新交易日成交量", lambda df: _last(_col(df, "volume")), "股", "", 1),
        (
            "amount",
            "最新交易日成交额",
            lambda df: _last(_col(df, "amount")),
            "元",
            "",
            1,
        ),
        (
            "ret_5",
            "近5个交易日涨跌幅",
            lambda df: _pct_change(_col(df, "close"), 5),
            "%",
            "越大越强",
            6,
        ),
        (
            "ret_20",
            "近20个交易日涨跌幅",
            lambda df: _pct_change(_col(df, "close"), 20),
            "%",
            "越大越强",
            21,
        ),
        (
            "ret_60",
            "近60个交易日涨跌幅",
            lambda df: _pct_change(_col(df, "close"), 60),
            "%",
            "越大越强",
            61,
        ),
        (
            "ma20",
            "20日均线价",
            lambda df: _ma(_col(df, "close"), 20),
            "元",
            "",
            20,
        ),
        (
            "ma60",
            "60日均线价",
            lambda df: _ma(_col(df, "close"), 60),
            "元",
            "",
            60,
        ),
        (
            "dist_to_ma20",
            "收盘价相对20日均线的偏离度",
            lambda df: (
                (c[-1] / np.mean(c[-20:]) - 1.0) * 100.0
                if (c := _col(df, "close")) is not None and len(c) >= 20 and np.mean(c[-20:]) != 0
                else _nan()
            ),
            "%",
            "正值表示在均线上方",
            20,
        ),
        (
            "vol_ratio_5_20",
            "量比：近5日均量 / 近20日均量",
            lambda df: (
                float(np.mean(v[-5:]) / np.mean(v[-20:]))
                if (v := _col(df, "volume")) is not None
                and len(v) >= 20
                and np.mean(v[-20:]) > 0
                else _nan()
            ),
            "倍",
            "大于1表示近期放量",
            20,
        ),
        (
            "volatility_20",
            "近20日日收益率的年化波动率",
            lambda df: (
                float(np.std(np.diff(np.log(c[-21:])), ddof=1) * np.sqrt(244) * 100.0)
                if (c := _col(df, "close")) is not None
                and len(c) >= 21
                and np.all(c[-21:] > 0)
                else _nan()
            ),
            "%",
            "越大波动越剧烈",
            21,
        ),
        (
            "above_ma20_days",
            "连续收在20日均线上方的天数",
            lambda df: _count_streak(_col(df, "close"), 20),
            "天",
            "越大趋势越稳",
            20,
        ),
        (
            "amp_20",
            "近20日振幅（区间最高/最低-1）",
            lambda df: (
                (float(np.max(h[-20:])) / float(np.min(lo[-20:])) - 1.0) * 100.0
                if (h := _col(df, "high")) is not None
                and (lo := _col(df, "low")) is not None
                and len(h) >= 20
                and np.min(lo[-20:]) > 0
                else _nan()
            ),
            "%",
            "越大区间越宽",
            20,
        ),
    ]


def _count_streak(close: Optional[np.ndarray], window: int) -> float:
    """从最后一根 bar 往前数，连续收在 N 日均线上方的天数。"""
    if close is None or len(close) < window:
        return _nan()
    streak = 0
    for end in range(len(close), window - 1, -1):
        ma = float(np.mean(close[end - window : end]))
        if not np.isfinite(ma):
            break
        if close[end - 1] > ma:
            streak += 1
        else:
            break
    return float(streak)


def build_default_factor_registry() -> FactorRegistry:
    """构造内置因子注册中心。

    结构类因子（支撑阻力）默认**不**注册：它们需要逐标的运行 S/R 检测，
    成本远高于普通因子，应由调用方按需显式注册（见 register_level_factors）。
    """
    reg = FactorRegistry()
    for name, desc, fn, unit, hint, min_bars in _builtin_factors():
        reg.register(
            name,
            desc,
            fn,
            unit=unit,
            higher_means=hint,
            min_bars=min_bars,
            origin="builtin",
        )
    return reg


def register_level_factors(
    registry: FactorRegistry, detector: Any
) -> Callable[[], None]:
    """按需注册支撑阻力类因子（成本较高，全市场扫描前请评估耗时）。

    Args:
        registry: 因子注册中心
        detector: SupportResistanceDetector 实例

    Returns:
        disposer：调用即注销这两个因子
    """
    def _support_strength(df: pd.DataFrame) -> float:
        scan = detector.detect(df)
        return float(scan.nearest_support.strength) if scan.nearest_support else _nan()

    def _dist_to_support(df: pd.DataFrame) -> float:
        """到最近支撑区域的距离，以 ATR 为单位。正值=在支撑上方。"""
        scan = detector.detect(df)
        if scan.nearest_support is None or scan.atr <= 0:
            return _nan()
        return float(scan.distance_atr(scan.nearest_support))

    d1 = registry.register(
        "support_strength",
        "最近支撑区域的强度（0~1，越高说明该支撑被反复验证且伴随放量）",
        _support_strength,
        unit="",
        higher_means="越大支撑越可靠",
        min_bars=60,
        origin="levels",
    )
    d2 = registry.register(
        "dist_to_support",
        "当前价到最近支撑区域的距离，以ATR为单位",
        _dist_to_support,
        unit="ATR",
        higher_means="接近0表示正贴着支撑，越大表示离支撑越远",
        min_bars=60,
        origin="levels",
    )

    def dispose() -> None:
        d1()
        d2()

    return dispose


__all__ = [
    "FactorRegistry",
    "FactorSpec",
    "FactorFn",
    "build_default_factor_registry",
    "register_level_factors",
]
