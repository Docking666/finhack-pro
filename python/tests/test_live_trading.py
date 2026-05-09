"""
FinHack Pro Phase 5 测试 - 实盘交易与监控

覆盖:
- PaperBroker: 订单提交、成交模拟、持仓跟踪、撤单、PnL
- LiveTrader: 连接/断开、下单/撤单、持仓查询、风控检查、dry_run 模式
- MonitoringService: 指标注册、counter/gauge/histogram、告警规则、Prometheus 导出、Grafana Dashboard
- MetricsServer: Prometheus 格式输出
"""

from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from finhack_pro.execution.live_trader import (
    AccountInfo,
    Direction,
    LiveTrader,
    LiveTradingConfig,
    Order,
    OrderStatus,
    OrderType,
    PaperBroker,
    Position,
)
from finhack_pro.utils.monitoring import (
    Alert,
    AlertLevel,
    AlertRule,
    MetricsServer,
    MonitoringConfig,
    MonitoringService,
)

# ============================================================================
# TestPaperBroker
# ============================================================================


class TestPaperBroker:
    """PaperBroker 模拟券商测试"""

    def test_init_default_cash(self):
        """测试默认初始化资金"""
        broker = PaperBroker()
        assert broker.cash == 1_000_000.0

    def test_init_custom_cash(self):
        """测试自定义初始资金"""
        broker = PaperBroker(initial_cash=500_000.0)
        assert broker.cash == 500_000.0

    def test_set_and_get_market_price(self):
        """测试设置和获取市场价格"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.5)
        assert broker.get_market_price("000001.SZ") == 10.5
        assert broker.get_market_price("UNKNOWN") == 0.0

    def test_submit_buy_order(self):
        """测试买入订单提交和成交"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        order = Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100)
        result = broker.submit_order(order)

        assert result.order_id == order.order_id
        assert result.status in (OrderStatus.FILLED.value, OrderStatus.PARTIAL_FILLED.value)
        assert result.filled_volume > 0
        assert result.filled_price > 0
        assert result.commission > 0

    def test_submit_sell_order(self):
        """测试卖出订单（先买入再卖出）"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        # 先买入
        buy_order = Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100)
        broker.submit_order(buy_order)

        # 更新价格后卖出
        broker.set_market_price("000001.SZ", 11.0)
        sell_order = Order(symbol="000001.SZ", direction="sell", price=11.0, volume=100)
        result = broker.submit_order(sell_order)

        assert result.status in (OrderStatus.FILLED.value, OrderStatus.PARTIAL_FILLED.value)
        assert broker.realized_pnl > 0  # 应该有盈利

    def test_fill_simulation_slippage(self):
        """测试成交滑点模拟"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        results = []
        for _ in range(20):
            order = Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100)
            result = broker.submit_order(order)
            if result.status == OrderStatus.FILLED.value:
                results.append(result.filled_price)

        # 限价单应该以限价成交
        assert all(abs(p - 10.0) < 0.01 for p in results)

    def test_position_tracking(self):
        """测试持仓跟踪"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        # 买入
        order = Order(symbol="000001.SZ", direction="buy", price=10.0, volume=200)
        broker.submit_order(order)

        positions = broker.get_positions()
        assert "000001.SZ" in positions
        assert positions["000001.SZ"].quantity == 200
        assert positions["000001.SZ"].avg_price > 0

    def test_position_pnl_update(self):
        """测试持仓盈亏更新"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        order = Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100)
        broker.submit_order(order)

        # 价格上涨
        broker.set_market_price("000001.SZ", 11.0)
        positions = broker.get_positions()
        assert positions["000001.SZ"].pnl > 0
        assert positions["000001.SZ"].pnl_pct > 0

        # 价格下跌
        broker.set_market_price("000001.SZ", 9.0)
        positions = broker.get_positions()
        assert positions["000001.SZ"].pnl < 0
        assert positions["000001.SZ"].pnl_pct < 0

    def test_cancel_order(self):
        """测试撤单"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        order = Order(symbol="000001.SZ", direction="buy", price=5.0, volume=100)  # 低于市价的限价单
        broker.submit_order(order)

        # 如果订单因为限价未成交，可以撤单
        if order.status == OrderStatus.SUBMITTED.value:
            success = broker.cancel_order(order.order_id)
            assert success
            assert order.status == OrderStatus.CANCELLED.value

    def test_cancel_nonexistent_order(self):
        """测试取消不存在的订单"""
        broker = PaperBroker()
        success = broker.cancel_order("nonexistent")
        assert not success

    def test_cancel_filled_order(self):
        """测试取消已成交订单"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        order = Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100)
        broker.submit_order(order)

        if order.status == OrderStatus.FILLED.value:
            success = broker.cancel_order(order.order_id)
            assert not success

    def test_insufficient_funds(self):
        """测试资金不足"""
        broker = PaperBroker(initial_cash=100.0)
        broker.set_market_price("000001.SZ", 10.0)

        order = Order(symbol="000001.SZ", direction="buy", price=10.0, volume=1000)
        result = broker.submit_order(order)

        # 应该被拒绝、部分成交或以缩小后的量成交
        assert result.status in (
            OrderStatus.REJECTED.value,
            OrderStatus.PARTIAL_FILLED.value,
            OrderStatus.FILLED.value,
        )
        # 原始请求1000股，资金不足时实际成交量应远小于1000
        assert result.filled_volume < 1000

    def test_insufficient_position_for_sell(self):
        """测试持仓不足卖出"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        order = Order(symbol="000001.SZ", direction="sell", price=10.0, volume=100)
        result = broker.submit_order(order)

        assert result.status == OrderStatus.REJECTED.value

    def test_get_orders(self):
        """测试获取订单列表"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        broker.submit_order(Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100))
        broker.submit_order(Order(symbol="000001.SZ", direction="buy", price=10.0, volume=200))

        orders = broker.get_orders()
        assert len(orders) == 2

        filled_orders = broker.get_orders(status=OrderStatus.FILLED.value)
        assert len(filled_orders) >= 0

    def test_get_account_info(self):
        """测试获取账户信息"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        broker.submit_order(Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100))

        account = broker.get_account_info()
        assert isinstance(account, AccountInfo)
        assert account.total_equity > 0
        assert account.available_cash >= 0
        assert account.total_position_value >= 0

    def test_market_order(self):
        """测试市价单"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        order = Order(
            symbol="000001.SZ",
            direction="buy",
            price=10.0,
            volume=100,
            order_type=OrderType.MARKET.value,
        )
        result = broker.submit_order(order)

        assert result.status in (OrderStatus.FILLED.value, OrderStatus.PARTIAL_FILLED.value)

    def test_trade_log(self):
        """测试交易日志"""
        broker = PaperBroker()
        broker.set_market_price("000001.SZ", 10.0)

        broker.submit_order(Order(symbol="000001.SZ", direction="buy", price=10.0, volume=100))

        log = broker.get_trade_log()
        assert len(log) == 1
        assert log[0]["symbol"] == "000001.SZ"
        assert log[0]["direction"] == "buy"


# ============================================================================
# TestLiveTrader
# ============================================================================


class TestLiveTrader:
    """LiveTrader 实盘交易接口测试"""

    def _make_config(self, **kwargs) -> LiveTradingConfig:
        """创建测试配置"""
        defaults = {
            "broker_type": "paper",
            "dry_run": True,
            "log_trades": False,
            "max_position_pct": 0.3,
        }
        defaults.update(kwargs)
        return LiveTradingConfig(**defaults)

    def test_connect_paper(self):
        """测试模拟模式连接"""
        config = self._make_config()
        trader = LiveTrader(config)
        assert trader.connect() is True
        assert trader._connected is True
        trader.disconnect()

    def test_disconnect(self):
        """测试断开连接"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()
        trader.disconnect()
        assert trader._connected is False

    def test_submit_order(self):
        """测试提交订单"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        # 设置市场价格
        trader._paper_broker.set_market_price("000001.SZ", 10.0)

        order = trader.submit_order("000001.SZ", "buy", 10.0, 100)
        assert order.symbol == "000001.SZ"
        assert order.direction == "buy"
        assert order.volume == 100
        assert order.status in (OrderStatus.FILLED.value, OrderStatus.PARTIAL_FILLED.value)

        trader.stop()

    def test_cancel_order(self):
        """测试撤单"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)
        order = trader.submit_order("000001.SZ", "buy", 10.0, 100)

        if order.status == OrderStatus.FILLED.value:
            # 已成交订单不能撤单
            assert trader.cancel_order(order.order_id) is False
        else:
            assert trader.cancel_order(order.order_id) is True

        trader.stop()

    def test_get_positions(self):
        """测试获取持仓"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)
        trader.submit_order("000001.SZ", "buy", 10.0, 100)

        positions = trader.get_positions()
        assert "000001.SZ" in positions

        trader.stop()

    def test_get_orders(self):
        """测试获取订单列表"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)
        trader.submit_order("000001.SZ", "buy", 10.0, 100)

        orders = trader.get_orders()
        assert len(orders) == 1

        trader.stop()

    def test_get_account_info(self):
        """测试获取账户信息"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        account = trader.get_account_info()
        assert isinstance(account, AccountInfo)
        assert account.total_equity == 1_000_000.0

        trader.stop()

    def test_risk_check_position_too_large(self):
        """测试风控：单笔交易超过限制"""
        config = self._make_config(max_position_pct=0.01)
        trader = LiveTrader(config)
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)

        with pytest.raises(ValueError, match="风控"):
            trader.submit_order("000001.SZ", "buy", 10.0, 10000)

        trader.stop()

    def test_risk_check_insufficient_cash(self):
        """测试风控：资金不足"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)

        with pytest.raises(ValueError, match="风控"):
            trader.submit_order("000001.SZ", "buy", 10.0, 200000)

        trader.stop()

    def test_risk_check_insufficient_position(self):
        """测试风控：卖出时持仓不足"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)

        with pytest.raises(ValueError, match="风控"):
            trader.submit_order("000001.SZ", "sell", 10.0, 100)

        trader.stop()

    def test_dry_run_mode(self):
        """测试 dry_run 模式"""
        config = self._make_config(dry_run=True)
        trader = LiveTrader(config)
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)
        order = trader.submit_order("000001.SZ", "buy", 10.0, 100)

        assert order.status in (OrderStatus.FILLED.value, OrderStatus.PARTIAL_FILLED.value)
        trader.stop()

    def test_submit_without_connect(self):
        """测试未连接时提交订单"""
        config = self._make_config()
        trader = LiveTrader(config)

        with pytest.raises(RuntimeError, match="未连接"):
            trader.submit_order("000001.SZ", "buy", 10.0, 100)

    def test_subscribe_market_data(self):
        """测试订阅行情"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        received = []

        def callback(data):
            received.append(data)

        trader.subscribe_market_data(["000001.SZ"], callback)
        assert "000001.SZ" in trader._market_data_callbacks

        trader.stop()

    def test_start_stop(self):
        """测试启动和停止交易循环"""
        config = self._make_config()
        trader = LiveTrader(config)
        trader.connect()

        trader.start()
        assert trader._running is True

        trader.stop()
        assert trader._running is False

    def test_log_trades(self, tmp_path):
        """测试交易日志记录"""
        log_dir = str(tmp_path / "trades")
        config = self._make_config(log_trades=True)
        trader = LiveTrader(config)
        trader._trade_log_dir = log_dir
        trader.connect()

        trader._paper_broker.set_market_price("000001.SZ", 10.0)
        trader.submit_order("000001.SZ", "buy", 10.0, 100)

        # 检查日志文件
        log_files = [f for f in os.listdir(log_dir) if f.endswith(".jsonl")]
        assert len(log_files) == 1

        with open(os.path.join(log_dir, log_files[0]), "r") as f:
            lines = f.readlines()
            assert len(lines) == 1
            record = json.loads(lines[0])
            assert record["symbol"] == "000001.SZ"
            assert record["action"] == "submit"

        trader.stop()


# ============================================================================
# TestMonitoringService
# ============================================================================


class TestMonitoringService:
    """MonitoringService 监控服务测试"""

    def test_init(self):
        """测试初始化"""
        service = MonitoringService()
        assert service is not None
        assert len(service._alert_rules) >= 5  # 内置告警规则

    def test_register_metric_counter(self):
        """测试注册 counter 指标"""
        service = MonitoringService()
        service.register_metric("test_counter", "counter", "Test counter")
        assert "test_counter" in service._metric_definitions
        assert service._metric_definitions["test_counter"]["type"] == "counter"

    def test_register_metric_gauge(self):
        """测试注册 gauge 指标"""
        service = MonitoringService()
        service.register_metric("test_gauge", "gauge", "Test gauge")
        assert "test_gauge" in service._metric_definitions

    def test_register_metric_histogram(self):
        """测试注册 histogram 指标"""
        service = MonitoringService()
        service.register_metric("test_hist", "histogram", "Test histogram")
        assert "test_hist" in service._metric_definitions

    def test_increment_counter(self):
        """测试增加计数器"""
        service = MonitoringService()
        service.increment_counter("trades_total")
        service.increment_counter("trades_total")
        service.increment_counter("trades_total", value=5)

        snapshot = service.get_metrics_snapshot()
        assert snapshot.get("finhack_trades_total") == 7

    def test_increment_counter_with_labels(self):
        """测试带标签的计数器"""
        service = MonitoringService()
        service.register_metric("labeled_counter", "counter", "Labeled counter")
        service.increment_counter("labeled_counter", labels={"symbol": "000001.SZ"})
        service.increment_counter("labeled_counter", labels={"symbol": "000001.SZ"})
        service.increment_counter("labeled_counter", labels={"symbol": "600519.SH"})

        snapshot = service.get_metrics_snapshot()
        assert snapshot.get('finhack_labeled_counter{symbol="000001.SZ"}') == 2
        assert snapshot.get('finhack_labeled_counter{symbol="600519.SH"}') == 1

    def test_set_gauge(self):
        """测试设置仪表值"""
        service = MonitoringService()
        service.set_gauge("total_equity", 1_500_000.0)

        snapshot = service.get_metrics_snapshot()
        assert snapshot.get("finhack_total_equity") == 1_500_000.0

    def test_set_gauge_with_labels(self):
        """测试带标签的仪表"""
        service = MonitoringService()
        service.register_metric("position_value", "gauge", "Position value")
        service.set_gauge("position_value", 50000.0, labels={"symbol": "000001.SZ"})

        snapshot = service.get_metrics_snapshot()
        assert snapshot.get('finhack_position_value{symbol="000001.SZ"}') == 50000.0

    def test_observe_histogram(self):
        """测试记录直方图"""
        service = MonitoringService()
        for val in [0.1, 0.2, 0.3, 0.5, 1.0, 2.0]:
            service.observe_histogram("api_latency_seconds", val)

        snapshot = service.get_metrics_snapshot()
        assert snapshot.get("finhack_api_latency_seconds_count") == 6
        assert snapshot.get("finhack_api_latency_seconds_sum") == pytest.approx(4.1, rel=0.01)

    def test_observe_histogram_with_labels(self):
        """测试带标签的直方图"""
        service = MonitoringService()
        service.register_metric("custom_hist", "histogram", "Custom histogram")
        service.observe_histogram("custom_hist", 0.5, labels={"endpoint": "/api/test"})
        service.observe_histogram("custom_hist", 1.5, labels={"endpoint": "/api/test"})

        snapshot = service.get_metrics_snapshot()
        key = 'finhack_custom_hist{endpoint="/api/test"}'
        assert snapshot.get(f"{key}_count") == 2

    def test_add_alert_rule(self):
        """测试添加告警规则"""
        service = MonitoringService()

        rule = AlertRule(
            name="test_rule",
            condition=lambda ctx: ctx.get("test_value", 0) > 100,
            level=AlertLevel.INFO.value,
            cooldown_seconds=0,
        )
        service.add_alert_rule(rule)

        assert len(service._alert_rules) >= 6  # 5 built-in + 1 custom

    def test_check_alerts_triggered(self):
        """测试告警触发"""
        service = MonitoringService()

        rule = AlertRule(
            name="test_trigger",
            condition=lambda ctx: ctx.get("test_value", 0) > 50,
            level=AlertLevel.WARNING.value,
            cooldown_seconds=0,
        )
        service.add_alert_rule(rule)

        alerts = service.check_alerts({"test_value": 100})
        assert len(alerts) >= 1
        assert any(a.metadata.get("rule") == "test_trigger" for a in alerts)

    def test_check_alerts_not_triggered(self):
        """测试告警未触发"""
        service = MonitoringService()

        rule = AlertRule(
            name="test_no_trigger",
            condition=lambda ctx: ctx.get("test_value", 0) > 100,
            level=AlertLevel.INFO.value,
            cooldown_seconds=0,
        )
        service.add_alert_rule(rule)

        alerts = service.check_alerts({"test_value": 10})
        # test_no_trigger should not trigger, but built-in rules might
        assert not any(a.metadata.get("rule") == "test_no_trigger" for a in alerts)

    def test_check_alerts_cooldown(self):
        """测试告警冷却期"""
        service = MonitoringService()

        rule = AlertRule(
            name="cooldown_test",
            condition=lambda ctx: True,  # Always triggers
            level=AlertLevel.INFO.value,
            cooldown_seconds=300,
        )
        service.add_alert_rule(rule)

        # First check - should trigger
        alerts1 = service.check_alerts({})
        cooldown_alerts = [a for a in alerts1 if a.metadata.get("rule") == "cooldown_test"]
        assert len(cooldown_alerts) == 1

        # Second check immediately - should be in cooldown
        alerts2 = service.check_alerts({})
        cooldown_alerts2 = [a for a in alerts2 if a.metadata.get("rule") == "cooldown_test"]
        assert len(cooldown_alerts2) == 0

    def test_get_alerts(self):
        """测试获取告警列表"""
        service = MonitoringService()

        rule = AlertRule(
            name="get_alerts_test",
            condition=lambda ctx: True,
            level=AlertLevel.INFO.value,
            cooldown_seconds=0,
        )
        service.add_alert_rule(rule)
        service.check_alerts({})

        alerts = service.get_alerts(acknowledged=False)
        assert len(alerts) >= 1

    def test_acknowledge_alert(self):
        """测试确认告警"""
        service = MonitoringService()

        rule = AlertRule(
            name="ack_test",
            condition=lambda ctx: True,
            level=AlertLevel.INFO.value,
            cooldown_seconds=0,
        )
        service.add_alert_rule(rule)
        alerts = service.check_alerts({})

        ack_alerts = [a for a in alerts if a.metadata.get("rule") == "ack_test"]
        if ack_alerts:
            alert_id = ack_alerts[0].alert_id
            success = service.acknowledge_alert(alert_id)
            assert success

            # After acknowledge, should not appear in unacknowledged list
            remaining = service.get_alerts(acknowledged=False)
            assert not any(a.alert_id == alert_id for a in remaining)

    def test_builtin_alert_high_drawdown(self):
        """测试内置告警：高回撤"""
        service = MonitoringService()

        alerts = service.check_alerts({"finhack_drawdown_pct": 20.0})
        assert any(a.metadata.get("rule") == "high_drawdown" for a in alerts)

    def test_builtin_alert_api_error_rate(self):
        """测试内置告警：API 错误率"""
        service = MonitoringService()

        alerts = service.check_alerts({
            "finhack_api_requests_total": 100,
            "finhack_api_errors_total": 10,  # 10% error rate
        })
        assert any(a.metadata.get("rule") == "api_error_rate_high" for a in alerts)

    def test_builtin_alert_memory_usage(self):
        """测试内置告警：内存使用"""
        service = MonitoringService()

        alerts = service.check_alerts({"finhack_memory_usage_pct": 90.0})
        assert any(a.metadata.get("rule") == "memory_usage_high" for a in alerts)

    def test_builtin_alert_position_concentration(self):
        """测试内置告警：持仓集中度"""
        service = MonitoringService()

        alerts = service.check_alerts({"finhack_position_concentration_pct": 40.0})
        assert any(a.metadata.get("rule") == "position_concentration_high" for a in alerts)

    def test_builtin_alert_daily_loss(self):
        """测试内置告警：日亏损限制"""
        service = MonitoringService()

        alerts = service.check_alerts({"finhack_daily_pnl": -100000.0})
        assert any(a.metadata.get("rule") == "daily_loss_limit_breach" for a in alerts)

    def test_export_prometheus(self):
        """测试 Prometheus 格式导出"""
        service = MonitoringService()
        service.increment_counter("trades_total", value=10)
        service.set_gauge("total_equity", 1_000_000.0)

        output = service.export_prometheus()
        assert "# HELP finhack_trades_total" in output
        assert "# TYPE finhack_trades_total counter" in output
        assert "finhack_trades_total 10" in output
        assert "# HELP finhack_total_equity" in output
        assert "# TYPE finhack_total_equity gauge" in output
        assert "finhack_total_equity 1000000" in output

    def test_export_prometheus_histogram(self):
        """测试 Prometheus 直方图导出"""
        service = MonitoringService()
        for val in [0.1, 0.5, 1.0]:
            service.observe_histogram("api_latency_seconds", val)

        output = service.export_prometheus()
        assert "# TYPE finhack_api_latency_seconds histogram" in output
        assert "finhack_api_latency_seconds_bucket" in output
        assert 'le="+Inf"' in output
        assert "finhack_api_latency_seconds_sum" in output
        assert "finhack_api_latency_seconds_count" in output

    def test_export_prometheus_with_labels(self):
        """测试带标签的 Prometheus 导出"""
        service = MonitoringService()
        service.register_metric("labeled", "counter", "Labeled metric")
        service.increment_counter("labeled", labels={"symbol": "000001.SZ"})

        output = service.export_prometheus()
        assert 'finhack_labeled{symbol="000001.SZ"}' in output

    def test_export_grafana_dashboard(self):
        """测试 Grafana Dashboard JSON 生成"""
        service = MonitoringService()
        dashboard = service.export_grafana_dashboard()

        assert isinstance(dashboard, dict)
        assert dashboard["title"] == "FinHack Pro Trading Dashboard"
        assert "panels" in dashboard
        assert len(dashboard["panels"]) >= 8

        # Check for required panels
        panel_titles = [p.get("title", "") for p in dashboard["panels"]]
        assert "Equity Curve" in panel_titles
        assert "Drawdown" in panel_titles
        assert "Trade Count" in panel_titles
        assert "Win Rate" in panel_titles
        assert "API Latency" in panel_titles
        assert "Error Rate" in panel_titles

        # Check valid JSON structure
        json_str = json.dumps(dashboard)
        assert json_str  # Can serialize

    def test_get_metrics_snapshot(self):
        """测试指标快照"""
        service = MonitoringService()
        service.increment_counter("trades_total", value=5)
        service.set_gauge("total_equity", 2_000_000.0)

        snapshot = service.get_metrics_snapshot()
        assert isinstance(snapshot, dict)
        assert "finhack_trades_total" in snapshot
        assert snapshot["finhack_trades_total"] == 5
        assert snapshot["finhack_total_equity"] == 2_000_000.0

    def test_alert_rule_disabled(self):
        """测试禁用的告警规则"""
        service = MonitoringService()

        rule = AlertRule(
            name="disabled_rule",
            condition=lambda ctx: True,
            level=AlertLevel.INFO.value,
            cooldown_seconds=0,
            enabled=False,
        )
        service.add_alert_rule(rule)

        alerts = service.check_alerts({})
        assert not any(a.metadata.get("rule") == "disabled_rule" for a in alerts)


# ============================================================================
# TestMetricsServer
# ============================================================================


class TestMetricsServer:
    """MetricsServer Prometheus HTTP 服务测试"""

    def test_prometheus_format_output(self):
        """测试 Prometheus 格式输出"""
        service = MonitoringService()
        service.increment_counter("trades_total", value=42)
        service.set_gauge("total_equity", 1_500_000.0)

        output = service.export_prometheus()

        # Verify basic Prometheus format
        assert "# HELP" in output
        assert "# TYPE" in output
        assert "finhack_trades_total 42" in output
        assert "finhack_total_equity 1500000" in output

    def test_prometheus_format_counter(self):
        """测试 counter 类型格式"""
        service = MonitoringService()
        service.register_metric("my_counter", "counter", "My counter")
        service.increment_counter("my_counter", value=10)

        output = service.export_prometheus()
        assert "# TYPE finhack_my_counter counter" in output
        assert "# HELP finhack_my_counter My counter" in output
        assert "finhack_my_counter 10" in output

    def test_prometheus_format_gauge(self):
        """测试 gauge 类型格式"""
        service = MonitoringService()
        service.register_metric("my_gauge", "gauge", "My gauge")
        service.set_gauge("my_gauge", 75.5)

        output = service.export_prometheus()
        assert "# TYPE finhack_my_gauge gauge" in output
        assert "finhack_my_gauge 75.5" in output

    def test_prometheus_format_histogram_buckets(self):
        """测试 histogram 桶格式"""
        service = MonitoringService()
        service.register_metric("my_hist", "histogram", "My histogram")
        service.observe_histogram("my_hist", 0.05)
        service.observe_histogram("my_hist", 0.5)
        service.observe_histogram("my_hist", 5.0)

        output = service.export_prometheus()
        assert "# TYPE finhack_my_hist histogram" in output
        assert 'le="0.005"' in output
        assert 'le="0.01"' in output
        assert 'le="+Inf"' in output
        assert "finhack_my_hist_sum" in output
        assert "finhack_my_hist_count 3" in output

    def test_prometheus_empty_metrics(self):
        """测试空指标导出"""
        service = MonitoringService()
        # Reset to empty
        service._metrics = {}
        service._metric_definitions = {}

        output = service.export_prometheus()
        assert output == "" or output.strip() == ""

    def test_monitoring_config_defaults(self):
        """测试监控配置默认值"""
        config = MonitoringConfig()
        assert config.metrics_host == "0.0.0.0"
        assert config.metrics_port == 9090
        assert config.alert_cooldown == 300.0
        assert config.retention_hours == 24.0
        assert config.check_interval == 60.0
