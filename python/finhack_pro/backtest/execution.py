"""
撮合精度约束模块 - Execution Constraints

提供 A 股市场真实交易规则约束，提升回测保真度（预设精度）：

- 涨跌停约束：涨停价拒绝买单、跌停价拒绝卖单
- T+1 规则：当日买入的股票次日才可卖出
- 停牌处理：停牌 bar 跳过所有成交
- 滑点模型：固定滑点 / 成交量比例滑点 / 冲击成本
- 最小变动价位（A股 0.01 元）与整手约束（100 股）

设计：
- 所有约束为"开关"，默认关闭保持向后兼容，开启后逐项收紧
- 约束逻辑与引擎解耦：VectorizedEngine / AsyncEventEngine / PortfolioEngine 共用
- 纯函数 + 轻量状态，便于单元测试

Usage:
    gate = ExecutionGate(ExecutionConfig(
        enable_limit_up_down=True,
        enable_t1=True,
        enable_suspension=True,
        slippage_model="volume_proportional",
    ))
    fill = gate.check_and_fill(bar, signal, position, date, buyable_volume=..., sellable_volume=...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Optional

from finhack_pro.strategies.base import BarData, Signal, SignalDirection
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

# A 股默认涨跌停幅度（主板 10%，创业板/科创板 20%，ST 5%）
DEFAULT_LIMIT_PCT = 0.10
LIMIT_PCT_ST = 0.05
LIMIT_PCT_CHINEXT = 0.20  # 创业板 300xxx / 科创板 688xxx


class SlippageModel(str, Enum):
    """滑点模型"""
    FIXED = "fixed"                  # 固定比例滑点（默认，向后兼容）
    VOLUME_PROPORTIONAL = "volume_proportional"  # 成交量比例滑点（小单滑点小、大单滑点大）
    IMPACT_COST = "impact_cost"      # 冲击成本模型（平方根冲击）


@dataclass
class ExecutionConfig:
    """撮合精度约束配置"""
    enable_limit_up_down: bool = False   # 涨跌停约束
    enable_t1: bool = False              # T+1 规则
    enable_suspension: bool = False      # 停牌处理（需数据含 volume=0 标记或 trading_status 列）
    enable_price_tick: bool = False      # 最小变动价位（0.01）
    slippage_model: str = SlippageModel.FIXED.value  # 滑点模型
    slippage: float = 0.001              # 固定滑点比例
    limit_pct: float = DEFAULT_LIMIT_PCT # 默认涨跌停幅度（可被 bar.extra 覆盖）
    lot_size: int = 100                  # 整手股数
    min_price_tick: float = 0.01         # 最小变动价位
    max_position_pct: float = 0.9        # 单次买入最大使用现金比例

    def __post_init__(self) -> None:
        if self.slippage_model not in (m.value for m in SlippageModel):
            raise ValueError(f"无效滑点模型: {self.slippage_model}")


@dataclass
class FillResult:
    """撮合结果"""
    executed: bool = False           # 是否成交
    reject_reason: str = ""          # 拒绝原因（未成交时）
    fill_price: float = 0.0          # 成交价
    fill_volume: int = 0             # 成交量
    slippage_used: float = 0.0       # 实际滑点
    note: str = ""                   # 附加说明（如 T+1 冻结）


class ExecutionGate:
    """撮合约束闸门

    根据 bar + 持仓状态判断信号能否成交，并计算成交价与成交量。
    多引擎共用，保持撮合规则一致。
    """

    def __init__(self, config: Optional[ExecutionConfig] = None):
        self.config = config or ExecutionConfig()

    # ------------------------------------------------------------------
    # 涨跌停
    # ------------------------------------------------------------------
    def _limit_pct_for(self, bar: BarData) -> float:
        """获取标的涨跌停幅度（支持 bar.extra 覆盖）"""
        extra = getattr(bar, "extra", None) or {}
        if extra.get("limit_pct"):
            return float(extra["limit_pct"])
        if extra.get("is_st"):
            return LIMIT_PCT_ST
        symbol = getattr(bar, "symbol", "") or ""
        # 创业板 300/301、科创板 688/689
        if symbol.startswith(("300", "301", "688", "689")):
            return LIMIT_PCT_CHINEXT
        return self.config.limit_pct

    def _limit_prices(self, bar: BarData) -> Optional[tuple[float, float]]:
        """计算涨跌停价 (limit_up, limit_down)

        基于昨收（pre_close）。优先用 bar.extra['pre_close']，
        否则用 open 推断（无昨收数据时无法精确判断，返回 None 表示不约束）。
        """
        if not self.config.enable_limit_up_down:
            return None
        extra = getattr(bar, "extra", None) or {}
        pre_close = extra.get("pre_close")
        if pre_close is None or float(pre_close) <= 0:
            return None  # 无昨收数据，跳过约束（保守：不误拒）
        pct = self._limit_pct_for(bar)
        limit_up = round(float(pre_close) * (1 + pct), 2)
        limit_down = round(float(pre_close) * (1 - pct), 2)
        return limit_up, limit_down

    def _is_limit_up(self, bar: BarData) -> bool:
        """是否涨停（封板：close 触及涨停价且不可成交）"""
        prices = self._limit_prices(bar)
        if not prices:
            return False
        limit_up, _ = prices
        return float(bar.close) >= limit_up - 1e-9

    def _is_limit_down(self, bar: BarData) -> bool:
        """是否跌停"""
        prices = self._limit_prices(bar)
        if not prices:
            return False
        _, limit_down = prices
        return float(bar.close) <= limit_down + 1e-9

    # ------------------------------------------------------------------
    # 停牌
    # ------------------------------------------------------------------
    def _is_suspended(self, bar: BarData) -> bool:
        """是否停牌

        停牌判定：
        1. bar.extra['trading_status'] == 'suspended' 显式标记
        2. volume == 0 且 amount == 0（无成交视为停牌）
        """
        if not self.config.enable_suspension:
            return False
        extra = getattr(bar, "extra", None) or {}
        if extra.get("trading_status") == "suspended":
            return True
        return float(bar.volume) <= 0 and float(getattr(bar, "amount", 0)) <= 0

    # ------------------------------------------------------------------
    # T+1
    # ------------------------------------------------------------------
    def _sellable_volume(
        self,
        bar: BarData,
        position: Dict[str, Any],
    ) -> int:
        """计算可卖出数量（T+1 约束）

        position 需含 'volume' 与可选 'available_date'。
        当 enable_t1=True 时，available_date <= bar.date 的部分才可卖。
        """
        volume = int(position.get("volume", 0))
        if not self.config.enable_t1:
            return volume
        available_date = position.get("available_date")
        if not available_date:
            return volume  # 无可用日期记录，不约束
        bar_date = self._bar_date(bar)
        if bar_date >= available_date:
            return volume
        return 0

    @staticmethod
    def _bar_date(bar: BarData) -> date:
        dt = bar.datetime
        if isinstance(dt, datetime):
            return dt.date()
        if isinstance(dt, date):
            return dt
        try:
            return datetime.fromisoformat(str(dt)).date()
        except Exception:
            return date.today()

    # ------------------------------------------------------------------
    # 滑点
    # ------------------------------------------------------------------
    def _compute_slippage(self, bar: BarData, volume: int, direction: SignalDirection) -> float:
        """计算滑点比例"""
        model = self.config.slippage_model
        if model == SlippageModel.VOLUME_PROPORTIONAL.value:
            # 成交量比例滑点：滑点随成交占 bar 成交量比例增大
            bar_volume = float(bar.volume)
            if bar_volume <= 0:
                return self.config.slippage * 2  # 无量时保守
            participation = volume / bar_volume
            # 基础滑点 + 参与率放大（上限 5 倍基础滑点）
            return min(self.config.slippage * (1 + participation * 4), self.config.slippage * 5)
        if model == SlippageModel.IMPACT_COST.value:
            # 平方根冲击模型：滑点 ∝ sqrt(参与率)
            bar_volume = float(bar.volume)
            if bar_volume <= 0:
                return self.config.slippage * 2
            participation = volume / bar_volume
            return min(self.config.slippage * (1 + participation ** 0.5 * 2), self.config.slippage * 5)
        return self.config.slippage

    def _apply_price_tick(self, price: float) -> float:
        """对齐最小变动价位"""
        if not self.config.enable_price_tick:
            return price
        tick = self.config.min_price_tick
        return round(round(price / tick) * tick, 2)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def check_and_fill(
        self,
        bar: BarData,
        signal: Signal,
        position: Dict[str, Any],
        cash: float,
        direction: SignalDirection,
    ) -> FillResult:
        """检查约束并计算成交

        Args:
            bar: 当前 K 线
            signal: 交易信号
            position: 当前持仓 {"volume", "cost", "available_date"}
            cash: 当前可用现金
            direction: 信号方向

        Returns:
            FillResult
        """
        # 停牌：直接拒绝
        if self._is_suspended(bar):
            return FillResult(executed=False, reject_reason="suspended")

        if direction == SignalDirection.BUY:
            # 涨停拒买
            if self._is_limit_up(bar):
                return FillResult(executed=False, reject_reason="limit_up")

            # 计算买入量（整手）
            price = self._apply_price_tick(bar.close * (1 + self._compute_slippage(bar, signal.volume or 100, direction)))
            if price <= 0:
                return FillResult(executed=False, reject_reason="invalid_price")

            max_cash = cash * self.config.max_position_pct
            raw_volume = int(max_cash / price / self.config.lot_size) * self.config.lot_size
            if raw_volume <= 0:
                return FillResult(executed=False, reject_reason="insufficient_cash")

            return FillResult(
                executed=True,
                fill_price=price,
                fill_volume=raw_volume,
                slippage_used=price / bar.close - 1 if bar.close > 0 else 0,
            )

        elif direction == SignalDirection.SELL:
            # 跌停拒卖
            if self._is_limit_down(bar):
                return FillResult(executed=False, reject_reason="limit_down")

            sellable = self._sellable_volume(bar, position)
            if sellable <= 0:
                reason = "t1_frozen" if self.config.enable_t1 and int(position.get("volume", 0)) > 0 else "no_position"
                return FillResult(executed=False, reject_reason=reason)

            price = self._apply_price_tick(bar.close * (1 - self._compute_slippage(bar, sellable, direction)))
            if price <= 0:
                return FillResult(executed=False, reject_reason="invalid_price")

            return FillResult(
                executed=True,
                fill_price=price,
                fill_volume=sellable,
                slippage_used=bar.close / price - 1 if price > 0 else 0,
            )

        return FillResult(executed=False, reject_reason="unknown_direction")
