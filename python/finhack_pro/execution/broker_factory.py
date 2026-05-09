"""
FinHack Pro 券商适配器工厂

提供统一的券商适配器创建接口，支持：
- 注册自定义券商适配器
- 通过名称创建适配器实例
- 列出可用适配器
- 获取适配器信息

内置适配器：
- paper: PaperBrokerAdapter (模拟交易)
- qmt: QMTAdapter (QMT/XtQuant)
- ptrade: PTradeAdapter (PTrade 云端)

Usage:
    from finhack_pro.execution.broker_factory import BrokerFactory

    # 列出可用适配器
    print(BrokerFactory.list_available())

    # 创建 QMT 适配器
    adapter = BrokerFactory.create("qmt", {
        "mini_qmt_path": "/path/to/userdata_mini",
        "account_id": "123456",
    })

    # 注册自定义适配器
    BrokerFactory.register("custom", MyCustomAdapter)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Type

from finhack_pro.execution.broker_base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerOrder,
    BrokerPosition,
    OrderDirection,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger(__name__)


class BrokerFactory:
    """券商适配器工厂

    管理所有已注册的券商适配器，提供统一的创建接口。
    """

    _registry: Dict[str, Type[BrokerAdapter]] = {}

    @classmethod
    def register(cls, name: str, adapter_class: Type[BrokerAdapter]) -> None:
        """注册券商适配器

        Args:
            name: 适配器名称（小写）
            adapter_class: 适配器类（必须继承 BrokerAdapter）

        Raises:
            TypeError: 如果 adapter_class 不是 BrokerAdapter 的子类
            ValueError: 如果 name 已被注册
        """
        if not issubclass(adapter_class, BrokerAdapter):
            raise TypeError(
                f"{adapter_class.__name__} 必须继承 BrokerAdapter"
            )
        if name in cls._registry:
            logger.warning(
                f"券商适配器 '{name}' 已存在，将被覆盖为 {adapter_class.__name__}"
            )
        cls._registry[name] = adapter_class
        logger.info(f"注册券商适配器: {name} -> {adapter_class.__name__}")

    @classmethod
    def create(
        cls, broker_type: str, config: Optional[Dict[str, Any]] = None
    ) -> BrokerAdapter:
        """创建券商适配器实例

        Args:
            broker_type: 适配器名称
            config: 配置参数

        Returns:
            券商适配器实例

        Raises:
            ValueError: 如果 broker_type 未注册
        """
        if config is None:
            config = {}

        broker_type = broker_type.lower()
        adapter_class = cls._registry.get(broker_type)

        if adapter_class is None:
            available = ", ".join(sorted(cls._registry.keys()))
            raise ValueError(
                f"未知的券商类型: '{broker_type}'。"
                f"可用类型: {available}"
            )

        adapter = adapter_class(config)
        logger.info(f"创建券商适配器: {broker_type} ({adapter_class.__name__})")
        return adapter

    @classmethod
    def list_available(cls) -> List[str]:
        """列出所有已注册的适配器名称

        Returns:
            适配器名称列表
        """
        return sorted(cls._registry.keys())

    @classmethod
    def get_broker_info(cls, name: str) -> Dict[str, Any]:
        """获取适配器信息

        Args:
            name: 适配器名称

        Returns:
            包含适配器信息的字典

        Raises:
            ValueError: 如果 name 未注册
        """
        adapter_class = cls._registry.get(name)
        if adapter_class is None:
            raise ValueError(f"未知的券商类型: '{name}'")

        # 创建临时实例获取元信息
        info: Dict[str, Any] = {
            "name": name,
            "class": adapter_class.__name__,
            "module": adapter_class.__module__,
        }

        try:
            temp = adapter_class({})
            info["broker_name"] = temp.broker_name
            info["supported_features"] = temp.supported_features
        except Exception:
            info["broker_name"] = name
            info["supported_features"] = []

        return info

    @classmethod
    def unregister(cls, name: str) -> bool:
        """取消注册适配器

        Args:
            name: 适配器名称

        Returns:
            是否取消成功
        """
        if name in cls._registry:
            del cls._registry[name]
            return True
        return False


# ============================================================================
# Paper Broker Adapter (wraps existing PaperBroker)
# ============================================================================


class PaperBrokerAdapter(BrokerAdapter):
    """模拟交易适配器

    将现有的 PaperBroker 包装为 BrokerAdapter 接口，
    用于测试和开发环境。
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        from finhack_pro.execution.live_trader import PaperBroker

        initial_cash = config.get("initial_cash", 1_000_000.0)
        self._paper_broker = PaperBroker(initial_cash=initial_cash)

    @property
    def broker_name(self) -> str:
        return "paper"

    @property
    def supported_features(self) -> List[str]:
        return ["order", "cancel", "query", "market_data"]

    def connect(self) -> bool:
        self._connected = True
        logger.info("PaperBroker 连接成功（模拟）")
        return True

    def disconnect(self) -> None:
        self._connected = False
        logger.info("PaperBroker 已断开")

    def submit_order(
        self,
        symbol: str,
        direction: OrderDirection,
        order_type: OrderType,
        price: float,
        volume: int,
        **kwargs: Any,
    ) -> BrokerOrder:
        from finhack_pro.execution.live_trader import Order as LiveOrder
        from finhack_pro.execution.live_trader import OrderStatus as LiveOrderStatus

        if not self._connected:
            raise RuntimeError("PaperBroker 未连接")

        live_order = LiveOrder(
            symbol=symbol,
            direction=direction.value,
            order_type=order_type.value,
            price=price,
            volume=volume,
        )
        result = self._paper_broker.submit_order(live_order)

        status_map = {
            LiveOrderStatus.PENDING.value: OrderStatus.PENDING,
            LiveOrderStatus.SUBMITTED.value: OrderStatus.SUBMITTED,
            LiveOrderStatus.PARTIAL_FILLED.value: OrderStatus.PARTIAL_FILLED,
            LiveOrderStatus.FILLED.value: OrderStatus.FILLED,
            LiveOrderStatus.CANCELLED.value: OrderStatus.CANCELLED,
            LiveOrderStatus.REJECTED.value: OrderStatus.REJECTED,
        }

        return BrokerOrder(
            broker_order_id=result.order_id,
            symbol=result.symbol,
            direction=direction,
            order_type=order_type,
            price=result.price,
            volume=result.volume,
            filled_volume=result.filled_volume,
            filled_price=result.filled_price,
            status=status_map.get(result.status, OrderStatus.UNKNOWN),
            commission=result.commission,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        if not self._connected:
            raise RuntimeError("PaperBroker 未连接")
        return self._paper_broker.cancel_order(broker_order_id)

    def get_orders(self, symbol: Optional[str] = None) -> List[BrokerOrder]:
        from finhack_pro.execution.live_trader import OrderStatus as LiveOrderStatus

        if not self._connected:
            raise RuntimeError("PaperBroker 未连接")

        live_orders = self._paper_broker.get_orders()
        status_map = {
            LiveOrderStatus.PENDING.value: OrderStatus.PENDING,
            LiveOrderStatus.SUBMITTED.value: OrderStatus.SUBMITTED,
            LiveOrderStatus.PARTIAL_FILLED.value: OrderStatus.PARTIAL_FILLED,
            LiveOrderStatus.FILLED.value: OrderStatus.FILLED,
            LiveOrderStatus.CANCELLED.value: OrderStatus.CANCELLED,
            LiveOrderStatus.REJECTED.value: OrderStatus.REJECTED,
        }

        result = []
        for lo in live_orders:
            if symbol and lo.symbol != symbol:
                continue
            result.append(BrokerOrder(
                broker_order_id=lo.order_id,
                symbol=lo.symbol,
                direction=OrderDirection(lo.direction),
                order_type=OrderType(lo.order_type),
                price=lo.price,
                volume=lo.volume,
                filled_volume=lo.filled_volume,
                filled_price=lo.filled_price,
                status=status_map.get(lo.status, OrderStatus.UNKNOWN),
                commission=lo.commission,
            ))
        return result

    def get_positions(
        self, symbol: Optional[str] = None
    ) -> Dict[str, BrokerPosition]:
        if not self._connected:
            raise RuntimeError("PaperBroker 未连接")

        live_positions = self._paper_broker.get_positions()
        result = {}
        for sym, pos in live_positions.items():
            if symbol and sym != symbol:
                continue
            result[sym] = BrokerPosition(
                symbol=sym,
                volume=pos.quantity,
                available_volume=pos.quantity,
                avg_price=pos.avg_price,
                current_price=pos.current_price,
                market_value=pos.market_value,
                pnl=pos.pnl,
                pnl_pct=pos.pnl_pct,
            )
        return result

    def get_account(self) -> BrokerAccount:
        if not self._connected:
            raise RuntimeError("PaperBroker 未连接")

        info = self._paper_broker.get_account_info()
        return BrokerAccount(
            total_equity=info.total_equity,
            available_cash=info.available_cash,
            frozen_cash=0.0,
            market_value=info.total_position_value,
            unrealized_pnl=info.unrealized_pnl,
            realized_pnl=info.realized_pnl,
            margin_used=info.margin_used,
            margin_ratio=info.margin_ratio,
        )

    def subscribe_market_data(
        self, symbols: List[str], callback: Any
    ) -> bool:
        if not self._connected:
            raise RuntimeError("PaperBroker 未连接")
        # PaperBroker 不需要显式订阅
        return True

    @property
    def paper_broker(self):
        """暴露底层 PaperBroker 实例（用于设置价格等）"""
        return self._paper_broker


# ============================================================================
# Auto-register built-in adapters
# ============================================================================

# 注意：QMT 和 PTrade 适配器的注册延迟到导入时执行，
# 因为它们依赖可能未安装的第三方 SDK。
# 这里只注册 paper 适配器。
BrokerFactory.register("paper", PaperBrokerAdapter)


def _register_sdk_adapters() -> None:
    """尝试注册依赖第三方 SDK 的适配器"""
    # QMT
    try:
        from finhack_pro.execution.broker_qmt import QMTAdapter
        BrokerFactory.register("qmt", QMTAdapter)
    except Exception:
        pass

    # PTrade
    try:
        from finhack_pro.execution.broker_ptrade import PTradeAdapter
        BrokerFactory.register("ptrade", PTradeAdapter)
    except Exception:
        pass


# 尝试注册 SDK 适配器
_register_sdk_adapters()
