"""
FinHack Pro 券商适配器测试

覆盖:
- BrokerOrder/Position/Account: 数据类创建和默认值
- BrokerFactory: 注册、创建、列表、未知券商错误
- QMTAdapter: mock xtquant，测试连接/下单/撤单/查询
- PTradeAdapter: mock ptrade API，测试连接/下单/撤单/查询
- PaperBrokerAdapter: 包装现有 PaperBroker
- AdapterIntegration: LiveTrader 与 BrokerFactory 集成
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

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
from finhack_pro.execution.broker_factory import (
    BrokerFactory,
    PaperBrokerAdapter,
)

# ============================================================================
# Test Data Classes
# ============================================================================


class TestBrokerOrder:
    """BrokerOrder 数据类测试"""

    def test_creation_with_required_fields(self):
        """测试必填字段创建"""
        order = BrokerOrder(
            broker_order_id="ORD001",
            symbol="600519.SH",
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT,
            price=1800.0,
            volume=100,
        )
        assert order.broker_order_id == "ORD001"
        assert order.symbol == "600519.SH"
        assert order.direction == OrderDirection.BUY
        assert order.order_type == OrderType.LIMIT
        assert order.price == 1800.0
        assert order.volume == 100

    def test_defaults(self):
        """测试默认值"""
        order = BrokerOrder(
            broker_order_id="ORD002",
            symbol="000001.SZ",
            direction=OrderDirection.SELL,
            order_type=OrderType.MARKET,
            price=10.0,
            volume=200,
        )
        assert order.filled_volume == 0
        assert order.filled_price == 0.0
        assert order.status == OrderStatus.PENDING
        assert order.commission == 0.0
        assert order.error_msg == ""
        assert order.raw_data == {}
        assert isinstance(order.created_at, datetime)
        assert isinstance(order.updated_at, datetime)

    def test_all_fields(self):
        """测试所有字段"""
        now = datetime.now()
        order = BrokerOrder(
            broker_order_id="ORD003",
            symbol="300750.SZ",
            direction=OrderDirection.BUY,
            order_type=OrderType.LIMIT_MAKER,
            price=200.0,
            volume=500,
            filled_volume=300,
            filled_price=199.5,
            status=OrderStatus.PARTIAL_FILLED,
            created_at=now,
            updated_at=now,
            commission=15.0,
            error_msg="",
            raw_data={"xt_id": 12345},
        )
        assert order.filled_volume == 300
        assert order.filled_price == 199.5
        assert order.status == OrderStatus.PARTIAL_FILLED
        assert order.commission == 15.0
        assert order.raw_data == {"xt_id": 12345}


class TestBrokerPosition:
    """BrokerPosition 数据类测试"""

    def test_creation(self):
        """测试创建"""
        pos = BrokerPosition(
            symbol="600519.SH",
            volume=1000,
            available_volume=800,
            avg_price=1750.0,
            current_price=1800.0,
            market_value=1_800_000.0,
            pnl=50_000.0,
            pnl_pct=2.86,
        )
        assert pos.symbol == "600519.SH"
        assert pos.volume == 1000
        assert pos.available_volume == 800
        assert pos.pnl == 50_000.0

    def test_defaults(self):
        """测试默认值"""
        pos = BrokerPosition(
            symbol="000001.SZ",
            volume=100,
            available_volume=100,
            avg_price=10.0,
            current_price=10.5,
            market_value=1050.0,
        )
        assert pos.pnl == 0.0
        assert pos.pnl_pct == 0.0
        assert pos.raw_data == {}


class TestBrokerAccount:
    """BrokerAccount 数据类测试"""

    def test_creation(self):
        """测试创建"""
        acct = BrokerAccount(
            total_equity=2_000_000.0,
            available_cash=500_000.0,
            frozen_cash=100_000.0,
            market_value=1_400_000.0,
            unrealized_pnl=50_000.0,
            realized_pnl=20_000.0,
            margin_used=700_000.0,
            margin_ratio=35.0,
        )
        assert acct.total_equity == 2_000_000.0
        assert acct.available_cash == 500_000.0
        assert acct.realized_pnl == 20_000.0

    def test_defaults(self):
        """测试默认值"""
        acct = BrokerAccount(
            total_equity=1_000_000.0,
            available_cash=1_000_000.0,
            frozen_cash=0.0,
            market_value=0.0,
            unrealized_pnl=0.0,
        )
        assert acct.realized_pnl == 0.0
        assert acct.margin_used == 0.0
        assert acct.margin_ratio == 0.0
        assert acct.raw_data == {}


# ============================================================================
# Test Enums
# ============================================================================


class TestEnums:
    """枚举类型测试"""

    def test_order_direction(self):
        assert OrderDirection.BUY.value == "buy"
        assert OrderDirection.SELL.value == "sell"

    def test_order_type(self):
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT_MAKER.value == "limit_maker"

    def test_order_status(self):
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.SUBMITTED.value == "submitted"
        assert OrderStatus.PARTIAL_FILLED.value == "partial_filled"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.REJECTED.value == "rejected"
        assert OrderStatus.UNKNOWN.value == "unknown"


# ============================================================================
# Test BrokerCallback (abstract)
# ============================================================================


class TestBrokerCallback:
    """BrokerCallback 抽象类测试"""

    def test_cannot_instantiate(self):
        """不能直接实例化抽象类"""
        with pytest.raises(TypeError):
            BrokerCallback()

    def test_concrete_implementation(self):
        """具体实现可以实例化"""

        class MyCallback(BrokerCallback):
            def on_order_update(self, order: BrokerOrder) -> None:
                pass

            def on_trade_update(self, order: BrokerOrder) -> None:
                pass

            def on_position_update(self, position: BrokerPosition) -> None:
                pass

            def on_account_update(self, account: BrokerAccount) -> None:
                pass

            def on_error(self, error_msg: str, order_id: str = "") -> None:
                pass

            def on_disconnected(self) -> None:
                pass

        cb = MyCallback()
        assert isinstance(cb, BrokerCallback)


# ============================================================================
# Test BrokerAdapter (abstract)
# ============================================================================


class TestBrokerAdapter:
    """BrokerAdapter 抽象类测试"""

    def test_cannot_instantiate(self):
        """不能直接实例化抽象类"""
        with pytest.raises(TypeError):
            BrokerAdapter({})

    def test_concrete_implementation(self):
        """具体实现必须实现所有抽象方法"""

        class MinimalAdapter(BrokerAdapter):
            @property
            def broker_name(self) -> str:
                return "minimal"

            @property
            def supported_features(self) -> List[str]:
                return []

            def connect(self) -> bool:
                self._connected = True
                return True

            def disconnect(self) -> None:
                self._connected = False

            def submit_order(self, symbol, direction, order_type, price, volume, **kwargs):
                return BrokerOrder(
                    broker_order_id="1",
                    symbol=symbol,
                    direction=direction,
                    order_type=order_type,
                    price=price,
                    volume=volume,
                )

            def cancel_order(self, broker_order_id: str) -> bool:
                return True

            def get_orders(self, symbol=None):
                return []

            def get_positions(self, symbol=None):
                return {}

            def get_account(self):
                return BrokerAccount(
                    total_equity=0, available_cash=0,
                    frozen_cash=0, market_value=0, unrealized_pnl=0,
                )

            def subscribe_market_data(self, symbols, callback):
                return True

        adapter = MinimalAdapter({})
        assert adapter.broker_name == "minimal"
        assert adapter.supported_features == []
        assert not adapter.is_connected

        adapter.connect()
        assert adapter.is_connected

        adapter.disconnect()
        assert not adapter.is_connected

    def test_register_callback(self):
        """测试注册回调"""

        class DummyAdapter(BrokerAdapter):
            @property
            def broker_name(self) -> str:
                return "dummy"

            @property
            def supported_features(self) -> List[str]:
                return []

            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def submit_order(self, symbol, direction, order_type, price, volume, **kwargs):
                return BrokerOrder(broker_order_id="1", symbol=symbol,
                                   direction=direction, order_type=order_type,
                                   price=price, volume=volume)

            def cancel_order(self, broker_order_id: str) -> bool:
                return True

            def get_orders(self, symbol=None):
                return []

            def get_positions(self, symbol=None):
                return {}

            def get_account(self):
                return BrokerAccount(total_equity=0, available_cash=0,
                                     frozen_cash=0, market_value=0, unrealized_pnl=0)

            def subscribe_market_data(self, symbols, callback):
                return True

        adapter = DummyAdapter({})

        class MyCallback(BrokerCallback):
            def on_order_update(self, order: BrokerOrder) -> None:
                pass

            def on_trade_update(self, order: BrokerOrder) -> None:
                pass

            def on_position_update(self, position: BrokerPosition) -> None:
                pass

            def on_account_update(self, account: BrokerAccount) -> None:
                pass

            def on_error(self, error_msg: str, order_id: str = "") -> None:
                pass

            def on_disconnected(self) -> None:
                pass

        cb = MyCallback()
        adapter.register_callback(cb)
        assert adapter._callback is cb


# ============================================================================
# Test BrokerFactory
# ============================================================================


class TestBrokerFactory:
    """BrokerFactory 工厂测试"""

    def setup_method(self):
        """每个测试前清理注册表（保留 paper）"""
        # 保存当前注册表
        self._saved_registry = dict(BrokerFactory._registry)
        # 重置为只有 paper
        BrokerFactory._registry = {}
        BrokerFactory.register("paper", PaperBrokerAdapter)

    def teardown_method(self):
        """恢复注册表"""
        BrokerFactory._registry = self._saved_registry

    def test_list_available(self):
        """测试列出可用适配器"""
        available = BrokerFactory.list_available()
        assert "paper" in available

    def test_register_custom(self):
        """测试注册自定义适配器"""

        class CustomAdapter(BrokerAdapter):
            @property
            def broker_name(self) -> str:
                return "custom"

            @property
            def supported_features(self) -> List[str]:
                return ["order"]

            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def submit_order(self, symbol, direction, order_type, price, volume, **kwargs):
                return BrokerOrder(broker_order_id="1", symbol=symbol,
                                   direction=direction, order_type=order_type,
                                   price=price, volume=volume)

            def cancel_order(self, broker_order_id: str) -> bool:
                return True

            def get_orders(self, symbol=None):
                return []

            def get_positions(self, symbol=None):
                return {}

            def get_account(self):
                return BrokerAccount(total_equity=0, available_cash=0,
                                     frozen_cash=0, market_value=0, unrealized_pnl=0)

            def subscribe_market_data(self, symbols, callback):
                return True

        BrokerFactory.register("custom", CustomAdapter)
        assert "custom" in BrokerFactory.list_available()

    def test_register_invalid_type(self):
        """测试注册非 BrokerAdapter 子类"""
        with pytest.raises(TypeError, match="必须继承 BrokerAdapter"):
            BrokerFactory.register("invalid", str)  # type: ignore

    def test_create_paper(self):
        """测试创建 paper 适配器"""
        adapter = BrokerFactory.create("paper")
        assert isinstance(adapter, PaperBrokerAdapter)
        assert adapter.broker_name == "paper"

    def test_create_unknown(self):
        """测试创建未知适配器"""
        with pytest.raises(ValueError, match="未知的券商类型"):
            BrokerFactory.create("nonexistent_broker")

    def test_create_with_config(self):
        """测试带配置创建"""
        adapter = BrokerFactory.create("paper", {"initial_cash": 500_000.0})
        assert isinstance(adapter, PaperBrokerAdapter)
        adapter.connect()
        account = adapter.get_account()
        assert account.total_equity == 500_000.0

    def test_get_broker_info(self):
        """测试获取适配器信息"""
        info = BrokerFactory.get_broker_info("paper")
        assert info["name"] == "paper"
        assert info["class"] == "PaperBrokerAdapter"
        assert "order" in info["supported_features"]

    def test_get_broker_info_unknown(self):
        """测试获取未知适配器信息"""
        with pytest.raises(ValueError, match="未知的券商类型"):
            BrokerFactory.get_broker_info("unknown")

    def test_unregister(self):
        """测试取消注册"""
        assert "paper" in BrokerFactory.list_available()
        result = BrokerFactory.unregister("paper")
        assert result
        assert "paper" not in BrokerFactory.list_available()

    def test_unregister_nonexistent(self):
        """测试取消注册不存在的适配器"""
        result = BrokerFactory.unregister("nonexistent")
        assert not result


# ============================================================================
# Test PaperBrokerAdapter
# ============================================================================


class TestPaperBrokerAdapter:
    """PaperBrokerAdapter 测试"""

    def test_connect_disconnect(self):
        """测试连接和断开"""
        adapter = PaperBrokerAdapter({})
        assert not adapter.is_connected
        assert adapter.connect() is True
        assert adapter.is_connected
        adapter.disconnect()
        assert not adapter.is_connected

    def test_submit_order(self):
        """测试提交订单"""
        adapter = PaperBrokerAdapter({})
        adapter.connect()
        adapter.paper_broker.set_market_price("000001.SZ", 10.0)

        order = adapter.submit_order(
            "000001.SZ", OrderDirection.BUY, OrderType.LIMIT, 10.0, 100
        )
        assert order.symbol == "000001.SZ"
        assert order.direction == OrderDirection.BUY
        assert order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL_FILLED)
        assert order.filled_volume > 0

    def test_cancel_order(self):
        """测试撤单"""
        adapter = PaperBrokerAdapter({})
        adapter.connect()
        adapter.paper_broker.set_market_price("000001.SZ", 10.0)

        order = adapter.submit_order(
            "000001.SZ", OrderDirection.BUY, OrderType.LIMIT, 10.0, 100
        )
        # 已成交的订单不能撤
        if order.status == OrderStatus.FILLED:
            assert adapter.cancel_order(order.broker_order_id) is False

    def test_get_positions(self):
        """测试获取持仓"""
        adapter = PaperBrokerAdapter({})
        adapter.connect()
        adapter.paper_broker.set_market_price("000001.SZ", 10.0)

        adapter.submit_order(
            "000001.SZ", OrderDirection.BUY, OrderType.LIMIT, 10.0, 100
        )

        positions = adapter.get_positions()
        assert "000001.SZ" in positions
        assert positions["000001.SZ"].volume > 0

    def test_get_orders(self):
        """测试获取订单"""
        adapter = PaperBrokerAdapter({})
        adapter.connect()
        adapter.paper_broker.set_market_price("000001.SZ", 10.0)

        adapter.submit_order(
            "000001.SZ", OrderDirection.BUY, OrderType.LIMIT, 10.0, 100
        )

        orders = adapter.get_orders()
        assert len(orders) >= 1

    def test_get_account(self):
        """测试获取账户"""
        adapter = PaperBrokerAdapter({"initial_cash": 500_000.0})
        adapter.connect()

        account = adapter.get_account()
        assert account.total_equity == 500_000.0
        assert account.available_cash == 500_000.0

    def test_subscribe_market_data(self):
        """测试订阅行情"""
        adapter = PaperBrokerAdapter({})
        adapter.connect()
        assert adapter.subscribe_market_data(["000001.SZ"], lambda x: None) is True

    def test_submit_without_connect(self):
        """测试未连接时下单"""
        adapter = PaperBrokerAdapter({})
        with pytest.raises(RuntimeError, match="未连接"):
            adapter.submit_order(
                "000001.SZ", OrderDirection.BUY, OrderType.LIMIT, 10.0, 100
            )


# ============================================================================
# Test QMT Adapter (mocked)
# ============================================================================


class TestQMTAdapter:
    """QMTAdapter 测试（使用 mock）"""

    def _make_mock_xtquant(self):
        """创建 mock 的 xtquant 模块"""
        # 使用真实字典，因为 MagicMock.__getitem__ 不支持 __setitem__ 赋值
        mock_xt: Dict[str, Any] = {}

        # Mock classes
        mock_trader_cls = MagicMock()
        mock_trader_instance = MagicMock()
        mock_trader_cls.return_value = mock_trader_instance
        mock_xt["XtQuantTrader"] = mock_trader_cls

        mock_account_cls = MagicMock()
        mock_account_instance = MagicMock()
        mock_account_cls.return_value = mock_account_instance
        mock_xt["StockAccount"] = mock_account_cls

        mock_xt["XtQuantTraderCallback"] = MagicMock
        mock_xt["XtOrder"] = MagicMock
        mock_xt["XtPosition"] = MagicMock
        mock_xt["XtAsset"] = MagicMock
        mock_xt["XtTrade"] = MagicMock

        return mock_xt, mock_trader_instance, mock_account_instance

    def test_connect_success(self):
        """测试连接成功"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({
                "mini_qmt_path": "/tmp/qmt",
                "account_id": "123456",
            })
            result = adapter.connect()

            assert result is True
            assert adapter.is_connected
            mock_trader.start.assert_called_once()
            mock_trader.subscribe.assert_called_once_with(mock_account)

    def test_connect_import_error(self):
        """测试连接时 xtquant 未安装"""
        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            side_effect=ImportError("xtquant not installed"),
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            result = adapter.connect()
            assert result is False
            assert not adapter.is_connected

    def test_disconnect(self):
        """测试断开连接"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            adapter.connect()
            adapter.disconnect()

            assert not adapter.is_connected
            mock_trader.stop.assert_called_once()

    def test_submit_order(self):
        """测试下单"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()
        mock_trader.order_stock.return_value = 12345

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            adapter.connect()

            order = adapter.submit_order(
                "600519.SH", OrderDirection.BUY, OrderType.LIMIT, 1800.0, 100
            )

            assert order.broker_order_id == "12345"
            assert order.symbol == "600519.SH"
            assert order.direction == OrderDirection.BUY
            assert order.status == OrderStatus.SUBMITTED
            mock_trader.order_stock.assert_called_once()

    def test_submit_order_error(self):
        """测试下单异常"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()
        mock_trader.order_stock.side_effect = RuntimeError("网络错误")

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            adapter.connect()

            order = adapter.submit_order(
                "600519.SH", OrderDirection.BUY, OrderType.LIMIT, 1800.0, 100
            )

            assert order.status == OrderStatus.REJECTED
            assert "网络错误" in order.error_msg

    def test_cancel_order(self):
        """测试撤单"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            adapter.connect()

            result = adapter.cancel_order("12345")
            assert result is True
            mock_trader.cancel_order_stock.assert_called_once_with(
                mock_account, 12345
            )

    def test_get_orders(self):
        """测试查询订单"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()

        # Mock XtOrder 对象
        mock_order = MagicMock()
        mock_order.order_id = 100
        mock_order.stock_code = "600519.SH"
        mock_order.order_type = 23  # BUY
        mock_order.order_status = 56  # SUCCEEDED
        mock_order.price_type = 11  # FIX_PRICE
        mock_order.order_price = 1800.0
        mock_order.order_volume = 100
        mock_order.traded_volume = 100
        mock_order.traded_price = 1800.0
        mock_order.strategy_name = ""
        mock_order.order_remark = ""
        mock_trader.query_stock_orders.return_value = [mock_order]

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            adapter.connect()

            orders = adapter.get_orders()
            assert len(orders) == 1
            assert orders[0].symbol == "600519.SH"
            assert orders[0].status == OrderStatus.FILLED
            assert orders[0].filled_volume == 100

    def test_get_positions(self):
        """测试查询持仓"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()

        mock_pos = MagicMock()
        mock_pos.stock_code = "600519.SH"
        mock_pos.volume = 1000
        mock_pos.can_use_volume = 800
        mock_pos.open_price = 1750.0
        mock_pos.last_price = 1800.0
        mock_pos.market_value = 1_800_000.0
        mock_trader.query_stock_positions.return_value = [mock_pos]

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            adapter.connect()

            positions = adapter.get_positions()
            assert "600519.SH" in positions
            assert positions["600519.SH"].volume == 1000
            assert positions["600519.SH"].pnl > 0

    def test_get_account(self):
        """测试查询账户"""
        mock_xt, mock_trader, mock_account = self._make_mock_xtquant()

        mock_asset = MagicMock()
        mock_asset.total_asset = 2_000_000.0
        mock_asset.cash = 500_000.0
        mock_asset.frozen_cash = 100_000.0
        mock_asset.market_value = 1_400_000.0
        mock_asset.unrealized_pnl = 50_000.0
        mock_asset.realized_pnl = 20_000.0
        mock_asset.margin = 700_000.0
        mock_asset.margin_ratio = 35.0
        mock_trader.query_stock_asset.return_value = mock_asset

        with patch(
            "finhack_pro.execution.broker_qmt._import_xtquant",
            return_value=mock_xt,
        ):
            from finhack_pro.execution.broker_qmt import QMTAdapter

            adapter = QMTAdapter({"account_id": "123456"})
            adapter.connect()

            account = adapter.get_account()
            assert account.total_equity == 2_000_000.0
            assert account.available_cash == 500_000.0
            assert account.unrealized_pnl == 50_000.0

    def test_submit_without_connect(self):
        """测试未连接时下单"""
        from finhack_pro.execution.broker_qmt import QMTAdapter

        adapter = QMTAdapter({"account_id": "123456"})
        with pytest.raises(RuntimeError, match="未连接"):
            adapter.submit_order(
                "600519.SH", OrderDirection.BUY, OrderType.LIMIT, 1800.0, 100
            )

    def test_broker_name_and_features(self):
        """测试适配器名称和特性"""
        from finhack_pro.execution.broker_qmt import QMTAdapter

        adapter = QMTAdapter({"account_id": "123456"})
        assert adapter.broker_name == "qmt"
        assert "order" in adapter.supported_features
        assert "cancel" in adapter.supported_features
        assert "query" in adapter.supported_features
        assert "market_data" in adapter.supported_features
        assert "callback" in adapter.supported_features


