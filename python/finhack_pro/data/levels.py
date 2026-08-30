"""
支撑 / 阻力位检测（Support & Resistance）

为什么是"区域"而不是"线"
------------------------
把支撑阻力当成一条精确的水平线是新手最常见的错误。真实市场中它是一个**带
斜率的带状区域**：价格在同一区域反复受阻，但每次的精确价位不同；且区域本身
常随时间倾斜（上行通道的下轨、下行通道的上轨）。本模块对每簇极值做最小二乘
拟合，输出的是带 ``slope`` 的中心线与上下沿，而非单一价位。

算法范式（**独立实现**，未参考任何第三方项目代码）
------------------------------------------------
1. **平滑**  Savitzky-Golay 保形平滑，抑制噪声极值。优于简单均线：不过度削峰，
   能保留真实的转折点位置。缺 scipy 时用 numpy 手写同款滤波器（见 ``_savgol``）。
2. **极值**  在平滑序列上取局部极值（左右各 ``order`` 根 K 线的邻域内为最值）。
3. **聚类**  价格间距小于 ``merge_atr_mult × ATR`` 的极值合并为同一区域。
   阈值随波动率自适应 —— 高波动标的自然得到更宽的聚类，避免硬编码价差。
4. **拟合**  对每簇 ``(bar_index, price)`` 做最小二乘线性拟合，得到带斜率的区域。
5. **确认**  统计价格触及该区域的 K 线数（``touches``），并用
   "触及日均量 / 全样本均量" 作为成交量确认分 ``volume_score``。
   只有多次触碰且伴随放量的区域才是真结构，单点是噪声。
6. **评分**  ``strength`` 综合触碰次数（45%）、成交量确认（30%）、时间衰减（25%）。

未来函数警告（务必阅读）
-----------------------
``detect()`` 只使用传入 DataFrame 中**已有的** bar。它本身无未来函数，
但**调用方有责任**在回测中传入截至决策时点的截断序列 —— 若把整段历史
（含未来）传进去，得到的"支撑位"天然贴合后续走势，回测收益会虚高且不可修复。

正确用法（回测）::

    for i in range(lookback, len(df)):
        scan = detector.detect(df.iloc[:i])      # 只用 i 之前的 bar
        ...

许可证说明
----------
开源社区存在同主题的 GPL-3.0 项目（如 Detect_support_and_resistance_levels）。
本模块**未参考其任何代码**，仅采用上述公开的、教科书级的算法步骤独立实现，
以保持 FinHack Pro 的 MIT 许可不被传染。后续维护请勿直接粘贴 GPL 代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class PriceLevel:
    """一个支撑 / 阻力区域"""

    kind: str               # "support" | "resistance"
    center: float           # 最新 bar 处的区域中心价（随 slope 外推）
    lower: float            # 区域下沿
    upper: float            # 区域上沿
    slope: float            # 每根 K 线的价格变化；>0 上行通道，<0 下行通道
    touches: int            # 触及该区域的 K 线数
    volume_score: float     # 触及日均量 / 全样本均量；1.0 = 与均量持平
    strength: float         # 综合强度 0~1
    first_touch: str = ""   # 首次触及日期 (YYYY-MM-DD)
    last_touch: str = ""    # 最近触及日期 (YYYY-MM-DD)

    @property
    def width(self) -> float:
        return self.upper - self.lower


@dataclass
class LevelScan:
    """单标的扫描结果"""

    symbol: str
    as_of: str                       # 最后一根 bar 的日期
    bars: int
    close: float
    atr: float
    levels: List[PriceLevel] = field(default_factory=list)
    nearest_support: Optional[PriceLevel] = None
    nearest_resistance: Optional[PriceLevel] = None

    def distance_atr(self, level: PriceLevel) -> float:
        """当前价到区域中心的距离，以 ATR 为单位。负=在区域下方。"""
        if self.atr <= 0:
            return 0.0
        return (level.center - self.close) / self.atr

    def in_zone(self, level: PriceLevel) -> bool:
        """当前价是否正落在该区域内。"""
        return level.lower <= self.close <= level.upper


# ============================================================================
# 平滑滤波器
# ============================================================================


def _savgol_numpy(y: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    """Savitzky-Golay 平滑的 numpy 实现（scipy 缺失时的回退）。

    原理：在长度为 ``window`` 的滑动窗口内用 ``polyorder`` 次多项式最小二乘拟合，
    取拟合曲线在窗口中心处的值作为平滑输出。因拟合系数只依赖窗口长度与阶数，
    可预先算出一个固定的卷积核，对全序列做一次卷积即可。
    """
    half = window // 2
    # 位置坐标：-half .. +half，中心为 0
    x = np.arange(-half, half + 1, dtype=float)
    # Vandermonde 矩阵 (window × (polyorder+1))
    X = np.vander(x, polyorder + 1, increasing=True)
    # 帽矩阵 H = X (X^T X)^-1 X^T；取 x=0 对应的行即为平滑核
    XtX_inv = np.linalg.pinv(X.T @ X)
    H = X @ XtX_inv @ X.T
    kernel = H[half]

    # 边缘用常量填充：使两端趋于平坦，避免在序列端点制造虚假极值
    padded = np.pad(y, (half, half), mode="edge")
    return np.convolve(padded, kernel[::-1], mode="valid")


def _smooth(y: np.ndarray, window: int, polyorder: int) -> np.ndarray:
    """平滑入口：优先 scipy，缺失时回退到 numpy 实现。"""
    n = len(y)
    if window % 2 == 0:
        window += 1  # SG 滤波要求奇数窗口
    if window > n:
        # 样本不足：不做平滑（平滑本身会引入更强的边缘效应）
        logger.debug(f"样本不足({n})于平滑窗口({window})，跳过平滑")
        return y.astype(float).copy()
    polyorder = min(polyorder, window - 1)

    try:
        from scipy.signal import savgol_filter

        return savgol_filter(y, window, polyorder, mode="nearest")
    except ImportError:
        return _savgol_numpy(np.asarray(y, dtype=float), window, polyorder)


# ============================================================================
# 极值检测
# ============================================================================


def _local_extrema(values: np.ndarray, order: int) -> Tuple[np.ndarray, np.ndarray]:
    """在序列上找局部极大 / 极小点的下标。

    用居中滚动最值实现：某点等于其邻域最值即为候选。平台（连续相等值）
    会产生多个候选，取连续段的中点作为代表，避免同一平台被重复计数。

    Args:
        values: 输入序列
        order: 单侧邻域宽度，实际窗口 = 2*order+1

    Returns:
        (max_indices, min_indices)
    """
    s = pd.Series(values)
    w = 2 * order + 1

    roll_max = s.rolling(w, center=True, min_periods=w).max().to_numpy()
    roll_min = s.rolling(w, center=True, min_periods=w).min().to_numpy()

    return _dedupe_runs(s.to_numpy() == roll_max), _dedupe_runs(s.to_numpy() == roll_min)


def _dedupe_runs(mask: np.ndarray) -> np.ndarray:
    """把连续 True 段压缩为段中点（处理价格平台）。"""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return idx
    # 段边界：后一个下标不与前一个相邻
    breaks = np.flatnonzero(np.diff(idx) > 1) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [idx.size]))
    return np.array([idx[(a + b - 1) // 2] for a, b in zip(starts, ends)], dtype=int)


# ============================================================================
# 检测器
# ============================================================================


class SupportResistanceDetector:
    """支撑 / 阻力区域检测器

    Args:
        window: SG 平滑窗口（奇数；内部会修正为奇数）
        polyorder: SG 多项式阶数
        order: 极值检测的单侧邻域宽度
        merge_atr_mult: 聚类阈值 = 该值 × ATR。越大越容易合并出少数宽区域
        min_touches: 最少触及次数，低于此值视为噪声丢弃
        band_atr_mult: 区域半宽的下限 = 该值 × ATR
        max_levels: 按强度保留的最多区域数
        recency_half_life: 时间衰减半衰期（bar 数）；最近触及越久，强度衰减越多
        atr_period: ATR 周期
    """

    def __init__(
        self,
        window: int = 11,
        polyorder: int = 3,
        order: int = 5,
        merge_atr_mult: float = 0.75,
        min_touches: int = 2,
        band_atr_mult: float = 0.5,
        max_levels: int = 8,
        recency_half_life: int = 120,
        atr_period: int = 14,
    ) -> None:
        if window < 3:
            raise ValueError("window 至少为 3")
        if order < 1:
            raise ValueError("order 至少为 1")
        if polyorder >= window:
            raise ValueError("polyorder 必须小于 window")
        self.window = window
        self.polyorder = polyorder
        self.order = order
        self.merge_atr_mult = merge_atr_mult
        self.min_touches = min_touches
        self.band_atr_mult = band_atr_mult
        self.max_levels = max_levels
        self.recency_half_life = max(1, recency_half_life)
        self.atr_period = atr_period

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame, symbol: str = "") -> LevelScan:
        """检测单标的的支撑 / 阻力区域。

        Args:
            df: 含 date/open/high/low/close/volume 的 DataFrame，**必须已按日期升序**
            symbol: 标的代码（仅用于结果标记）

        Returns:
            LevelScan
        """
        required = {"high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"缺少必需列: {sorted(missing)}")
        if len(df) < self.window:
            raise ValueError(
                f"样本不足: {len(df)} 根 K 线，至少需要 {self.window} 根（window={self.window}）"
            )

        work = df.reset_index(drop=True)
        high = work["high"].to_numpy(dtype=float)
        low = work["low"].to_numpy(dtype=float)
        close = work["close"].to_numpy(dtype=float)
        volume = work["volume"].to_numpy(dtype=float)
        n = len(work)

        atr = self._compute_atr(work)

        sm_hi = _smooth(high, self.window, self.polyorder)
        sm_lo = _smooth(low, self.window, self.polyorder)

        hi_idx = _local_extrema(sm_hi, self.order)[0]   # 局部极大 -> 阻力候选
        lo_idx = _local_extrema(sm_lo, self.order)[1]   # 局部极小 -> 支撑候选

        dates = (
            pd.to_datetime(work["date"]).dt.strftime("%Y-%m-%d").to_numpy()
            if "date" in work.columns
            else np.array([""] * n)
        )

        levels: List[PriceLevel] = []
        levels += self._build_levels(
            "resistance", hi_idx, high[hi_idx], high, low, volume, dates, atr, n
        )
        levels += self._build_levels(
            "support", lo_idx, low[lo_idx], high, low, volume, dates, atr, n
        )

        levels = [l for l in levels if l.touches >= self.min_touches]
        levels.sort(key=lambda l: l.strength, reverse=True)
        levels = levels[: self.max_levels]

        as_of = self._as_of(work)
        last_close = float(close[-1])

        supports = [l for l in levels if l.kind == "support" and l.center <= last_close]
        resistances = [l for l in levels if l.kind == "resistance" and l.center >= last_close]

        return LevelScan(
            symbol=symbol,
            as_of=as_of,
            bars=n,
            close=last_close,
            atr=float(atr),
            levels=levels,
            # 取距离最近（而非强度最高）的作为"最近支撑/阻力"
            nearest_support=max(supports, key=lambda l: l.center) if supports else None,
            nearest_resistance=min(resistances, key=lambda l: l.center) if resistances else None,
        )

    def detect_batch(
        self, data: Dict[str, pd.DataFrame]
    ) -> Dict[str, LevelScan]:
        """批量检测。

        单标的失败**不中断**整批，但必须记录 —— 全市场扫描中失败标的若被
        静默丢弃，会让机会池系统性偏向"数据干净的大盘股"。
        失败项记 warning 日志，调用方可用 ``set(data) - set(results)`` 取差集。
        """
        results: Dict[str, LevelScan] = {}
        for sym, df in data.items():
            try:
                results[sym] = self.detect(df, symbol=sym)
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(f"支撑阻力检测失败 {sym}: {type(e).__name__}: {e}")
        return results

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _compute_atr(self, df: pd.DataFrame) -> float:
        """取最新 ATR。TechnicalIndicator 不可用时回退到手写实现。"""
        try:
            from finhack_pro.data.technical import TechnicalIndicator

            out = TechnicalIndicator.add_atr(df.copy(), period=self.atr_period)
            val = float(out["atr"].dropna().iloc[-1])
            if val > 0 and np.isfinite(val):
                return val
            raise ValueError("ATR 非正或非有限")
        except Exception as e:
            logger.debug(f"TechnicalIndicator 计算 ATR 失败，回退手写实现: {e}")
            return self._atr_numpy(df)

    def _atr_numpy(self, df: pd.DataFrame) -> float:
        """手写 ATR：真实波幅的滚动均值（Wilder 简化为简单均值）。"""
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        prev_close = np.concatenate(([close[0]], close[:-1]))
        tr = np.maximum(
            high - low,
            np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)),
        )
        period = min(self.atr_period, len(tr))
        val = float(np.mean(tr[-period:]))
        if val <= 0:
            # 极端情况（全一字板）：退化为价格的一个极小比例，避免除零
            val = max(float(np.mean(close)) * 1e-4, 1e-8)
        return val

    def _build_levels(
        self,
        kind: str,
        ext_idx: np.ndarray,
        ext_price: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        volume: np.ndarray,
        dates: np.ndarray,
        atr: float,
        n: int,
    ) -> List[PriceLevel]:
        """极值 -> 聚类 -> 拟合 -> 确认 -> 评分。"""
        if ext_idx.size == 0:
            return []

        order_ = np.argsort(ext_price)
        idx_sorted = ext_idx[order_]
        price_sorted = ext_price[order_]

        clusters = self._cluster(idx_sorted, price_sorted, atr)

        mean_vol = float(np.mean(volume)) if volume.size else 0.0
        levels: List[PriceLevel] = []
        for c_idx, c_price in clusters:
            level = self._fit_level(
                kind, c_idx, c_price, high, low, volume, dates, mean_vol, atr, n
            )
            if level is not None:
                levels.append(level)
        return levels

    def _cluster(
        self, idx_sorted: np.ndarray, price_sorted: np.ndarray, atr: float
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """按"共线程度"做贪心聚类（阈值随 ATR 自适应）。

        判据是**线性拟合残差**，而非价格距离。原因：价格距离只能识别水平区域，
        识别不了倾斜通道 —— 上行通道下轨的各触点价格相差很远，但彼此共线。

        做法：沿时间推进，逐点尝试并入当前簇，重新拟合整簇并检查最大残差；
        超阈值则封簇另起。每次都对整簇重新拟合，可避免"阶梯式"链式合并
        （a-b 共线、b-c 共线、但 a-b-c 不共线的情况会被第三点检查拦下）。
        """
        thr = max(self.merge_atr_mult * atr, 1e-8)
        # 沿时间排序：聚类应按行情推进的顺序累积，而非按价格高低
        time_order = np.argsort(idx_sorted)
        idx = idx_sorted[time_order]
        px = price_sorted[time_order]

        groups: List[List[int]] = []
        current = [0]
        for i in range(1, len(idx)):
            trial = current + [i]
            if self._max_residual(idx[trial], px[trial]) <= thr:
                current = trial
            else:
                groups.append(current)
                current = [i]
        groups.append(current)

        return [(idx[g], px[g]) for g in groups]

    @staticmethod
    def _max_residual(x: np.ndarray, y: np.ndarray) -> float:
        """最小二乘线性拟合的最大绝对残差。

        点数 < 3 时恒为 0（两点必共线），因此第三点加入时会重 fit 整簇，
        由它来否决"看起来能连上、实际拐了弯"的情况。
        """
        if len(x) < 3:
            return 0.0
        xf = x.astype(float)
        slope, intercept = np.polyfit(xf, y, 1)
        return float(np.max(np.abs(y - (slope * xf + intercept))))

    def _fit_level(
        self,
        kind: str,
        c_idx: np.ndarray,
        c_price: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        volume: np.ndarray,
        dates: np.ndarray,
        mean_vol: float,
        atr: float,
        n: int,
    ) -> Optional[PriceLevel]:
        """拟合成带斜率的区域，并做触碰次数 + 成交量双重确认。"""
        if c_idx.size == 0:
            return None

        if c_idx.size >= 2 and np.ptp(c_idx) > 0:
            slope, intercept = np.polyfit(c_idx.astype(float), c_price, 1)
        else:
            # 单点或同位点：退化为水平区域
            slope, intercept = 0.0, float(np.mean(c_price))
        slope = float(slope)
        intercept = float(intercept)

        # 区域半宽：ATR 下限 与 簇内拟合残差离散度 取大者。
        # 注意必须用**残差**而非原始价格的 std —— 倾斜通道内各触点价格本身
        # 跨度很大，用原始 std 会把带宽撑成整个通道高度，区域失去意义。
        spread = float(np.std(c_price - (slope * c_idx.astype(float) + intercept)))
        band = max(self.band_atr_mult * atr, spread)
        band = max(band, 1e-8)

        # 区域随时间移动：第 i 根 bar 的中心
        centers = slope * np.arange(n, dtype=float) + intercept
        upper_t = centers + band
        lower_t = centers - band

        # 触碰判定：该 bar 的价格区间与当时区域有交集
        touch_mask = (low <= upper_t) & (high >= lower_t)
        touches = int(touch_mask.sum())
        if touches == 0:
            return None

        touch_idx = np.flatnonzero(touch_mask)
        last_i = int(touch_idx[-1])
        first_i = int(touch_idx[0])

        vol_score = (
            float(np.mean(volume[touch_mask]) / mean_vol) if mean_vol > 0 else 1.0
        )

        strength = self._score(touches, vol_score, n - 1 - last_i)

        return PriceLevel(
            kind=kind,
            center=float(centers[-1]),
            lower=float(lower_t[-1]),
            upper=float(upper_t[-1]),
            slope=slope,
            touches=touches,
            volume_score=round(vol_score, 4),
            strength=round(strength, 4),
            first_touch=str(dates[first_i]) if dates.size else "",
            last_touch=str(dates[last_i]) if dates.size else "",
        )

    def _score(self, touches: int, vol_score: float, bars_since_last: int) -> float:
        """强度评分：触碰 45% + 成交量 30% + 时间衰减 25%。

        权重是经验值，不是从数据拟合出来的 —— 若后续要做因子化研究，
        应把它变成可配置参数并做敏感性测试，而非当成常数。
        """
        touch_score = min(touches / max(2 * self.min_touches, 1), 1.0)
        vol_component = min(vol_score / 1.5, 1.0)  # 1.5 倍均量即满分
        recency = 0.5 ** (bars_since_last / self.recency_half_life)
        raw = 0.45 * touch_score + 0.30 * vol_component + 0.25 * recency
        return float(np.clip(raw, 0.0, 1.0))

    @staticmethod
    def _as_of(work: pd.DataFrame) -> str:
        if "date" in work.columns:
            return str(pd.to_datetime(work["date"].iloc[-1]).date())
        return ""


# ============================================================================
# 全市场筛选辅助
# ============================================================================


def screen_near_level(
    scans: Dict[str, LevelScan],
    kind: str = "support",
    max_distance_atr: float = 1.0,
    min_strength: float = 0.0,
) -> List[Tuple[str, PriceLevel, float]]:
    """从批量扫描结果中筛出"正逼近某区域"的标的。

    Args:
        scans: detect_batch 的结果
        kind: "support" | "resistance"
        max_distance_atr: 当前价到区域中心的最大距离（ATR 单位）
        min_strength: 区域强度下限

    Returns:
        [(symbol, level, distance_atr)] 按距离由近及远排序。
        距离为正表示标的价格在区域**下方**（对支撑而言即尚未到达）。
    """
    if kind not in ("support", "resistance"):
        raise ValueError(f"kind 必须是 support/resistance，收到 {kind}")

    out: List[Tuple[str, PriceLevel, float]] = []
    for sym, scan in scans.items():
        level = scan.nearest_support if kind == "support" else scan.nearest_resistance
        if level is None or level.strength < min_strength:
            continue
        dist = scan.distance_atr(level)
        if abs(dist) <= max_distance_atr:
            out.append((sym, level, round(dist, 4)))
    out.sort(key=lambda t: abs(t[2]))
    return out


__all__ = [
    "SupportResistanceDetector",
    "PriceLevel",
    "LevelScan",
    "screen_near_level",
]
