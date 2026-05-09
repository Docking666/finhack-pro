"""
FinHack Pro 券商适配器抽象基类

定义所有券商适配器必须实现的统一接口，包括：
- 数据结构：BrokerOrder, BrokerPosition, BrokerAccount
- 枚举类型：OrderDirection, OrderType, OrderStatus
- 回调接口：BrokerCallback
- 适配器基类：BrokerAdapter

设计原则：
1. 统一接口：所有券商使用相同的数据结构
2. 错误隔离：券商SDK异常不应传播到上层
3. 安全保护：所有实盘操作必须经过风控检查
4. 可观测性：所有操作记录日志和指标
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import threading


# ============================================================================
# Enums
# ============================================================================


class OrderDirection(Enum):
    """买卖方向"""
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """订单类型"""
    LIMIT = "limit"                # 限价委托
    MARKET = "market"              # 市价委托
    LIMIT_MAKER = "limit_maker"    # 限价只做Maker


class OrderStatus(Enum):
    """订单状态"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class BrokerOrder:
    """统一的订单数据结构"""
    broker_order_id: str       # 券商返回的订单ID
    symbol: str                # 证券代码 (e.g., "600519.SH")
    direction: OrderDirection  # 买卖方向
    order_type: OrderType      # 订单类型
    price: float               # 委托价格
    volume: int                # 委托数量（股）
    filled_volume: int = 0     # 已成交数量
    filled_price: float = 0.0  # 成交均价
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    commission: float = 0.0    # 手续费
    error_msg: str = ""        # 错误信息
    raw_data: Dict[str, Any] = field(default_factory=dict)  # 券商原始数据


@dataclass
class BrokerPosition:
    """统一的持仓数据结构"""
    symbol: str
    volume: int                # 持仓数量
    available_volume: int      # 可用数量
    avg_price: float           # 平均成本
    current_price: float       # 当前价格
    market_value: float        # 市值
    pnl: float = 0.0           # 盈亏金额
    pnl_pct: float = 0.0       # 盈亏比例
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BrokerAccount:
    """统一的账户数据结构"""
    total_equity: float        # 总资产
    available_cash: float      # 可用资金
    frozen_cash: float         # 冻结资金
    market_value: float        # 持仓市值
    unrealized_pnl: float      # 未实现盈亏
    realized_pnl: float = 0.0  # 已实现盈亏
    margin_used: float = 0.0   # 已用保证金
    margin_ratio: float = 0.0  # 保证金比例
    raw_data: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Callback Interface
# ============================================================================


class BrokerCallback(ABC):
    """券商回调基类

    所有券商适配器通过此接口向上层推送事件。
    上层实现此接口以接收订单、成交、持仓、账户的变更通知。
    """

    @abstractmethod
    def on_order_update(self, order: BrokerOrder) -> None:
        """订单状态变更回调"""
        ...

    @abstractmethod
    def on_trade_update(self, order: BrokerOrder) -> None:
        """成交回报回调"""
        ...

    @abstractmethod
    def on_position_update(self, position: BrokerPosition) -> None:
        """持仓变更回调"""
        ...

    @abstractmethod
    def on_account_update(self, account: BrokerAccount) -> None:
        """账户变更回调"""
        ...

    @abstractmethod
    def on_error(self, error_msg: str, order_id: str = "") -> None:
        """错误回调"""
        ...

    @abstractmethod
    def on_disconnected(self) -> None:
        """连接断开回调"""
        ...


# ============================================================================
# Broker Adapter Abstract Base
# ============================================================================


class BrokerAdapter(ABC):
    """券商适配器抽象基类

    所有券商实现必须继承此类并实现所有抽象方法。

    设计原则：
    1. 统一接口：所有券商使用相同的数据结构（BrokerOrder/Position/Account）
    2. 错误隔离：券商SDK异常不应传播到上层
    3. 安全保护：所有实盘操作必须经过风控检查
    4. 可观测性：所有操作记录日志和指标
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config
        self._connected = False
        self._callback: Optional[BrokerCallback] = None
        self._lock = threading.RLock()

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """券商名称"""
        ...

    @property
    @abstractmethod
    def supported_features(self) -> List[str]:
        """支持的特性列表，如 ['order', 'cancel', 'query', 'market_data']"""
        ...

    @abstractmethod
    def connect(self) -> bool:
        """连接券商

        Returns:
            是否连接成功
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """断开券商连接"""
        ...

    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        order_type: OrderType,
        price: float,
        volume: int,
        **kwargs: Any,
    ) -> BrokerOrder:
        """提交订单

        Args:
            symbol: 证券代码
            direction: 买卖方向
            order_type: 订单类型
            price: 委托价格
            volume: 委托数量（股）
            **kwargs: 券商特定参数

        Returns:
            统一的订单对象
        """
        ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """取消订单

        Args:
            broker_order_id: 券商订单ID

        Returns:
            是否取消成功
        """
        ...

    @abstractmethod
    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """查询订单

        Args:
            symbol: 证券代码，为空返回全部

        Returns:
            订单列表
        """
        ...

    @abstractmethod
    def get_positions(
        self, symbol: Optional[str] = None
    ) -> Dict[str, BrokerPosition]:
        """查询持仓

        Args:
            symbol: 证券代码，为空返回全部

        Returns:
            持仓字典 {symbol: BrokerPosition}
        """
        ...

    @abstractmethod
    def get_account(self) -> BrokerAccount:
        """查询账户信息

        Returns:
            统一的账户对象
        """
        ...

    @abstractmethod
    def subscribe_market_data(
        self, symbols: List[str], callback: Callable
    ) -> bool:
        """订阅实时行情

        Args:
            symbols: 证券代码列表
            callback: 行情回调函数

        Returns:
            是否订阅成功
        """
        ...

    def register_callback(self, callback: BrokerCallback) -> None:
        """注册回调处理器

        Args:
            callback: 回调处理器实例
        """
        self._callback = callback

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected
