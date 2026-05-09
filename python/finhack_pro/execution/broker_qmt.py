"""
FinHack Pro QMT (XtQuant) 券商适配器

基于 XtQuant SDK 实现的券商适配器，支持：
- 连接/断开 QMT 客户端
- 下单/撤单（限价、市价、限价只做Maker）
- 查询订单、持仓、账户
- 订阅实时行情
- 回调事件分发

XtQuant 常量映射：
- 方向: STOCK_BUY=23, STOCK_SELL=24
- 价格类型: FIX_PRICE=11, LATEST_PRICE=5, MARKET_BEST_5=43, MARKET_BEST=44,
            MARKET_SH5=45, MARKET_SH=46, MARKET_SZ5=47, MARKET_SZ=48
- 订单状态: ORDER_UNREPORTED=48, ORDER_WAITING=49, ORDER_REPORTED=50,
             ORDER_SUCCEEDED=56, ORDER_PARTIALEFFECT=51, ORDER_CANCELLING=52,
             ORDER_CANCELLED=53, ORDER_REJECTED=54, ORDER_JUNK=57

Usage:
    from finhack_pro.execution.broker_qmt import QMTAdapter

    config = {
        "mini_qmt_path": "/path/to/userdata_mini",
        "account_id": "your_account_id",
        "account_type": "STOCK",
    }
    adapter = QMTAdapter(config)
    adapter.connect()
    order = adapter.submit_order("600519.SH", OrderDirection.BUY,
                                  OrderType.LIMIT, 1800.0, 100)
"""

from __future__ import annotations

import logging
import threading
import time
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
# XtQuant 常量映射
# ============================================================================

# 交易方向
XT_STOCK_BUY = 23
XT_STOCK_SELL = 24

# 价格类型
XT_FIX_PRICE = 11           # 固定价格
XT_LATEST_PRICE = 5         # 最新价格
XT_MARKET_BEST_5 = 43       # 五档最优
XT_MARKET_BEST = 44         # 最优价格
XT_MARKET_SH5 = 45          # 上海五档即成剩撤
XT_MARKET_SH = 46           # 上海即成剩撤
XT_MARKET_SZ5 = 47          # 深圳五档即成剩撤
XT_MARKET_SZ = 48           # 深圳即成剩撤

# 订单状态
XT_ORDER_UNREPORTED = 48    # 未报
XT_ORDER_WAITING = 49       # 待报
XT_ORDER_REPORTED = 50      # 已报
XT_ORDER_PARTIALEFFECT = 51 # 部成
XT_ORDER_CANCELLING = 52    # 撤单中
XT_ORDER_CANCELLED = 53     # 已撤
XT_ORDER_REJECTED = 54      # 废单
XT_ORDER_SUCCEEDED = 56     # 全成
XT_ORDER_JUNK = 57          # 垃圾（无效）单

# 订单类型 -> XtQuant 价格类型映射
_ORDER_TYPE_TO_XT: Dict[OrderType, int] = {
    OrderType.LIMIT: XT_FIX_PRICE,
    OrderType.MARKET: XT_LATEST_PRICE,
    OrderType.LIMIT_MAKER: XT_FIX_PRICE,
}

# XtQuant 订单状态 -> 统一状态映射
_XT_STATUS_MAP: Dict[int, OrderStatus] = {
    XT_ORDER_UNREPORTED: OrderStatus.PENDING,
    XT_ORDER_WAITING: OrderStatus.PENDING,
    XT_ORDER_REPORTED: OrderStatus.SUBMITTED,
    XT_ORDER_PARTIALEFFECT: OrderStatus.PARTIAL_FILLED,
    XT_ORDER_CANCELLING: OrderStatus.SUBMITTED,
    XT_ORDER_CANCELLED: OrderStatus.CANCELLED,
    XT_ORDER_REJECTED: OrderStatus.REJECTED,
    XT_ORDER_SUCCEEDED: OrderStatus.FILLED,
    XT_ORDER_JUNK: OrderStatus.UNKNOWN,
}

# 统一方向 -> XtQuant 方向映射
_DIRECTION_TO_XT: Dict[OrderDirection, int] = {
    OrderDirection.BUY: XT_STOCK_BUY,
    OrderDirection.SELL: XT_STOCK_SELL,
}

# XtQuant 方向 -> 统一方向映射
_XT_DIRECTION_MAP: Dict[int, OrderDirection] = {
    XT_STOCK_BUY: OrderDirection.BUY,
    XT_STOCK_SELL: OrderDirection.SELL,
}

