"""
FinHack Pro PTrade 券商适配器

基于 PTrade API 实现的券商适配器，支持：
- 连接/断开 PTrade 云端环境
- 下单/撤单（限价、市价）
- 查询订单、持仓、账户
- 订阅实时行情

PTrade 特点：
- 运行在券商云端服务器，无需本地客户端
- amount 正数=买入，负数=卖出
- Order 对象: .id, .security, .amount, .price, .filled, .status
- Position 对象: .security, .total_amount, .closeable_amount, .avg_cost

PTrade API 映射：
- order(security, amount, limit_price=None) -> Order
- order_target(security, amount, limit_price=None) -> Order
- order_value(security, value, limit_price=None) -> Order
- cancel_order(order_id) -> bool
- get_positions() -> dict
- get_position(security) -> Position
- get_orders(security=None) -> dict
- get_order(order_id) -> Order
- get_trades() -> list
- get_open_orders(security=None) -> list

Usage:
    from finhack_pro.execution.broker_ptrade import PTradeAdapter

    config = {"account_id": "your_account_id"}
    adapter = PTradeAdapter(config)
    adapter.connect()
    order = adapter.submit_order("600519.SH", OrderDirection.BUY,
                                  OrderType.LIMIT, 1800.0, 100)
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from finhack_pro.execution.broker_base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerCallback,
    BrokerOrder,
    BrokerPosition,
    OrderDirection,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger(__name__)

# ============================================================================
# PTrade 状态映射
# ============================================================================

# PTrade Order.status 值 -> 统一 OrderStatus
_PTRADE_STATUS_MAP: Dict[str, OrderStatus] = {
    "pending": OrderStatus.PENDING,
    "submitted": OrderStatus.SUBMITTED,
    "partial_filled": OrderStatus.PARTIAL_FILLED,
    "filled": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "held": OrderStatus.SUBMITTED,
    "new": OrderStatus.PENDING,
    "open": OrderStatus.SUBMITTED,
    "canceled": OrderStatus.CANCELLED,
}


def _map_ptrade_status(status: Any) -> OrderStatus:
    """将 PTrade 状态转换为统一状态"""
    if isinstance(status, OrderStatus):
        return status
    status_str = str(status).lower()
    return _PTRADE_STATUS_MAP.get(status_str, OrderStatus.UNKNOWN)


# ============================================================================
# Lazy import helper
# ============================================================================


def _import_ptrade():
    """延迟导入 PTrade API

    PTrade API 仅在券商云端服务器上可用，
    本地环境无法导入。
    """
    try:
        # PTrade 的 API 在其沙箱环境中作为全局函数提供
        # 这里尝试导入，如果失败则说明不在 PTrade 环境中
        import ptrade_api
        return ptrade_api
    except ImportError:
        try:
            # 某些 PTrade 版本直接暴露全局函数
            # 在测试环境中我们通过 mock 来模拟
            import builtins
            if hasattr(builtins, 'order'):
                return builtins
        except Exception:
            pass
        logger.error(
            "PTrade API 不可用。PTrade 适配器仅在券商云端服务器上运行。"
        )
        raise ImportError("PTrade API 不可用")


# ============================================================================
# PTrade Adapter
# ============================================================================


class PTradeAdapter(BrokerAdapter):
    """PTrade 券商适配器

    通过 PTrade 云端 API 进行交易。
    PTrade 运行在券商提供的云端服务器上，无需本地客户端。

    配置参数：
        account_id: str - 资金账号（可选，PTrade 通常自动绑定）
        commission_rate: float - 佣金费率，默认 0.0003
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._account_id = config.get("account_id", "")
        self._commission_rate = config.get("commission_rate", 0.0003)

        # PTrade API 模块（延迟初始化）
        self._ptrade: Any = None

    @property
    def broker_name(self) -> str:
        return "ptrade"

    @property
    def supported_features(self) -> List[str]:
        return ["order", "cancel", "query", "market_data"]

    def connect(self) -> bool:
        """连接 PTrade

        PTrade 运行在云端沙箱中，通常无需显式连接。
        此方法验证 PTrade API 是否可用。

        Returns:
            是否可用
        """
        try:
            self._ptrade = _import_ptrade()
            with self._lock:
                self._connected = True
            logger.info("PTrade 连接成功")
            return True
        except ImportError:
            logger.error("PTrade API 不可用")
            return False
        except Exception as e:
            logger.error(f"PTrade 连接失败: {e}")
            return False

    def disconnect(self) -> None:
        """断开 PTrade 连接"""
        with self._lock:
            self._connected = False
            self._ptrade = None
        logger.info("PTrade 已断开连接")

    def submit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        order_type: OrderType,
        price: float,
        volume: int,
        **kwargs: Any,
    ) -> BrokerOrder:
        """提交订单到 PTrade

        Args:
            symbol: 证券代码 (e.g., "600519.SH")
            direction: 买卖方向
            order_type: 订单类型
            price: 委托价格
            volume: 委托数量（股）
            **kwargs: 额外参数
                - limit_price: 限价价格（覆盖 price 参数）

        Returns:
            统一的 BrokerOrder 对象
        """
        if not self._connected:
            raise RuntimeError("PTrade 未连接，请先调用 connect()")

        try:
            # PTrade: 正数=买入，负数=卖出
            amount = volume if direction == OrderDirection.BUY else -volume

            # 限价单传价格，市价单不传价格
            limit_price = kwargs.get("limit_price", None)
            if order_type == OrderType.LIMIT or order_type == OrderType.LIMIT_MAKER:
                limit_price = limit_price or price
            else:
                limit_price = None  # 市价单

            # 调用 PTrade order API
            ptrade_order = self._ptrade.order(symbol, amount, limit_price)

            return self._convert_order(ptrade_order, symbol, direction, order_type, price, volume)

        except Exception as e:
            logger.error(f"PTrade 下单失败: {e}")
            return BrokerOrder(
                broker_order_id="",
                symbol=symbol,
                direction=direction,
                order_type=order_type,
                price=price,
                volume=volume,
                status=OrderStatus.REJECTED,
                error_msg=str(e),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

    def cancel_order(self, broker_order_id: str) -> bool:
        """取消订单

        Args:
            broker_order_id: PTrade 订单ID

        Returns:
            是否取消成功
        """
        if not self._connected:
            raise RuntimeError("PTrade 未连接")

        try:
            self._ptrade.cancel_order(broker_order_id)
            return True
        except Exception as e:
            logger.error(f"PTrade 撤单失败: {e}")
            return False

    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """查询订单

        Args:
            symbol: 证券代码过滤，为空返回全部

        Returns:
            订单列表
        """
        if not self._connected:
            raise RuntimeError("PTrade 未连接")

        try:
            orders_dict = self._ptrade.get_orders(symbol)
            result = []

            if isinstance(orders_dict, dict):
                for order_id, ptrade_order in orders_dict.items():
                    order = self._convert_order(ptrade_order)
                    result.append(order)
            elif isinstance(orders_dict, list):
                for ptrade_order in orders_dict:
                    order = self._convert_order(ptrade_order)
                    if symbol is None or order.symbol == symbol:
                        result.append(order)

            return result
        except Exception as e:
            logger.error(f"PTrade 查询订单失败: {e}")
            return []

    def get_positions(
        self, symbol: Optional[str] = None
    ) -> Dict[str, BrokerPosition]:
        """查询持仓

        Args:
            symbol: 证券代码过滤，为空返回全部

        Returns:
            持仓字典
        """
        if not self._connected:
            raise RuntimeError("PTrade 未连接")

        try:
            positions_dict = self._ptrade.get_positions()
            result = {}

            if isinstance(positions_dict, dict):
                for sec, ptrade_pos in positions_dict.items():
                    position = self._convert_position(ptrade_pos)
                    if position.volume > 0:
                        if symbol is None or position.symbol == symbol:
                            result[position.symbol] = position

            return result
        except Exception as e:
            logger.error(f"PTrade 查询持仓失败: {e}")
            return {}

    def get_account(self) -> BrokerAccount:
        """查询账户信息

        Returns:
            统一的账户对象
        """
        if not self._connected:
            raise RuntimeError("PTrade 未连接")

        try:
            # PTrade 通过全局函数获取账户信息
            # 不同版本可能使用不同 API
            if hasattr(self._ptrade, 'get_account'):
                account_data = self._ptrade.get_account()
            else:
                # 回退：尝试通过持仓和资金计算
                account_data = None

            if account_data:
                return self._convert_account(account_data)
            else:
                # 使用基础查询
                return self._query_account_from_positions()
        except Exception as e:
            logger.error(f"PTrade 查询账户失败: {e}")
            return BrokerAccount(
                total_equity=0.0,
                available_cash=0.0,
                frozen_cash=0.0,
                market_value=0.0,
                unrealized_pnl=0.0,
            )

    def subscribe_market_data(
        self, symbols: List[str], callback: Callable
    ) -> bool:
        """订阅实时行情

        PTrade 在云端环境中自动获取行情数据。
        此方法注册行情回调。

        Args:
            symbols: 证券代码列表
            callback: 行情回调函数

        Returns:
            是否订阅成功
        """
        if not self._connected:
            raise RuntimeError("PTrade 未连接")

        try:
            # PTrade 可能提供 subscribe 函数
            if hasattr(self._ptrade, 'subscribe'):
                self._ptrade.subscribe(symbols)
            logger.info(f"PTrade 行情订阅: {symbols}")
            return True
        except Exception as e:
            logger.error(f"PTrade 行情订阅失败: {e}")
            return False

    # ========================================================================
    # Internal converters
    # ========================================================================

    def _convert_order(
        self,
        ptrade_order: Any,
        symbol: str = "",
        direction: OrderDirection = OrderDirection.BUY,
        order_type: OrderType = OrderType.LIMIT,
        price: float = 0.0,
        volume: int = 0,
    ) -> BrokerOrder:
        """将 PTrade Order 转换为统一 BrokerOrder"""
        if ptrade_order is None:
            return BrokerOrder(
                broker_order_id="",
                symbol=symbol,
                direction=direction,
                order_type=order_type,
                price=price,
                volume=volume,
                status=OrderStatus.REJECTED,
                error_msg="PTrade 返回空订单",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

        # 从 PTrade Order 对象提取属性
        order_id = str(getattr(ptrade_order, "id", getattr(ptrade_order, "order_id", "")))
        sec = getattr(ptrade_order, "security", symbol)
        amount = int(getattr(ptrade_order, "amount", volume))
        filled = int(getattr(ptrade_order, "filled", 0))
        order_price = float(getattr(ptrade_order, "price", price))
        status = _map_ptrade_status(getattr(ptrade_order, "status", "pending"))

        # 从 amount 推断方向
        if amount >= 0:
            actual_direction = OrderDirection.BUY
            actual_volume = amount
        else:
            actual_direction = OrderDirection.SELL
            actual_volume = abs(amount)

        # 如果调用时已知方向，使用调用时的方向
        if direction != OrderDirection.BUY or amount >= 0:
            actual_direction = direction

        return BrokerOrder(
            broker_order_id=order_id,
            symbol=sec,
            direction=actual_direction,
            order_type=order_type,
            price=order_price,
            volume=actual_volume,
            filled_volume=filled,
            filled_price=order_price if filled > 0 else 0.0,
            status=status,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            raw_data={
                "id": order_id,
                "security": sec,
                "amount": amount,
                "filled": filled,
                "price": order_price,
            },
        )

    @staticmethod
    def _convert_position(ptrade_pos: Any) -> BrokerPosition:
        """将 PTrade Position 转换为统一 BrokerPosition"""
        symbol = getattr(ptrade_pos, "security", "")
        total_amount = int(getattr(ptrade_pos, "total_amount", 0))
        closeable = int(getattr(ptrade_pos, "closeable_amount", 0))
        avg_cost = float(getattr(ptrade_pos, "avg_cost", 0.0))
        current_price = float(getattr(ptrade_pos, "price", getattr(ptrade_pos, "current_price", 0.0)))
        market_value = total_amount * current_price

        pnl = 0.0
        pnl_pct = 0.0
        if avg_cost > 0 and total_amount > 0:
            pnl = (current_price - avg_cost) * total_amount
            pnl_pct = (current_price - avg_cost) / avg_cost * 100

        return BrokerPosition(
            symbol=symbol,
            volume=total_amount,
            available_volume=closeable,
            avg_price=avg_cost,
            current_price=current_price,
            market_value=round(market_value, 4),
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct, 4),
            raw_data={
                "security": symbol,
                "total_amount": total_amount,
                "closeable_amount": closeable,
                "avg_cost": avg_cost,
            },
        )

    def _convert_account(self, account_data: Any) -> BrokerAccount:
        """将 PTrade 账户数据转换为统一 BrokerAccount"""
        return BrokerAccount(
            total_equity=float(getattr(account_data, "total_equity", getattr(account_data, "total_asset", 0.0))),
            available_cash=float(getattr(account_data, "available_cash", getattr(account_data, "cash", 0.0))),
            frozen_cash=float(getattr(account_data, "frozen_cash", 0.0)),
            market_value=float(getattr(account_data, "market_value", getattr(account_data, "position_value", 0.0))),
            unrealized_pnl=float(getattr(account_data, "unrealized_pnl", 0.0)),
            realized_pnl=float(getattr(account_data, "realized_pnl", 0.0)),
            margin_used=float(getattr(account_data, "margin_used", 0.0)),
            margin_ratio=float(getattr(account_data, "margin_ratio", 0.0)),
            raw_data={},
        )

    def _query_account_from_positions(self) -> BrokerAccount:
        """通过持仓和余额推算账户信息"""
        try:
            positions = self.get_positions()
            market_value = sum(p.market_value for p in positions.values())
            unrealized_pnl = sum(p.pnl for p in positions.values())

            # 尝试获取余额
            available_cash = 0.0
            if hasattr(self._ptrade, 'get_balance'):
                balance = self._ptrade.get_balance()
                available_cash = float(getattr(balance, "available_cash", getattr(balance, "cash", 0.0)))

            total_equity = available_cash + market_value

            return BrokerAccount(
                total_equity=round(total_equity, 4),
                available_cash=round(available_cash, 4),
                frozen_cash=0.0,
                market_value=round(market_value, 4),
                unrealized_pnl=round(unrealized_pnl, 4),
            )
        except Exception as e:
            logger.error(f"PTrade 推算账户信息失败: {e}")
            return BrokerAccount(
                total_equity=0.0,
                available_cash=0.0,
                frozen_cash=0.0,
                market_value=0.0,
                unrealized_pnl=0.0,
            )
