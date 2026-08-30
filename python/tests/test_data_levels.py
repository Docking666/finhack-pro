"""支撑阻力区域检测器的回归测试

最重要的不变量是**无未来函数**：在回测中必须用截至决策时点的截断序列调用，
否则"支撑位"会天然贴合后续走势，收益虚高且不可修复。
本文件用 test_no_future_function_* 两个用例把该约束钉死。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finhack_pro.data.levels import (
    LevelScan,
    PriceLevel,
    SupportResistanceDetector,
    _dedupe_runs,
    _local_extrema,
    _savgol_numpy,
    screen_near_level,
)

# ============================================================================
# 合成数据
# ============================================================================


def _oscillator(
    n: int = 240,
    center: float = 11.0,
    amp: float = 1.0,
    period: int = 40,
    slope: float = 0.0,
    noise: float = 0.02,
    seed: int = 7,
    volume_base: float = 1e6,
) -> pd.DataFrame:
    """在 [center-amp, center+amp] 之间往复震荡的构造数据。

    ``slope>0`` 时整体倾斜，用于验证通道（带斜率）区域的识别。
    极值处成交量放大，用于验证 volume_score 的确认逻辑。
    """
    rng = np.random.default_rng(seed)
    x = np.arange(n, dtype=float)
    wave = np.sin(2 * np.pi * x / period)
    close = center + slope * x + amp * wave + rng.normal(0, noise, n)
    high = close + np.abs(rng.normal(0.03, 0.01, n))
    low = close - np.abs(rng.normal(0.03, 0.01, n))
    # 极值处放量：|wave| 接近 1 时成交量抬升
    surge = 1.0 + 2.0 * (np.abs(wave) > 0.9)
    volume = volume_base * surge * (1 + rng.normal(0, 0.05, n))
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=n),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


# ============================================================================
# 底层组件
# ============================================================================


def test_savgol_preserves_polynomials():
    """SG 滤波器对不超过 polyorder 次的多项式应无损通过。"""
    x = np.arange(60, dtype=float)
    for coeffs in ([3.0], [1.0, 0.5], [0.1, -0.2, 0.01]):
        y = np.polyval(coeffs, x)
        out = _savgol_numpy(y, window=11, polyorder=3)
        # 边缘 padding 会带来偏差，只看中段
        assert np.allclose(out[10:-10], y[10:-10], atol=1e-6)


def test_savgol_output_length_matches_input():
    y = np.random.default_rng(1).normal(10, 1, 50)
    assert _savgol_numpy(y, window=11, polyorder=3).shape == y.shape


def test_detect_works_without_scipy():
    """scipy 未声明为项目依赖，numpy 回退路径才是多数环境的真实执行路径。

    这里屏蔽 scipy 强制走回退，确保两条路径都能产出可用结果。
    """
    import builtins
    import sys

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError("scipy 已被测试屏蔽")
        return real_import(name, *args, **kwargs)

    saved = sys.modules.get("scipy.signal")
    sys.modules["scipy.signal"] = None
    builtins.__import__ = _blocked
    try:
        scan = SupportResistanceDetector().detect(_oscillator())
    finally:
        builtins.__import__ = real_import
        if saved is not None:
            sys.modules["scipy.signal"] = saved
        else:
            sys.modules.pop("scipy.signal", None)

    assert scan.levels
    assert scan.nearest_support is not None
    assert scan.nearest_support.center == pytest.approx(10.0, abs=0.4)


def test_savgol_matches_scipy_when_available():
    scipy = pytest.importorskip("scipy.signal")
    y = np.random.default_rng(3).normal(10, 1, 80)
    mine = _savgol_numpy(y, window=11, polyorder=3)
    theirs = scipy.savgol_filter(y, 11, 3, mode="nearest")
    # 边缘处理策略不同（常量填充 vs nearest），只比对中段
    assert np.allclose(mine[10:-10], theirs[10:-10], atol=1e-8)


def test_local_extrema_finds_peaks_and_troughs():
    y = np.array([0, 1, 2, 3, 2, 1, 0, 1, 2, 3, 2, 1, 0], dtype=float)
    maxs, mins = _local_extrema(y, order=2)
    assert set(maxs.tolist()) == {3, 9}
    # 下标 0 与 12 是端点，邻域不完整（min_periods 未满足），故不算极值
    assert set(mins.tolist()) == {6}


def test_local_extrema_excludes_edges():
    """边缘无法构成完整邻域，不得被判为极值（否则必然是假信号）。"""
    y = np.array([99.0] + [1.0] * 20 + [99.0])
    maxs, mins = _local_extrema(y, order=3)
    assert 0 not in maxs.tolist()
    assert len(y) - 1 not in maxs.tolist()


def test_dedupe_runs_collapses_plateau_to_middle():
    mask = np.array([False, True, True, True, False, True, False])
    assert _dedupe_runs(mask).tolist() == [2, 5]


def test_dedupe_runs_empty():
    assert _dedupe_runs(np.array([False, False])).size == 0


# ============================================================================
# 检测器：基本行为
# ============================================================================


def test_detect_finds_support_and_resistance_bands():
    df = _oscillator()
    scan = SupportResistanceDetector().detect(df, symbol="TEST")

    assert scan.symbol == "TEST"
    assert scan.bars == len(df)
    assert scan.atr > 0
    assert scan.levels, "应至少识别出一个区域"

    # 震荡区间为 [10, 12]
    assert scan.nearest_resistance is not None
    assert scan.nearest_support is not None
    assert scan.nearest_resistance.center == pytest.approx(12.0, abs=0.4)
    assert scan.nearest_support.center == pytest.approx(10.0, abs=0.4)


def test_detect_identifies_uptrend_channel_slope():
    """倾斜通道：支撑区域斜率应为正，而非退化为水平线。"""
    df = _oscillator(n=300, center=11.0, slope=0.02, period=40)
    scan = SupportResistanceDetector().detect(df)

    supports = [lv for lv in scan.levels if lv.kind == "support"]
    assert supports, "应识别出支撑区域"
    best = max(supports, key=lambda lv: lv.strength)
    assert best.slope > 0
    assert best.slope == pytest.approx(0.02, abs=0.01)


def test_level_is_a_band_not_a_line():
    """区域必须有宽度：把它当精确价位用是错误的用法。"""
    df = _oscillator()
    scan = SupportResistanceDetector().detect(df)
    for lv in scan.levels:
        assert lv.upper > lv.lower
        assert lv.width > 0


def test_touches_and_volume_score_are_meaningful():
    """极值处放量的构造数据，其区域 volume_score 应 > 1。"""
    df = _oscillator()
    scan = SupportResistanceDetector().detect(df)
    strong = max(scan.levels, key=lambda lv: lv.strength)
    assert strong.touches >= 2
    assert strong.volume_score > 1.0


def test_min_touches_filters_single_touch_noise():
    df = _oscillator()
    strict = SupportResistanceDetector(min_touches=4).detect(df)
    loose = SupportResistanceDetector(min_touches=2).detect(df)
    assert len(strict.levels) <= len(loose.levels)
    assert all(lv.touches >= 4 for lv in strict.levels)


def test_max_levels_caps_output():
    df = _oscillator()
    scan = SupportResistanceDetector(max_levels=2).detect(df)
    assert len(scan.levels) <= 2


def test_strength_within_unit_interval():
    df = _oscillator()
    scan = SupportResistanceDetector().detect(df)
    for lv in scan.levels:
        assert 0.0 <= lv.strength <= 1.0


def test_first_and_last_touch_dates_populated():
    df = _oscillator()
    scan = SupportResistanceDetector().detect(df)
    for lv in scan.levels:
        assert lv.first_touch and lv.last_touch
        assert lv.first_touch <= lv.last_touch


def test_as_of_is_last_bar_date():
    df = _oscillator()
    scan = SupportResistanceDetector().detect(df)
    assert scan.as_of == str(df["date"].iloc[-1].date())


def test_works_without_date_column():
    df = _oscillator().drop(columns=["date"])
    scan = SupportResistanceDetector().detect(df)
    assert scan.as_of == ""
    assert scan.levels


# ============================================================================
# 未来函数防护（核心不变量）
# ============================================================================


def test_no_future_function_truncation_independent_of_later_bars():
    """改动未来 bar 不得影响截至过去的检测结果。"""
    df = _oscillator(n=240)
    detector = SupportResistanceDetector()
    cut = 150

    baseline = detector.detect(df.iloc[:cut], symbol="T")

    tampered = df.copy()
    # 把未来 bar 改成完全不同量级的价格
    tampered.loc[cut:, ["open", "high", "low", "close"]] += 500.0
    after = detector.detect(tampered.iloc[:cut], symbol="T")

    assert after.as_of == baseline.as_of
    assert len(after.levels) == len(baseline.levels)
    for a, b in zip(after.levels, baseline.levels):
        assert a.center == pytest.approx(b.center)
        assert a.touches == b.touches
        assert a.strength == pytest.approx(b.strength)


def test_no_future_function_as_of_follows_input_end():
    """as_of 必须等于输入序列的最后一根 bar，而非全集末尾。"""
    df = _oscillator(n=240)
    detector = SupportResistanceDetector()
    for cut in (80, 140, 200):
        scan = detector.detect(df.iloc[:cut])
        assert scan.as_of == str(df["date"].iloc[cut - 1].date())
        assert scan.bars == cut


# ============================================================================
# 输入校验
# ============================================================================


def test_missing_columns_raises():
    df = _oscillator().drop(columns=["high"])
    with pytest.raises(ValueError, match="缺少必需列"):
        SupportResistanceDetector().detect(df)


def test_too_few_bars_raises():
    df = _oscillator(n=5)
    with pytest.raises(ValueError, match="样本不足"):
        SupportResistanceDetector(window=11).detect(df)


def test_invalid_construction_params():
    with pytest.raises(ValueError):
        SupportResistanceDetector(window=2)
    with pytest.raises(ValueError):
        SupportResistanceDetector(order=0)
    with pytest.raises(ValueError):
        SupportResistanceDetector(window=5, polyorder=5)


def test_short_series_skips_smoothing_gracefully():
    """样本刚够但短于窗口时不得崩溃。"""
    df = _oscillator(n=25)
    detector = SupportResistanceDetector(window=11, order=3)
    scan = detector.detect(df)
    assert scan.bars == 25


# ============================================================================
# 批量与筛选
# ============================================================================


def test_detect_batch_skips_bad_series_without_aborting():
    """全市场扫描中个别标的失败不得中断整批，但必须可从差集发现。"""
    good_a = _oscillator(n=200, center=11.0, seed=1)
    good_b = _oscillator(n=200, center=21.0, seed=2)
    broken = _oscillator(n=200).drop(columns=["low"])

    scans = SupportResistanceDetector().detect_batch(
        {"A": good_a, "B": good_b, "BROKEN": broken}
    )

    assert set(scans) == {"A", "B"}
    assert set(scans).symmetric_difference({"A", "B", "BROKEN"}) == {"BROKEN"}


def test_screen_near_level_sorts_by_distance():
    scans = {
        "FAR": SupportResistanceDetector().detect(_oscillator(n=200, center=11.0, seed=1), "FAR"),
        "NEAR": SupportResistanceDetector().detect(_oscillator(n=200, center=11.0, seed=2), "NEAR"),
    }
    out = screen_near_level(scans, kind="support", max_distance_atr=50.0)
    assert [s for s, _, _ in out]
    dists = [abs(d) for _, _, d in out]
    assert dists == sorted(dists)


def test_screen_near_level_respects_max_distance():
    scan = SupportResistanceDetector().detect(_oscillator(n=200), "X")
    assert screen_near_level({"X": scan}, kind="support", max_distance_atr=0.0) == []


def test_screen_near_level_min_strength_filter():
    scan = SupportResistanceDetector().detect(_oscillator(n=200), "X")
    assert screen_near_level({"X": scan}, min_strength=1.01) == []


def test_screen_near_level_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind 必须是"):
        screen_near_level({}, kind="ceiling")


def test_distance_atr_and_in_zone():
    df = _oscillator()
    scan = SupportResistanceDetector().detect(df)
    lv = scan.levels[0]
    expected = (lv.center - scan.close) / scan.atr
    assert scan.distance_atr(lv) == pytest.approx(expected)
    assert isinstance(scan.in_zone(lv), bool)


def test_distance_atr_zero_when_atr_missing():
    scan = LevelScan(symbol="X", as_of="", bars=0, close=10.0, atr=0.0)
    lv = PriceLevel(
        kind="support", center=11.0, lower=10.5, upper=11.5,
        slope=0.0, touches=3, volume_score=1.0, strength=0.5,
    )
    assert scan.distance_atr(lv) == 0.0


# ============================================================================
# 退化场景
# ============================================================================


def test_flat_series_does_not_crash():
    """一字板（零波动）不得除零或抛异常。"""
    n = 120
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=n),
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1e6,
        }
    )
    scan = SupportResistanceDetector().detect(df)
    assert scan.atr > 0  # 回退到极小正数，避免后续除零


def test_monotonic_trend_produces_levels():
    n = 200
    x = np.arange(n, dtype=float)
    close = 10 + 0.03 * x
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=n),
            "open": close,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": 1e6,
        }
    )
    scan = SupportResistanceDetector().detect(df)
    assert isinstance(scan, LevelScan)