# XtQuant 价格类型 -> 统一订单类型映射
_XT_PRICE_TYPE_MAP: Dict[int, OrderType] = {
    XT_FIX_PRICE: OrderType.LIMIT,
    XT_LATEST_PRICE: OrderType.MARKET,
    XT_MARKET_BEST_5: OrderType.MARKET,
    XT_MARKET_BEST: OrderType.MARKET,
    XT_MARKET_SH5: OrderType.MARKET,
    XT_MARKET_SH: OrderType.MARKET,
    XT_MARKET_SZ5: OrderType.MARKET,
    XT_MARKET_SZ: OrderType.MARKET,
}


# ============================================================================
# Lazy import helper
# ============================================================================

def _import_xtquant():
    """延迟导入 xtquant，仅在运行时需要时加载"""
    try:
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        from xtquant.xttype import (
            StockAccount,
            XtAsset,
            XtOrder,
            XtPosition,
            XtTrade,
        )
        return {
            "XtQuantTrader": XtQuantTrader,
            "XtQuantTraderCallback": XtQuantTraderCallback,
            "StockAccount": StockAccount,
            "XtOrder": XtOrder,
            "XtPosition": XtPosition,
            "XtAsset": XtAsset,
            "XtTrade": XtTrade,
        }
    except ImportError:
        logger.error(
            "xtquant 未安装。请安装 QMT 客户端后使用。"
            "安装方式：参考 QMT 官方文档。"
        )
        raise


# ============================================================================
# QMT Callback Handler
# ============================================================================


class _QMTCallbackHandler:
    """QMT 回调处理器

    将 XtQuant 的回调事件转换为统一格式并分发到 BrokerCallback。
    回调在独立线程中执行，避免阻塞 XtQuant 的回调线程。
    """

    def __init__(self, adapter: "QMTAdapter") -> None:
        self._adapter = adapter
        self._event_queue: List[Any] = []
        self._event_lock = threading.Lock()
        self._dispatch_thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """启动事件分发线程"""
        self._running = True
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True
        )
        self._dispatch_thread.start()

    def stop(self) -> None:
        """停止事件分发线程"""
        self._running = False
        if self._dispatch_thread and self._dispatch_thread.is_alive():
            self._dispatch_thread.join(timeout=5.0)

    def _enqueue(self, event: Any) -> None:
        """将事件放入队列"""
        with self._event_lock:
            self._event_queue.append(event)

    def _dispatch_loop(self) -> None:
        """事件分发循环"""
        while self._running:
            events = []
            with self._event_lock:
                events = self._event_queue[:]
                self._event_queue.clear()

            for event in events:
                try:
                    self._dispatch(event)
                except Exception as e:
                    logger.error(f"事件分发异常: {e}")

            time.sleep(0.01)  # 10ms 轮询间隔

    def _dispatch(self, event: Any) -> None:
        """分发单个事件"""
        event_type = event.get("type")
        callback = self._adapter._callback
        if callback is None:
            return

        if event_type == "order":
            callback.on_order_update(event["data"])
        elif event_type == "trade":
            callback.on_trade_update(event["data"])
        elif event_type == "position":
            callback.on_position_update(event["data"])
        elif event_type == "account":
            callback.on_account_update(event["data"])
        elif event_type == "error":
            callback.on_error(event["data"]["error_msg"], event["data"].get("order_id", ""))
        elif event_type == "disconnected":
            callback.on_disconnected()


# ============================================================================
# QMT Adapter
# ============================================================================


