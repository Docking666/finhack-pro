"""
策略基类模块

定义所有策略的抽象接口，包括初始化、K线处理、Tick处理、订单回调等。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class OrderType(str, Enum):
    """订单类型"""
    MARKET = "market"  # 市价单
    LIMIT = "limit"  # 限价单
    STOP = "stop"  # 止损单


class SignalDirection(str, Enum):
    """信号方向"""
    BUY = "buy"
    SELL = "sell"


@dataclass
class Signal:
    """交易信号

    Attributes:
        symbol: 标的代码
        direction: 信号方向
        price: 信号价格
        volume: 信号数量(股)
        order_type: 订单类型
        stop_loss: 止损价
        take_profit: 止盈价
        timestamp: 信号时间
        strategy_name: 策略名称
        extra: 额外信息
    """
    symbol: str
    direction: SignalDirection
    price: float
    volume: int = 100
    order_type: OrderType = OrderType.MARKET
    stop_loss: float = 0.0
    take_profit: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    strategy_name: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "price": self.price,
            "volume": self.volume,
            "order_type": self.order_type.value,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "timestamp": self.timestamp.isoformat(),
            "strategy_name": self.strategy_name,
            "extra": self.extra,
        }


@dataclass
class BarData:
    """K线数据"""
    symbol: str
    datetime: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TickData:
    """Tick数据"""
    symbol: str
    datetime: datetime
    last_price: float
    volume: float
    bid_price_1: float = 0.0
    ask_price_1: float = 0.0
    bid_volume_1: float = 0.0
    ask_volume_1: float = 0.0


@dataclass
class OrderData:
    """订单数据"""
    order_id: str
    symbol: str
    direction: SignalDirection
    price: float
    volume: int
    traded_volume: int = 0
    status: str = "pending"
    order_time: datetime = field(default_factory=datetime.now)


@dataclass
class TradeData:
    """成交数据"""
    trade_id: str
    order_id: str
    symbol: str
    direction: SignalDirection
    price: float
    volume: int
    trade_time: datetime = field(default_factory=datetime.now)
    commission: float = 0.0


@dataclass
class Portfolio:
    """组合信息"""
    cash: float = 1_000_000.0
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    total_value: float = 1_000_000.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0

    def get_position(self, symbol: str) -> Dict[str, Any]:
        """获取持仓"""
        return self.positions.get(symbol, {"volume": 0, "cost": 0.0, "pnl": 0.0})


@dataclass
class Context:
    """策略运行上下文

    包含策略运行所需的所有环境信息。

    Attributes:
        portfolio: 组合信息
        current_time: 当前时间
        config: 策略配置
        data_feed: 数据引用
        params: 策略参数
        broker: 经纪商接口(下单/撤单)
    """
    portfolio: Portfolio = field(default_factory=Portfolio)
    current_time: datetime = field(default_factory=datetime.now)
    config: Dict[str, Any] = field(default_factory=dict)
    data_feed: Optional[Any] = None
    params: Dict[str, Any] = field(default_factory=dict)
    broker: Optional[Any] = None

    def get_param(self, key: str, default: Any = None) -> Any:
        """获取策略参数"""
        return self.params.get(key, default)

    def buy(
        self,
        symbol: str,
        price: float,
        volume: int,
        order_type: OrderType = OrderType.MARKET,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> None:
        """发送买入订单"""
        if self.broker:
            self.broker.send_order(
                symbol=symbol,
                direction=SignalDirection.BUY,
                price=price,
                volume=volume,
                order_type=order_type,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

    def sell(
        self,
        symbol: str,
        price: float,
        volume: int,
        order_type: OrderType = OrderType.MARKET,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> None:
        """发送卖出订单"""
        if self.broker:
            self.broker.send_order(
                symbol=symbol,
                direction=SignalDirection.SELL,
                price=price,
                volume=volume,
                order_type=order_type,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )


class BaseStrategy(ABC):
    """策略基类

    所有策略需继承此类并实现相应方法。

    生命周期:
    1. on_init() - 策略初始化
    2. on_bar() / on_tick() - 行情驱动
    3. on_order_status() / on_trade() - 交易回调

    Usage:
        class MyStrategy(BaseStrategy):
            def on_init(self, context: Context) -> None:
                self.fast_period = context.get_param("fast_period", 5)

            def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
                # 策略逻辑
                return [Signal(...)]
    """

    def __init__(self) -> None:
        self.strategy_name: str = self.__class__.__name__
        self._params: Dict[str, Any] = {}
        self._initialized = False

    @abstractmethod
    def on_init(self, context: Context) -> None:
        """策略初始化

        在回测/实盘开始前调用，用于初始化策略参数和状态。

        Args:
            context: 策略上下文
        """
        ...

    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        """K线回调(每根K线调用一次)

        Args:
            context: 策略上下文
            bar: K线数据

        Returns:
            交易信号列表
        """
        return []

    def on_tick(self, context: Context, tick: TickData) -> Optional[Signal]:
        """Tick回调(每个Tick调用一次)

        Args:
            context: 策略上下文
            tick: Tick数据

        Returns:
            交易信号(可选)
        """
        return None

    def on_order_status(self, context: Context, order: OrderData) -> None:
        """订单状态回调

        Args:
            context: 策略上下文
            order: 订单数据
        """
        pass

    def on_trade(self, context: Context, trade: TradeData) -> None:
        """成交回调

        Args:
            context: 策略上下文
            trade: 成交数据
        """
        pass

    def on_finish(self, context: Context) -> None:
        """策略结束回调

        Args:
            context: 策略上下文
        """
        pass

    def get_parameters(self) -> Dict[str, Any]:
        """获取策略参数"""
        return self._params.copy()

    def set_parameters(self, params: Dict[str, Any]) -> None:
        """设置策略参数

        Args:
            params: 参数字典
        """
        self._params.update(params)

    def __repr__(self) -> str:
        return f"<{self.strategy_name} params={self._params}>"
