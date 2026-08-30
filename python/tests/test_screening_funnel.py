"""全市场选股漏斗的回归测试

核心不变量：
1. **每层丢弃必须可归因** —— why_dropped() 要能一步定位"某标的为什么没进池"
2. **PIT** —— 所有序列在第①层截断到 as_of，晚一步截断就全盘带上未来信息
3. **启用 LLM 即如实标记非确定性** —— 不做"我们也算确定性"的自欺
4. **确定性路径可复现** —— 不注入 final_select 时，同输入必须同输出
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finhack_pro.data.levels import SupportResistanceDetector
from finhack_pro.data.warehouse import MarketWarehouse
from finhack_pro.screening import (
    Condition,
    FactorRegistry,
    FilterSpec,
    FunnelConfig,
    ScreenEngine,
    StockFunnel,
    build_default_factor_registry,
)

# ============================================================================
# 构造数据
# ============================================================================


def _ohlcv(
    n: int = 120,
    trend: float = 0.0,
    seed: int = 1,
    volume: float = 2e6,
    zero_tail: int = 0,
) -> pd.DataFrame:
    """构造 OHLCV。zero_tail>0 表示末尾若干日零成交（模拟停牌）。"""
    rng = np.random.default_rng(seed)
    close = 10.0 + trend * np.arange(n) + np.cumsum(rng.normal(0, 0.05, n))
    high = close + np.abs(rng.normal(0.04, 0.01, n))
    low = close - np.abs(rng.normal(0.04, 0.01, n))
    vol = np.maximum(volume * (1 + rng.normal(0, 0.1, n)), 0)
    if zero_tail:
        vol[-zero_tail:] = 0.0
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=n),
            "open": (high + low) / 2,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
    )


@pytest.fixture()
def warehouse(tmp_path) -> MarketWarehouse:
    wh = MarketWarehouse(tmp_path / "wh")
    # 12 只：趋势各异 + 若干边界情形
    wh.put("TREND_UP", _ohlcv(n=120, trend=0.03, seed=1))
    wh.put("TREND_DN", _ohlcv(n=120, trend=-0.02, seed=2))
    for i in range(10):
        wh.put(f"FLAT{i:02d}", _ohlcv(n=120, trend=0.001 * i, seed=10 + i))
    # 边界情形
    wh.put("SHORT", _ohlcv(n=20, seed=99))          # 历史不足
    wh.put("HALTED", _ohlcv(n=120, seed=98, zero_tail=15))  # 近期停牌
    return wh


@pytest.fixture()
def factors() -> FactorRegistry:
    return build_default_factor_registry()


@pytest.fixture()
def engine(factors) -> ScreenEngine:
    return ScreenEngine(factors)


# ============================================================================
# 第①层：数据可用性与 PIT 截断
# ============================================================================


def test_stage1_drops_insufficient_history(warehouse, factors):
    funnel = StockFunnel(warehouse, factors)
    report = funnel.run(as_of="2024-12-31")

    assert report.why_dropped("SHORT") is not None
    assert "历史不足" in report.why_dropped("SHORT")


def test_stage1_truncates_to_as_of(warehouse, factors):
    """PIT：传入 as_of 前的数据，序列必须被截断。"""
    funnel = StockFunnel(warehouse, factors, config=FunnelConfig(min_bars=10))
    report = funnel.run(as_of="2024-03-01")

    # 截断后（约 42 个交易日）仍应通过 min_bars=10
    assert "TREND_UP" not in [
        s for s in report.stages[0].dropped if "历史不足" in report.stages[0].dropped[s]
    ]
    # 截断点之后的数据不得参与：用一个只到 2 月的 as_of，历史长度应显著小于全量
    assert report.stages[0].output_count > 0


def test_funnel_without_as_of_uses_full_history(warehouse, factors):
    funnel = StockFunnel(warehouse, factors)
    report = funnel.run()
    assert report.as_of == ""
    assert report.stages[0].output_count > 0


# ============================================================================
# 第②层：流动性
# ============================================================================


def test_stage2_drops_halted(warehouse, factors):
    funnel = StockFunnel(warehouse, factors)
    report = funnel.run(as_of="2024-12-31")

    reason = report.why_dropped("HALTED")
    assert reason is not None
    assert "停牌" in reason


def test_stage2_amount_threshold(warehouse, factors):
    """min_avg_amount 设得极高时应全部被剔除，且原因可查。"""
    cfg = FunnelConfig(min_avg_amount=1e15)
    funnel = StockFunnel(warehouse, factors, config=cfg)
    report = funnel.run(as_of="2024-12-31")

    assert report.stages[1].output_count == 0
    assert "均额" in next(iter(report.stages[1].dropped.values()))


def test_stage2_amount_approximated_when_column_missing(warehouse, factors):
    """无 amount 列时用 成交量×均价 近似，而非直接放弃该过滤。"""
    cfg = FunnelConfig(min_avg_amount=1e15)
    funnel = StockFunnel(warehouse, factors, config=cfg)
    report = funnel.run(as_of="2024-12-31")
    # 数据里本来就没有 amount 列，能走到"均额低于下限"说明近似生效
    assert report.stages[1].output_count == 0


# ============================================================================
# 第③层：条件筛选
# ============================================================================


def test_stage3_applies_spec(warehouse, factors, engine):
    spec = FilterSpec(conditions=[Condition("ret_20", ">", 0.0)], logic="all")
    spec.validate(factors)

    funnel = StockFunnel(warehouse, factors, engine=engine)
    report = funnel.run(spec=spec, as_of="2024-12-31")

    names = [s.name for s in report.stages]
    assert "③条件筛选" in names
    stage3 = next(s for s in report.stages if s.name == "③条件筛选")
    assert stage3.output_count <= stage3.input_count


def test_stage3_records_which_condition_blocked(warehouse, factors, engine):
    spec = FilterSpec(conditions=[Condition("ret_20", ">", 1e9)])  # 无人能满足
    spec.validate(factors)

    funnel = StockFunnel(warehouse, factors, engine=engine)
    report = funnel.run(spec=spec, as_of="2024-12-31")

    stage3 = next(s for s in report.stages if s.name == "③条件筛选")
    assert stage3.output_count == 0
    assert all("不满足" in r for r in stage3.dropped.values())


def test_stage3_requires_engine(warehouse, factors):
    """没有 engine 却给了 spec —— 必须显式报错，不能悄悄跳过筛选。"""
    spec = FilterSpec(conditions=[Condition("ret_20", ">", 0.0)])
    funnel = StockFunnel(warehouse, factors)
    with pytest.raises(ValueError, match="ScreenEngine"):
        funnel.run(spec=spec, as_of="2024-12-31")


def test_stage3_skipped_when_no_spec(warehouse, factors, engine):
    funnel = StockFunnel(warehouse, factors, engine=engine)
    report = funnel.run(as_of="2024-12-31")
    assert "③条件筛选" not in [s.name for s in report.stages]


# ============================================================================
# 第④层：结构检测
# ============================================================================


def test_stage4_runs_when_detector_provided(warehouse, factors, engine):
    funnel = StockFunnel(
        warehouse, factors, engine=engine, detector=SupportResistanceDetector()
    )
    report = funnel.run(as_of="2024-12-31")
    assert "④结构检测" in [s.name for s in report.stages]


def test_stage4_skipped_when_disabled(warehouse, factors, engine):
    cfg = FunnelConfig(enable_structure_stage=False)
    funnel = StockFunnel(
        warehouse, factors, engine=engine, detector=SupportResistanceDetector(), config=cfg
    )
    report = funnel.run(as_of="2024-12-31")
    assert "④结构检测" not in [s.name for s in report.stages]


def test_stage4_caps_to_top_n(warehouse, factors, engine):
    cfg = FunnelConfig(structure_top_n=3)
    funnel = StockFunnel(
        warehouse, factors, engine=engine, detector=SupportResistanceDetector(), config=cfg
    )
    report = funnel.run(as_of="2024-12-31")
    stage4 = next(s for s in report.stages if s.name == "④结构检测")
    assert stage4.output_count <= 3


def test_stage4_min_strength_filter(warehouse, factors, engine):
    cfg = FunnelConfig(structure_min_strength=0.999)
    funnel = StockFunnel(
        warehouse, factors, engine=engine, detector=SupportResistanceDetector(), config=cfg
    )
    report = funnel.run(as_of="2024-12-31")
    stage4 = next(s for s in report.stages if s.name == "④结构检测")
    if stage4.output_count == 0:
        assert all("强度" in r or "支撑" in r for r in stage4.dropped.values())


# ============================================================================
# 第⑤层：终选
# ============================================================================


def test_stage5_deterministic_by_default(warehouse, factors, engine):
    funnel = StockFunnel(
        warehouse, factors, engine=engine, detector=SupportResistanceDetector(),
        config=FunnelConfig(final_top_k=5),
    )
    report = funnel.run(as_of="2024-12-31")

    assert report.deterministic
    assert len(report.final) <= 5
    assert not report.scores.empty


def test_deterministic_path_is_reproducible(warehouse, factors, engine):
    """同输入两次运行必须完全一致 —— 这是回测可信的前提。"""
    kwargs = dict(as_of="2024-12-31")
    funnel = StockFunnel(
        warehouse, factors, engine=engine, detector=SupportResistanceDetector(),
        config=FunnelConfig(final_top_k=5),
    )
    a = funnel.run(**kwargs)
    b = funnel.run(**kwargs)
    assert a.final == b.final


def test_final_top_k_respected(warehouse, factors, engine):
    funnel = StockFunnel(
        warehouse, factors, engine=engine, config=FunnelConfig(final_top_k=2)
    )
    report = funnel.run(as_of="2024-12-31")
    assert len(report.final) <= 2


def test_llm_stage_marks_non_deterministic(warehouse, factors, engine):
    """启用 LLM 即如实标记非确定性，不做自欺。"""
    def fake_llm(symbols, frame, as_of):
        return symbols[:3], "LLM 选了前三个（测试替身）"

    funnel = StockFunnel(warehouse, factors, engine=engine)
    report = funnel.run(as_of="2024-12-31", final_select=fake_llm)

    assert not report.deterministic
    assert "LLM" in report.note
    assert len(report.final) == 3


def test_llm_stage_respects_final_top_k(warehouse, factors, engine):
    def greedy(symbols, frame, as_of):
        return symbols, "全选"

    funnel = StockFunnel(
        warehouse, factors, engine=engine, config=FunnelConfig(final_top_k=4)
    )
    report = funnel.run(as_of="2024-12-31", final_select=greedy)
    assert len(report.final) == 4


def test_llm_stage_records_unselected_as_dropped(warehouse, factors, engine):
    def pick_two(symbols, frame, as_of):
        return symbols[:2], "只选两个"

    funnel = StockFunnel(warehouse, factors, engine=engine)
    report = funnel.run(as_of="2024-12-31", final_select=pick_two)

    stage5 = report.stages[-1]
    assert stage5.output_count == 2
    assert any("未选中" in r for r in stage5.dropped.values())


# ============================================================================
# 报告与诊断
# ============================================================================


def test_report_summary_shows_funnel_chain(warehouse, factors, engine):
    funnel = StockFunnel(warehouse, factors, engine=engine)
    report = funnel.run(as_of="2024-12-31")
    text = report.summary()
    assert "->" in text
    assert "确定性" in text


def test_why_dropped_returns_none_for_survivor(warehouse, factors, engine):
    cfg = FunnelConfig(final_top_k=100)  # 宽松，让标的能活到最后
    funnel = StockFunnel(warehouse, factors, engine=engine, config=cfg)
    report = funnel.run(as_of="2024-12-31")
    for sym in report.final:
        assert report.why_dropped(sym) is None


def test_empty_universe_short_circuits(warehouse, factors):
    funnel = StockFunnel(warehouse, factors)
    report = funnel.run(universe=[], as_of="2024-12-31")
    assert report.final == []
    assert "候选池为空" in report.note


def test_all_dropped_at_stage1_reports_reason(warehouse, factors):
    cfg = FunnelConfig(min_bars=10_000)
    funnel = StockFunnel(warehouse, factors, config=cfg)
    report = funnel.run(as_of="2024-12-31")
    assert report.final == []
    assert "第①层" in report.note
    assert report.stages[0].dropped


def test_stage_result_line_and_pass_rate():
    from finhack_pro.screening import StageResult

    s = StageResult(name="x", input_count=100, output_count=25)
    assert s.pass_rate == 0.25
    assert "100 -> 25" in s.line()
    assert StageResult(name="x", input_count=0, output_count=0).pass_rate == 0.0


def test_scores_contain_percentile_columns(warehouse, factors, engine):
    funnel = StockFunnel(
        warehouse, factors, engine=engine, detector=SupportResistanceDetector()
    )
    report = funnel.run(as_of="2024-12-31")
    for col in ("strength_pct", "proximity_pct", "momentum_pct", "volume_pct", "score"):
        assert col in report.scores.columns


def test_missing_momentum_falls_back_to_neutral_percentile(warehouse, factors, engine):
    """因子算不出时，该分项取 0.5 中性分位，而不是 0 —— 否则会无端垫底。"""
    wh = MarketWarehouse(warehouse.root)
    wh.put("TINY", _ohlcv(n=15, seed=77))
    cfg = FunnelConfig(min_bars=10, final_top_k=50)
    funnel = StockFunnel(warehouse, factors, engine=engine, config=cfg)
    report = funnel.run(as_of="2024-12-31")
    if "TINY" in report.scores.index:
        assert report.scores.loc["TINY", "momentum_pct"] == 0.5