class QMTAdapter(BrokerAdapter):
    """QMT (XtQuant) 券商适配器

    通过 XtQuant SDK 连接 QMT 客户端进行交易。

    配置参数：
        mini_qmt_path: str - QMT userdata_mini 路径
        account_id: str - 资金账号
        account_type: str - 账户类型，默认 "STOCK"
        session_id: int - 会话ID，默认 123456
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self._mini_qmt_path = config.get("mini_qmt_path", "")
        self._account_id = config.get("account_id", "")
        self._account_type = config.get("account_type", "STOCK")
        self._session_id = config.get("session_id", 123456)

        # XtQuant 对象（延迟初始化）
        self._xt_trader: Any = None
        self._xt_account: Any = None
        self._callback_handler: Optional[_QMTCallbackHandler] = None

    @property
    def broker_name(self) -> str:
        return "qmt"

    @property
    def supported_features(self) -> List[str]:
        return ["order", "cancel", "query", "market_data", "callback"]

    def connect(self) -> bool:
        """连接 QMT 客户端

        Returns:
            是否连接成功
        """
        try:
            xt = _import_xtquant()

            # 创建 XtQuantTrader 实例
            self._xt_trader = xt["XtQuantTrader"](
                self._mini_qmt_path, self._session_id
            )

            # 创建资金账号
            self._xt_account = xt["StockAccount"](
                self._account_id, self._account_type
            )

            # 启动连接
            self._xt_trader.start()

            # 注册回调
            self._callback_handler = _QMTCallbackHandler(self)
            self._xt_trader.register_callback(self._callback_handler)
            self._callback_handler.start()

            # 订阅账户
            self._xt_trader.subscribe(self._xt_account)

            with self._lock:
                self._connected = True

            logger.info(
                f"QMT 连接成功: account={self._account_id}, "
                f"type={self._account_type}"
            )
            return True

        except ImportError:
            logger.error("xtquant 未安装，无法连接 QMT")
            return False
        except Exception as e:
            logger.error(f"QMT 连接失败: {e}")
            return False

    def disconnect(self) -> None:
        """断开 QMT 连接"""
        try:
            if self._callback_handler:
                self._callback_handler.stop()
                self._callback_handler = None

            if self._xt_trader:
                self._xt_trader.stop()
                self._xt_trader = None

            with self._lock:
                self._connected = False

            logger.info("QMT 已断开连接")
        except Exception as e:
            logger.error(f"QMT 断开连接异常: {e}")
            with self._lock:
                self._connected = False

    def submit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        order_type: OrderType,
        price: float,
        volume: int,
        **kwargs: Any,
    ) -> BrokerOrder:
        """提交订单到 QMT

        Args:
            symbol: 证券代码 (e.g., "600519.SH")
            direction: 买卖方向
            order_type: 订单类型
            price: 委托价格
            volume: 委托数量（股）
            **kwargs: 额外参数
                - strategy_name: 策略名称
                - order_remark: 订单备注

        Returns:
            统一的 BrokerOrder 对象
        """
        if not self._connected:
            raise RuntimeError("QMT 未连接，请先调用 connect()")

        try:
            xt_direction = _DIRECTION_TO_XT[direction]
            xt_price_type = _ORDER_TYPE_TO_XT[order_type]

            strategy_name = kwargs.get("strategy_name", "")
            order_remark = kwargs.get("order_remark", "")

            # 调用 XtQuant 下单
            xt_order_id = self._xt_trader.order_stock(
                self._xt_account,
                symbol,
                xt_direction,
                volume,
                xt_price_type,
                price,
                strategy_name,
                order_remark,
            )

            return BrokerOrder(
                broker_order_id=str(xt_order_id),
                symbol=symbol,
                direction=direction,
                order_type=order_type,
                price=price,
                volume=volume,
                status=OrderStatus.SUBMITTED,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )

        except Exception as e:
            logger.error(f"QMT 下单失败: {e}")
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
            broker_order_id: QMT 订单ID

        Returns:
            是否取消成功
        """
        if not self._connected:
            raise RuntimeError("QMT 未连接")

        try:
            self._xt_trader.cancel_order_stock(
                self._xt_account, int(broker_order_id)
            )
            return True
        except Exception as e:
            logger.error(f"QMT 撤单失败: {e}")
            return False

    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        """查询订单

        Args:
            symbol: 证券代码过滤，为空返回全部

        Returns:
            订单列表
        """
        if not self._connected:
            raise RuntimeError("QMT 未连接")

        try:
            xt_orders = self._xt_trader.query_stock_orders(self._xt_account)
            result = []

            for xt_order in xt_orders:
                order = self._convert_order(xt_order)
                if symbol is None or order.symbol == symbol:
                    result.append(order)

            return result
        except Exception as e:
            logger.error(f"QMT 查询订单失败: {e}")
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
            raise RuntimeError("QMT 未连接")

        try:
            xt_positions = self._xt_trader.query_stock_positions(
                self._xt_account
            )
            result = {}

            for xt_pos in xt_positions:
                position = self._convert_position(xt_pos)
                if position.volume > 0:
                    if symbol is None or position.symbol == symbol:
                        result[position.symbol] = position

            return result
        except Exception as e:
            logger.error(f"QMT 查询持仓失败: {e}")
            return {}

    def get_account(self) -> BrokerAccount:
        """查询账户信息

        Returns:
            统一的账户对象
        """
        if not self._connected:
            raise RuntimeError("QMT 未连接")

        try:
            xt_asset = self._xt_trader.query_stock_asset(self._xt_account)
            return self._convert_account(xt_asset)
        except Exception as e:
            logger.error(f"QMT 查询账户失败: {e}")
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

        Args:
            symbols: 证券代码列表
            callback: 行情回调函数

        Returns:
            是否订阅成功
        """
        if not self._connected:
            raise RuntimeError("QMT 未连接")

        try:
            from xtquant.xtdata import subscribe_quote
            subscribe_quote(symbols)
            logger.info(f"QMT 行情订阅成功: {symbols}")
            return True
        except ImportError:
            logger.error("xtquant.xtdata 不可用")
            return False
        except Exception as e:
            logger.error(f"QMT 行情订阅失败: {e}")
            return False

    # ========================================================================
    # Internal converters
    # ========================================================================

    @staticmethod
    def _convert_order(xt_order: Any) -> BrokerOrder:
        """将 XtQuant XtOrder 转换为统一 BrokerOrder"""
        xt_status = getattr(xt_order, "order_status", XT_ORDER_UNREPORTED)
        status = _XT_STATUS_MAP.get(xt_status, OrderStatus.UNKNOWN)

        xt_direction = getattr(xt_order, "order_type", 0)
        direction = _XT_DIRECTION_MAP.get(xt_direction, OrderDirection.BUY)

        xt_price_type = getattr(xt_order, "price_type", XT_FIX_PRICE)
        order_type = _XT_PRICE_TYPE_MAP.get(xt_price_type, OrderType.LIMIT)

        return BrokerOrder(
            broker_order_id=str(getattr(xt_order, "order_id", "")),
            symbol=getattr(xt_order, "stock_code", ""),
            direction=direction,
            order_type=order_type,
            price=float(getattr(xt_order, "order_price", 0.0)),
            volume=int(getattr(xt_order, "order_volume", 0)),
            filled_volume=int(getattr(xt_order, "traded_volume", 0)),
            filled_price=float(getattr(xt_order, "traded_price", 0.0)),
            status=status,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            raw_data={
                "order_id": getattr(xt_order, "order_id", ""),
                "order_sysid": getattr(xt_order, "order_sysid", ""),
                "order_status": xt_status,
                "strategy_name": getattr(xt_order, "strategy_name", ""),
                "order_remark": getattr(xt_order, "order_remark", ""),
            },
        )

    @staticmethod
    def _convert_position(xt_pos: Any) -> BrokerPosition:
        """将 XtQuant XtPosition 转换为统一 BrokerPosition"""
        volume = int(getattr(xt_pos, "volume", 0))
        available = int(getattr(xt_pos, "can_use_volume", 0))
        avg_price = float(getattr(xt_pos, "open_price", 0.0))
        current_price = float(getattr(xt_pos, "last_price", 0.0))
        market_value = float(getattr(xt_pos, "market_value", 0.0))

        pnl = 0.0
        pnl_pct = 0.0
        if avg_price > 0 and volume > 0:
            pnl = (current_price - avg_price) * volume
            pnl_pct = (current_price - avg_price) / avg_price * 100

        return BrokerPosition(
            symbol=getattr(xt_pos, "stock_code", ""),
            volume=volume,
            available_volume=available,
            avg_price=avg_price,
            current_price=current_price,
            market_value=market_value,
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct, 4),
            raw_data={
                "volume": volume,
                "can_use_volume": available,
                "open_price": avg_price,
                "last_price": current_price,
            },
        )

    @staticmethod
    def _convert_account(xt_asset: Any) -> BrokerAccount:
        """将 XtQuant XtAsset 转换为统一 BrokerAccount"""
        total_equity = float(getattr(xt_asset, "total_asset", 0.0))
        available_cash = float(getattr(xt_asset, "cash", 0.0))
        frozen_cash = float(getattr(xt_asset, "frozen_cash", 0.0))
        market_value = float(getattr(xt_asset, "market_value", 0.0))

        return BrokerAccount(
            total_equity=total_equity,
            available_cash=available_cash,
            frozen_cash=frozen_cash,
            market_value=market_value,
            unrealized_pnl=float(getattr(xt_asset, "unrealized_pnl", 0.0)),
            realized_pnl=float(getattr(xt_asset, "realized_pnl", 0.0)),
            margin_used=float(getattr(xt_asset, "margin", 0.0)),
            margin_ratio=float(getattr(xt_asset, "margin_ratio", 0.0)),
            raw_data={
                "total_asset": total_equity,
                "cash": available_cash,
                "frozen_cash": frozen_cash,
            },
        )