# ============================================================================
# Test PTrade Adapter (mocked)
# ============================================================================


class TestPTradeAdapter:
    """PTradeAdapter 测试（使用 mock）"""

    def _make_mock_ptrade(self):
        """创建 mock 的 PTrade API 模块"""
        mock_ptrade = MagicMock()

        # Mock Order 对象
        mock_order = MagicMock()
        mock_order.id = "P001"
        mock_order.security = "600519.SH"
        mock_order.amount = 100
        mock_order.price = 1800.0
        mock_order.filled = 100
        mock_order.status = "filled"
        mock_ptrade.order.return_value = mock_order

        # Mock cancel_order
        mock_ptrade.cancel_order.return_value = True

        # Mock get_orders
        mock_ptrade.get_orders.return_value = {
            "P001": mock_order,
        }

        # Mock Position 对象
        mock_pos = MagicMock()
        mock_pos.security = "600519.SH"
        mock_pos.total_amount = 1000
        mock_pos.closeable_amount = 800
        mock_pos.avg_cost = 1750.0
        mock_pos.price = 1800.0
        mock_ptrade.get_positions.return_value = {
            "600519.SH": mock_pos,
        }

        # Mock account
        mock_account = MagicMock()
        mock_account.total_equity = 2_000_000.0
        mock_account.available_cash = 500_000.0
        mock_account.frozen_cash = 100_000.0
        mock_account.market_value = 1_400_000.0
        mock_account.unrealized_pnl = 50_000.0
        mock_account.realized_pnl = 20_000.0
        mock_ptrade.get_account.return_value = mock_account

        return mock_ptrade

    def test_connect_success(self):
        """测试连接成功"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            result = adapter.connect()

            assert result is True
            assert adapter.is_connected

    def test_connect_import_error(self):
        """测试连接时 PTrade API 不可用"""
        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            side_effect=ImportError("PTrade API not available"),
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            result = adapter.connect()
            assert result is False
            assert not adapter.is_connected

    def test_disconnect(self):
        """测试断开连接"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()
            adapter.disconnect()

            assert not adapter.is_connected

    def test_submit_order_buy(self):
        """测试买入下单"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            order = adapter.submit_order(
                "600519.SH", OrderDirection.BUY, OrderType.LIMIT, 1800.0, 100
            )

            assert order.symbol == "600519.SH"
            assert order.status == OrderStatus.FILLED
            # PTrade buy: amount should be positive
            mock_ptrade.order.assert_called_once_with("600519.SH", 100, 1800.0)

    def test_submit_order_sell(self):
        """测试卖出下单"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            order = adapter.submit_order(
                "600519.SH", OrderDirection.SELL, OrderType.LIMIT, 1800.0, 100
            )

            assert order.symbol == "600519.SH"
            # PTrade sell: amount should be negative
            mock_ptrade.order.assert_called_once_with("600519.SH", -100, 1800.0)

    def test_submit_order_market(self):
        """测试市价单"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            adapter.submit_order(
                "600519.SH", OrderDirection.BUY, OrderType.MARKET, 1800.0, 100
            )

            # Market order: limit_price should be None
            mock_ptrade.order.assert_called_once_with("600519.SH", 100, None)

    def test_submit_order_error(self):
        """测试下单异常"""
        mock_ptrade = self._make_mock_ptrade()
        mock_ptrade.order.side_effect = RuntimeError("PTrade error")

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            order = adapter.submit_order(
                "600519.SH", OrderDirection.BUY, OrderType.LIMIT, 1800.0, 100
            )

            assert order.status == OrderStatus.REJECTED
            assert "PTrade error" in order.error_msg

    def test_cancel_order(self):
        """测试撤单"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            result = adapter.cancel_order("P001")
            assert result is True
            mock_ptrade.cancel_order.assert_called_once_with("P001")

    def test_get_orders(self):
        """测试查询订单"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            orders = adapter.get_orders()
            assert len(orders) == 1
            assert orders[0].symbol == "600519.SH"
            assert orders[0].status == OrderStatus.FILLED

    def test_get_positions(self):
        """测试查询持仓"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            positions = adapter.get_positions()
            assert "600519.SH" in positions
            assert positions["600519.SH"].volume == 1000
            assert positions["600519.SH"].available_volume == 800

    def test_get_account(self):
        """测试查询账户"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            account = adapter.get_account()
            assert account.total_equity == 2_000_000.0
            assert account.available_cash == 500_000.0

    def test_subscribe_market_data(self):
        """测试订阅行情"""
        mock_ptrade = self._make_mock_ptrade()

        with patch(
            "finhack_pro.execution.broker_ptrade._import_ptrade",
            return_value=mock_ptrade,
        ):
            from finhack_pro.execution.broker_ptrade import PTradeAdapter

            adapter = PTradeAdapter({"account_id": "123456"})
            adapter.connect()

            result = adapter.subscribe_market_data(
                ["600519.SH"], lambda x: None
            )
            assert result is True

    def test_submit_without_connect(self):
        """测试未连接时下单"""
        from finhack_pro.execution.broker_ptrade import PTradeAdapter

        adapter = PTradeAdapter({"account_id": "123456"})
        with pytest.raises(RuntimeError, match="未连接"):
            adapter.submit_order(
                "600519.SH", OrderDirection.BUY, OrderType.LIMIT, 1800.0, 100
            )

    def test_broker_name_and_features(self):
        """测试适配器名称和特性"""
        from finhack_pro.execution.broker_ptrade import PTradeAdapter

        adapter = PTradeAdapter({"account_id": "123456"})
        assert adapter.broker_name == "ptrade"
        assert "order" in adapter.supported_features
        assert "cancel" in adapter.supported_features
        assert "query" in adapter.supported_features


# ============================================================================
# Test Adapter Integration
# ============================================================================


class TestAdapterIntegration:
    """适配器集成测试"""

    def test_factory_creates_paper_adapter(self):
        """测试工厂创建 paper 适配器"""
        adapter = BrokerFactory.create("paper")
        assert isinstance(adapter, PaperBrokerAdapter)
        assert adapter.connect() is True

    def test_paper_adapter_full_workflow(self):
        """测试 paper 适配器完整流程"""
        adapter = BrokerFactory.create("paper", {"initial_cash": 1_000_000.0})
        adapter.connect()

        # 设置市场价格
        adapter.paper_broker.set_market_price("000001.SZ", 10.0)

        # 下单
        order = adapter.submit_order(
            "000001.SZ", OrderDirection.BUY, OrderType.LIMIT, 10.0, 100
        )
        assert order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL_FILLED)

        # 查询持仓
        positions = adapter.get_positions()
        assert "000001.SZ" in positions

        # 查询账户
        account = adapter.get_account()
        assert account.total_equity > 0
        assert account.market_value > 0

        # 查询订单
        orders = adapter.get_orders()
        assert len(orders) >= 1

        adapter.disconnect()

    def test_adapter_thread_safety(self):
        """测试适配器线程安全"""
        adapter = PaperBrokerAdapter({})
        adapter.connect()
        adapter.paper_broker.set_market_price("000001.SZ", 10.0)

        results = []
        errors = []

        def submit_order():
            try:
                order = adapter.submit_order(
                    "000001.SZ", OrderDirection.BUY, OrderType.LIMIT, 10.0, 100
                )
                results.append(order)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=submit_order) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(results) == 10
        assert len(errors) == 0

    def test_qmt_status_mapping(self):
        """测试 QMT 状态映射完整性"""
        from finhack_pro.execution.broker_qmt import _XT_STATUS_MAP

        # 确保所有已知状态都有映射
        expected_statuses = [48, 49, 50, 51, 52, 53, 54, 56, 57]
        for status_code in expected_statuses:
            assert status_code in _XT_STATUS_MAP, (
                f"XtQuant status {status_code} not mapped"
            )

    def test_ptrade_status_mapping(self):
        """测试 PTrade 状态映射"""
        from finhack_pro.execution.broker_ptrade import _map_ptrade_status

        assert _map_ptrade_status("filled") == OrderStatus.FILLED
        assert _map_ptrade_status("cancelled") == OrderStatus.CANCELLED
        assert _map_ptrade_status("rejected") == OrderStatus.REJECTED
        assert _map_ptrade_status("pending") == OrderStatus.PENDING
        assert _map_ptrade_status("unknown_status") == OrderStatus.UNKNOWN

    def test_qmt_direction_mapping(self):
        """测试 QMT 方向映射"""
        from finhack_pro.execution.broker_qmt import (
            _DIRECTION_TO_XT,
            _XT_DIRECTION_MAP,
            XT_STOCK_BUY,
            XT_STOCK_SELL,
        )

        assert _DIRECTION_TO_XT[OrderDirection.BUY] == XT_STOCK_BUY
        assert _DIRECTION_TO_XT[OrderDirection.SELL] == XT_STOCK_SELL
        assert _XT_DIRECTION_MAP[XT_STOCK_BUY] == OrderDirection.BUY
        assert _XT_DIRECTION_MAP[XT_STOCK_SELL] == OrderDirection.SELL
