"""
全市场选股漏斗

为什么必须分层
--------------
5400 只全用最贵的方法跑一遍不可行。以支撑阻力结构检测为例，逐标的
拟合约需数十毫秒，5400 只就是几分钟；若再叠加 LLM，成本与延迟都不可接受。
而且绝大多数标的在第一、二层就该被排除 —— 对它们跑昂贵计算纯属浪费。

漏斗的作用是把**便宜且区分度高的过滤放在前面**，让昂贵方法只作用于
已经很小的候选集：

    5400  全市场（本地仓库）
     ↓  ① 数据可用性：历史长度、停牌
    3800  可评估
     ↓  ② 流动性：成交额下限、零成交占比
     ~800  可交易
     ↓  ③ 条件筛选：FilterSpec 确定性执行（便宜因子）
     300  符合条件
     ↓  ④ 结构检测：支撑阻力（逐标的拟合，最贵）
      60  结构最优
     ↓  ⑤ 终选：综合打分（默认确定性，可注入 LLM 辩论）
      20  最终股票池

四条纪律：

1. **每层丢弃必须可归因**。StageResult.dropped 记录 {symbol: 原因}。
   "股票池为什么是空的"必须能一步查到，而不是靠猜。
2. **截断到 as_of**（PIT）。第一层就把序列截断到决策日 —— 若晚一步，
   后续所有因子与结构检测都会带上未来信息，且错误无法在下游修正。
3. **LLM 是可选的第 ⑤ 层，不是过滤器**。默认走确定性综合打分；
   注入 LLM 后它只在 60 进 20 时出场，产出的是**排序意见**而非筛选条件，
   且必须连同理由一并返回以便审计。
4. **启用 LLM 即放弃可复现**。FunnelReport.deterministic 会如实标记 False，
   不做"我们也算确定性"的自欺。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

#: 终选函数：(候选 symbol 列表, 因子值表, as_of) -> (选中的 symbol 列表, 说明)
FinalSelectFn = Callable[[List[str], pd.DataFrame, str], Tuple[List[str], str]]


@dataclass
class StageResult:
    """单层漏斗的结果"""

    name: str
    input_count: int
    output_count: int
    dropped: Dict[str, str] = field(default_factory=dict)  # symbol -> 原因
    elapsed_sec: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        return round(self.output_count / self.input_count, 4) if self.input_count else 0.0

    def line(self) -> str:
        return (
            f"{self.name}: {self.input_count} -> {self.output_count} "
            f"(通过率 {self.pass_rate:.1%}, {self.elapsed_sec:.2f}s)"
        )


@dataclass
class FunnelReport:
    """一次漏斗运行的完整报告"""

    as_of: str
    stages: List[StageResult] = field(default_factory=list)
    final: List[str] = field(default_factory=list)
    scores: pd.DataFrame = field(default_factory=pd.DataFrame)
    note: str = ""
    deterministic: bool = True
    elapsed_sec: float = 0.0

    def summary(self) -> str:
        chain = " -> ".join(
            [str(self.stages[0].input_count)] + [str(s.output_count) for s in self.stages]
        ) if self.stages else "（未运行）"
        det = "确定性" if self.deterministic else "含 LLM（非确定性）"
        text = f"选股漏斗 {chain}｜{det}｜耗时 {self.elapsed_sec:.1f}s"
        return f"{text}\n{self.note}" if self.note else text

    def why_dropped(self, symbol: str) -> Optional[str]:
        """查询某标的在哪一层、因何被淘汰。"""
        for stage in self.stages:
            if symbol in stage.dropped:
                return f"[{stage.name}] {stage.dropped[symbol]}"
        return None


@dataclass
class FunnelConfig:
    """漏斗参数"""

    #: ① 数据可用性
    min_bars: int = 60
    #: ② 流动性（0 表示不过滤）
    min_avg_amount: float = 0.0
    liquidity_window: int = 20
    max_zero_volume_ratio: float = 0.3   # 近 N 日零成交占比上限，用于剔除长期停牌
    #: ④ 结构检测
    enable_structure_stage: bool = True
    structure_top_n: int = 60
    structure_min_strength: float = 0.0
    #: ⑤ 终选
    final_top_k: int = 20
    #: 综合打分权重（经验值，非拟合结果；做因子化研究时应改为可配置并做敏感性测试）
    w_strength: float = 0.40
    w_proximity: float = 0.30
    w_momentum: float = 0.20
    w_volume: float = 0.10


class StockFunnel:
    """全市场选股漏斗

    Args:
        warehouse: MarketWarehouse 实例（数据源）
        factors: 因子注册中心
        detector: SupportResistanceDetector，第 ④ 层需要；为 None 则跳过该层
        engine: ScreenEngine，第 ③ 层需要
        config: FunnelConfig
    """

    def __init__(
        self,
        warehouse: Any,
        factors: Any,
        detector: Optional[Any] = None,
        engine: Optional[Any] = None,
        config: Optional[FunnelConfig] = None,
    ) -> None:
        self.warehouse = warehouse
        self.factors = factors
        self.detector = detector
        self.engine = engine
        self.config = config or FunnelConfig()

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(
        self,
        spec: Any = None,
        as_of: str = "",
        start: str = "",
        universe: Optional[Sequence[str]] = None,
        final_select: Optional[FinalSelectFn] = None,
    ) -> FunnelReport:
        """运行漏斗。

        Args:
            spec: FilterSpec，第 ③ 层用；为 None 时跳过条件筛选
            as_of: 决策日。**所有序列都会截断到此日**（PIT）
            start: 数据起始日；空则取仓库全量
            universe: 候选池；None 时用仓库中全部标的
            final_select: 终选函数；None 时用确定性综合打分

        Returns:
            FunnelReport
        """
        started = time.time()
        report = FunnelReport(as_of=as_of)

        symbols = list(universe) if universe is not None else self.warehouse.symbols()
        if not symbols:
            report.note = "候选池为空，漏斗未运行"
            return report

        # ---- ① 数据可用性 + 截断到 as_of ----
        data, stage = self._stage_availability(symbols, start, as_of)
        report.stages.append(stage)
        if not data:
            report.note = f"第①层后无可用标的：{len(stage.dropped)} 只全部被剔除"
            report.elapsed_sec = round(time.time() - started, 2)
            return report

        # ---- ② 流动性 ----
        data, stage = self._stage_liquidity(data)
        report.stages.append(stage)
        if not data:
            report.note = "第②层后无可用标的：流动性过滤过严，请调低 min_avg_amount"
            report.elapsed_sec = round(time.time() - started, 2)
            return report

        # ---- ③ 条件筛选 ----
        if spec is not None:
            if self.engine is None:
                raise ValueError("第③层需要 ScreenEngine，请在构造时传入 engine")
            data, stage = self._stage_screen(data, spec)
            report.stages.append(stage)
            if not data:
                report.note = "第③层后无可用标的：筛选条件过严，或因子普遍不可用"
                report.elapsed_sec = round(time.time() - started, 2)
                return report

        # ---- ④ 结构检测 ----
        if self.config.enable_structure_stage and self.detector is not None:
            data, stage = self._stage_structure(data)
            report.stages.append(stage)
            if not data:
                report.note = "第④层后无可用标的：结构检测全部失败或强度不达标"
                report.elapsed_sec = round(time.time() - started, 2)
                return report

        # ---- ⑤ 终选 ----
        data, stage, scores, note = self._stage_final(data, as_of, final_select)
        report.stages.append(stage)
        report.final = data
        report.scores = scores
        report.note = note
        if final_select is not None:
            report.deterministic = False

        report.elapsed_sec = round(time.time() - started, 2)
        logger.info(report.summary())
        return report

    # ------------------------------------------------------------------
    # 各层
    # ------------------------------------------------------------------

    def _stage_availability(
        self, symbols: Sequence[str], start: str, as_of: str
    ) -> Tuple[Dict[str, pd.DataFrame], StageResult]:
        """读取并截断到 as_of。历史不足者剔除。"""
        t0 = time.time()
        data: Dict[str, pd.DataFrame] = {}
        dropped: Dict[str, str] = {}

        for sym in symbols:
            df = self.warehouse.get(sym, start=start, end=as_of, freq="daily")
            if df is None or df.empty:
                dropped[sym] = "仓库中无数据或未覆盖该区间"
                continue
            if len(df) < self.config.min_bars:
                dropped[sym] = f"历史不足 {self.config.min_bars} 根（实际 {len(df)}）"
                continue
            # PIT：截断到决策日。晚一步截断，后续所有计算都会带上未来信息。
            data[sym] = df.reset_index(drop=True)

        return data, StageResult(
            name="①数据可用性",
            input_count=len(symbols),
            output_count=len(data),
            dropped=dropped,
            elapsed_sec=round(time.time() - t0, 2),
            detail={"min_bars": self.config.min_bars, "as_of": as_of or "（全部）"},
        )

    def _stage_liquidity(
        self, data: Dict[str, pd.DataFrame]
    ) -> Tuple[Dict[str, pd.DataFrame], StageResult]:
        """剔除成交额过低与长期停牌的标的。

        停牌期间成交量为 0，若不过滤，"零成交"会被后续因子当成真实数据
        （例如量比算成 0 或除零），污染整个筛选。
        """
        t0 = time.time()
        kept: Dict[str, pd.DataFrame] = {}
        dropped: Dict[str, str] = {}
        cfg = self.config
        window = cfg.liquidity_window

        for sym, df in data.items():
            vol = df["volume"].to_numpy(dtype=float)[-window:] if "volume" in df else None
            if vol is None or len(vol) == 0:
                dropped[sym] = "缺少成交量列"
                continue
            zero_ratio = float(np.mean(vol <= 0))
            if zero_ratio > cfg.max_zero_volume_ratio:
                dropped[sym] = f"近{window}日零成交占比 {zero_ratio:.0%}，疑似停牌"
                continue
            if cfg.min_avg_amount > 0:
                if "amount" in df.columns:
                    avg_amount = float(np.mean(df["amount"].to_numpy(dtype=float)[-window:]))
                else:
                    # 无成交额列时用 成交量×均价 近似，避免直接放弃该过滤
                    close = df["close"].to_numpy(dtype=float)[-window:]
                    avg_amount = float(np.mean(vol * close))
                if avg_amount < cfg.min_avg_amount:
                    dropped[sym] = f"近{window}日均额 {avg_amount:.0f} 低于下限 {cfg.min_avg_amount:.0f}"
                    continue
            kept[sym] = df

        return kept, StageResult(
            name="②流动性",
            input_count=len(data),
            output_count=len(kept),
            dropped=dropped,
            elapsed_sec=round(time.time() - t0, 2),
            detail={
                "min_avg_amount": cfg.min_avg_amount,
                "max_zero_volume_ratio": cfg.max_zero_volume_ratio,
            },
        )

    def _stage_screen(
        self, data: Dict[str, pd.DataFrame], spec: Any
    ) -> Tuple[Dict[str, pd.DataFrame], StageResult]:
        """按 FilterSpec 确定性筛选。"""
        t0 = time.time()
        result = self.engine.screen(data, spec)

        passed = set(result.symbols)
        kept = {s: df for s, df in data.items() if s in passed}
        dropped: Dict[str, str] = {}
        # 未命中者：记录它卡在哪个条件上，便于用户调参
        for sym, df in data.items():
            if sym in passed:
                continue
            values = {f: self.factors.compute(f, df) for f in spec.required_fields()}
            failed = [
                str(c)
                for c in spec.conditions
                if not np.isnan(values.get(c.field, np.nan))
                and not c.evaluate(values[c.field])
            ]
            dropped[sym] = "不满足: " + "、".join(failed) if failed else "未命中"
        dropped.update(result.skipped)

        return kept, StageResult(
            name="③条件筛选",
            input_count=len(data),
            output_count=len(kept),
            dropped=dropped,
            elapsed_sec=round(time.time() - t0, 2),
            detail={"spec": spec.summary(), "skipped": len(result.skipped)},
        )

    def _stage_structure(
        self, data: Dict[str, pd.DataFrame]
    ) -> Tuple[Dict[str, pd.DataFrame], StageResult]:
        """支撑阻力结构检测（最贵的一层，故放在最后）。"""
        t0 = time.time()
        cfg = self.config
        scored: List[Tuple[str, float]] = []
        dropped: Dict[str, str] = {}

        for sym, df in data.items():
            try:
                scan = self.detector.detect(df, symbol=sym)
            except Exception as e:
                dropped[sym] = f"结构检测失败: {type(e).__name__}: {e}"
                continue
            if scan.nearest_support is None:
                dropped[sym] = "未识别到有效支撑区域"
                continue
            if scan.nearest_support.strength < cfg.structure_min_strength:
                dropped[sym] = (
                    f"支撑强度 {scan.nearest_support.strength:.3f} "
                    f"低于下限 {cfg.structure_min_strength}"
                )
                continue
            # 贴近支撑 + 强支撑 = 好位置；用 ATR 距离做贴近度衰减
            proximity = float(np.exp(-abs(scan.distance_atr(scan.nearest_support))))
            scored.append((sym, 0.6 * scan.nearest_support.strength + 0.4 * proximity))

        scored.sort(key=lambda t: t[1], reverse=True)
        kept_syms = [s for s, _ in scored[: cfg.structure_top_n]]
        kept = {s: data[s] for s in kept_syms}
        for sym, _ in scored[cfg.structure_top_n :]:
            dropped[sym] = f"结构得分未进入前 {cfg.structure_top_n}"

        return kept, StageResult(
            name="④结构检测",
            input_count=len(data),
            output_count=len(kept),
            dropped=dropped,
            elapsed_sec=round(time.time() - t0, 2),
            detail={
                "top_n": cfg.structure_top_n,
                "min_strength": cfg.structure_min_strength,
                "failed": sum(1 for v in dropped.values() if "失败" in v),
            },
        )

    def _stage_final(
        self,
        data: Dict[str, pd.DataFrame],
        as_of: str,
        final_select: Optional[FinalSelectFn],
    ) -> Tuple[List[str], StageResult, pd.DataFrame, str]:
        """终选：默认确定性综合打分，可注入 LLM 辩论。"""
        t0 = time.time()
        cfg = self.config
        symbols = list(data.keys())

        if final_select is not None:
            chosen, note = final_select(symbols, pd.DataFrame(), as_of)
            return (
                list(chosen)[: cfg.final_top_k],
                StageResult(
                    name="⑤终选(LLM)",
                    input_count=len(symbols),
                    output_count=min(len(chosen), cfg.final_top_k),
                    dropped={
                        s: "LLM 未选中" for s in symbols if s not in set(chosen)
                    },
                    elapsed_sec=round(time.time() - t0, 2),
                ),
                pd.DataFrame(),
                note,
            )

        if not symbols:
            return [], StageResult("⑤终选(打分)", 0, 0), pd.DataFrame(), "无候选"

        scores = self._composite_scores(data)
        ranked = scores.sort_values("score", ascending=False)
        chosen = ranked.index.tolist()[: cfg.final_top_k]
        dropped = {
            s: f"综合得分 {scores.loc[s, 'score']:.4f} 未进入前 {cfg.final_top_k}"
            for s in ranked.index.tolist()[cfg.final_top_k :]
        }

        return (
            chosen,
            StageResult(
                name="⑤终选(打分)",
                input_count=len(symbols),
                output_count=len(chosen),
                dropped=dropped,
                elapsed_sec=round(time.time() - t0, 2),
                detail={"weights": self._weights()},
            ),
            scores,
            f"终选依据：支撑强度 {cfg.w_strength} / 贴近度 {cfg.w_proximity} / "
            f"动量 {cfg.w_momentum} / 量能 {cfg.w_volume}（经验权重，非拟合结果）",
        )

    def _weights(self) -> Dict[str, float]:
        c = self.config
        return {
            "strength": c.w_strength,
            "proximity": c.w_proximity,
            "momentum": c.w_momentum,
            "volume": c.w_volume,
        }

    def _composite_scores(self, data: Dict[str, pd.DataFrame]) -> pd.DataFrame:
        """综合打分。各分项先做横截面分位归一，避免量纲不同的因子互相压制。"""
        cfg = self.config
        rows: Dict[str, Dict[str, float]] = {}

        for sym, df in data.items():
            row: Dict[str, float] = {}
            if self.detector is not None:
                try:
                    scan = self.detector.detect(df, symbol=sym)
                    if scan.nearest_support is not None:
                        row["strength"] = float(scan.nearest_support.strength)
                        row["proximity"] = float(
                            np.exp(-abs(scan.distance_atr(scan.nearest_support)))
                        )
                except Exception as e:
                    logger.debug("终选打分：%s 结构检测失败 %s", sym, e)
            for fname, key in (("ret_20", "momentum"), ("vol_ratio_5_20", "volume")):
                value = self.factors.compute(fname, df)
                row[key] = float(value) if value == value else np.nan
            rows[sym] = row

        frame = pd.DataFrame.from_dict(rows, orient="index")
        for col in ("strength", "proximity", "momentum", "volume"):
            if col not in frame.columns:
                frame[col] = np.nan
            # 横截面分位（0~1）；全 NaN 时该分项权重自动失效（填 0.5 中性值）
            ranked = frame[col].rank(pct=True)
            frame[f"{col}_pct"] = ranked.fillna(0.5)

        frame["score"] = (
            cfg.w_strength * frame["strength_pct"]
            + cfg.w_proximity * frame["proximity_pct"]
            + cfg.w_momentum * frame["momentum_pct"]
            + cfg.w_volume * frame["volume_pct"]
        )
        return frame


__all__ = [
    "StockFunnel",
    "FunnelConfig",
    "FunnelReport",
    "StageResult",
    "FinalSelectFn",
]
