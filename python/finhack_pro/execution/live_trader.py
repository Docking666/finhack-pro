"""
FinHack Pro 实盘交易模块

提供实盘/模拟交易接口，包含订单管理、持仓跟踪、风控检查等功能。
所有真实券商交互必须在 dry_run=True 模式下运行，防止误操作。

Usage:
    from finhack_pro.execution.live_trader import LiveTrader, LiveTradingConfig

    config = LiveTradingConfig(broker_type="paper", dry_run=True)
    trader = LiveTrader(config)
    trader.connect()
    order = trader.submit_order("000001.SZ", "buy", 10.5, 100)
    trader.stop()
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================


class OrderStatus(str, Enum):
    """订单状态"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderType(str, Enum):
    """订单类型"""
    MARKET = "market"
    LIMIT = "limit"


class Direction(str, Enum):
    """交易方向"""
    BUY = "buy"
    SELL = "sell"


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class LiveTradingConfig:
    """实盘交易配置"""
    broker_type: str = "paper"  # paper / real
    api_url: str = "http://localhost:8080"
    ws_url: str = "ws://localhost:8081"
    api_key: str = ""
    secret_key: str = ""
    symbols: List[str] = field(default_factory=lambda: ["000001.SZ"])
    max_position_pct: float = 0.3
    dry_run: bool = True  # 安全开关：True 时只使用模拟交易
    log_trades: bool = True
    slippage_bps: float = 1.0  # 滑点(基点)
    commission_rate: float = 0.0003  # 佣金费率


@dataclass
class Order:
    """订单"""
    order_id: str = ""
    symbol: str = ""
    direction: str = "buy"
    order_type: str = "limit"
    price: float = 0.0
    volume: int = 0
    filled_volume: int = 0
    filled_price: float = 0.0
    status: str = OrderStatus.PENDING.value
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    commission: float = 0.0

    def __post_init__(self) -> None:
        if not self.order_id:
            self.order_id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at


@dataclass
class Position:
    """持仓"""
    symbol: str = ""
    quantity: int = 0
    avg_price: float = 0.0
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    market_value: float = 0.0

    def update_price(self, price: float) -> None:
        """更新当前价格并重算盈亏"""
        self.current_price = price
        if self.quantity > 0 and self.avg_price > 0:
            self.market_value = self.quantity * price
            self.pnl = self.market_value - self.quantity * self.avg_price
            self.pnl_pct = (price - self.avg_price) / self.avg_price * 100


@dataclass
class AccountInfo:
    """账户信息"""
    total_equity: float = 0.0
    available_cash: float = 0.0
    total_position_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    margin_used: float = 0.0
    margin_ratio: float = 0.0


# ============================================================================
# PaperBroker - 模拟券商
# ============================================================================


class PaperBroker:
    """模拟券商

    完全在内存中运行，支持订单簿管理、持仓跟踪、PnL 计算。
    模拟滑点和部分成交，无需任何外部依赖。

    Usage:
        broker = PaperBroker(initial_cash=1_000_000)
        broker.set_market_price("000001.SZ", 10.5)
        order = broker.submit_order("000001.SZ", "buy", 10.5, 100)
    """

    def __init__(self, initial_cash: float = 1_000_000.0) -> None:
        self._cash = initial_cash
        self._initial_cash = initial_cash
        self._orders: Dict[str, Order] = {}
        self._positions: Dict[str, Position] = {}
        self._market_prices: Dict[str, float] = {}
        self._realized_pnl = 0.0
        self._trade_log: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    def set_market_price(self, symbol: str, price: float) -> None:
        """设置市场价格"""
        with self._lock:
            self._market_prices[symbol] = price
            if symbol in self._positions:
                self._positions[symbol].update_price(price)

    def get_market_price(self, symbol: str) -> float:
        """获取市场价格"""
        return self._market_prices.get(symbol, 0.0)

    def submit_order(self, order: Order) -> Order:
        """提交订单到模拟券商

        Args:
            order: 订单对象

        Returns:
            更新后的订单对象
        """
        with self._lock:
            order.status = OrderStatus.SUBMITTED.value
            order.updated_at = datetime.now().isoformat()
            self._orders[order.order_id] = order

            # 模拟成交
            self._simulate_fill(order)

        return order

    def _simulate_fill(self, order: Order) -> None:
        """模拟订单成交

        支持滑点和部分成交模拟。
        """
        market_price = self._market_prices.get(order.symbol, 0.0)
        if market_price <= 0:
            order.status = OrderStatus.REJECTED.value
            order.updated_at = datetime.now().isoformat()
            return

        # 检查资金/持仓是否足够
        if order.direction == Direction.BUY.value:
            estimated_cost = market_price * order.volume * 1.001  # 含滑点
            if estimated_cost > self._cash:
                # 部分成交
                max_volume = int(self._cash / (market_price * 1.001))
                if max_volume <= 0:
                    order.status = OrderStatus.REJECTED.value
                    order.updated_at = datetime.now().isoformat()
                    return
                order.volume = max_volume

        elif order.direction == Direction.SELL.value:
            position = self._positions.get(order.symbol)
            if position is None or position.quantity < order.volume:
                available = position.quantity if position else 0
                if available <= 0:
                    order.status = OrderStatus.REJECTED.value
                    order.updated_at = datetime.now().isoformat()
                    return
                order.volume = available

        # 模拟滑点
        slippage_factor = 1.0 + random.uniform(-0.001, 0.001)  # +/- 0.1%
        if order.direction == Direction.BUY.value:
            fill_price = market_price * slippage_factor
        else:
            fill_price = market_price * (2 - slippage_factor)

        # 限价单检查：只有当市场价严重偏离限价时才拒绝
        if order.order_type == OrderType.LIMIT.value:
            if order.direction == Direction.BUY.value and market_price > order.price * 1.001:
                # 买入限价单：市场价远高于限价则不成交
                order.status = OrderStatus.SUBMITTED.value
                order.updated_at = datetime.now().isoformat()
                return
            if order.direction == Direction.SELL.value and market_price < order.price * 0.999:
                # 卖出限价单：市场价远低于限价则不成交
                order.status = OrderStatus.SUBMITTED.value
                order.updated_at = datetime.now().isoformat()
                return
            # 限价单以限价成交
            fill_price = order.price

        # 部分成交模拟 (10% 概率)
        fill_volume = order.volume
        if random.random() < 0.1:
            fill_volume = max(1, int(order.volume * random.uniform(0.5, 0.9)))

        # 计算佣金
        commission = fill_price * fill_volume * 0.0003
        order.commission = round(commission, 4)

        # 更新订单
        order.filled_volume = fill_volume
        order.filled_price = round(fill_price, 4)
        order.status = (
            OrderStatus.PARTIAL_FILLED.value
            if fill_volume < order.volume
            else OrderStatus.FILLED.value
        )
        order.updated_at = datetime.now().isoformat()

        # 更新持仓
        self._update_position(order)

        # 更新资金
        trade_value = fill_price * fill_volume
        if order.direction == Direction.BUY.value:
            self._cash -= trade_value + commission
        else:
            self._cash += trade_value - commission

        # 记录交易
        self._trade_log.append({
            "order_id": order.order_id,
            "symbol": order.symbol,
            "direction": order.direction,
            "price": fill_price,
            "volume": fill_volume,
            "commission": commission,
            "timestamp": order.updated_at,
        })

    def _update_position(self, order: Order) -> None:
        """更新持仓"""
        symbol = order.symbol
        fill_volume = order.filled_volume
        fill_price = order.filled_price

        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)

        position = self._positions[symbol]

        if order.direction == Direction.BUY.value:
            total_cost = position.avg_price * position.quantity + fill_price * fill_volume
            total_volume = position.quantity + fill_volume
            if total_volume > 0:
                position.avg_price = round(total_cost / total_volume, 4)
            position.quantity += fill_volume
        else:
            # 卖出时计算已实现盈亏
            if position.quantity > 0:
                trade_pnl = (fill_price - position.avg_price) * fill_volume
                self._realized_pnl += trade_pnl
            position.quantity -= fill_volume
            if position.quantity <= 0:
                position.quantity = 0
                position.avg_price = 0.0

        position.update_price(self._market_prices.get(symbol, fill_price))

    def cancel_order(self, order_id: str) -> bool:
        """取消订单

        Args:
            order_id: 订单 ID

        Returns:
            是否取消成功
        """
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                return False
            if order.status in (
                OrderStatus.FILLED.value,
                OrderStatus.CANCELLED.value,
                OrderStatus.REJECTED.value,
            ):
                return False
            order.status = OrderStatus.CANCELLED.value
            order.updated_at = datetime.now().isoformat()
            return True

    def get_positions(self) -> Dict[str, Position]:
        """获取所有持仓"""
        with self._lock:
            return {sym: pos for sym, pos in self._positions.items() if pos.quantity > 0}

    def get_orders(self, status: Optional[str] = None) -> List[Order]:
        """获取订单列表

        Args:
            status: 过滤状态，为空返回全部

        Returns:
            订单列表
        """
        with self._lock:
            orders = list(self._orders.values())
            if status:
                orders = [o for o in orders if o.status == status]
            return orders

    def get_account_info(self) -> AccountInfo:
        """获取账户信息"""
        with self._lock:
            positions = self.get_positions()
            total_position_value = sum(p.market_value for p in positions.values())
            unrealized_pnl = sum(p.pnl for p in positions.values())
            total_equity = self._cash + total_position_value

            return AccountInfo(
                total_equity=round(total_equity, 2),
                available_cash=round(self._cash, 2),
                total_position_value=round(total_position_value, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                realized_pnl=round(self._realized_pnl, 2),
                margin_used=round(total_position_value * 0.5, 2),
                margin_ratio=round(total_position_value / total_equity * 100, 2) if total_equity > 0 else 0.0,
            )

    def get_trade_log(self) -> List[Dict[str, Any]]:
        """获取交易日志"""
        return list(self._trade_log)


# ============================================================================
# LiveTrader - 实盘交易接口
# ============================================================================


class LiveTrader:
    """实盘交易接口

    提供统一的交易接口，通过配置切换 paper/real 模式。
    所有真实券商调用都在 dry_run=True 保护下。

    Usage:
        config = LiveTradingConfig(broker_type="paper", dry_run=True)
        trader = LiveTrader(config)
        trader.connect()
        order = trader.submit_order("000001.SZ", "buy", 10.5, 100)
        trader.stop()
    """

    def __init__(self, config: LiveTradingConfig) -> None:
        self.config = config
        self._paper_broker = PaperBroker()
        self._connected = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._market_data_callbacks: Dict[str, Callable] = {}
        self._trade_log_dir = "logs/trades"

    def connect(self) -> bool:
        """连接券商

        Returns:
            是否连接成功
        """
        if self.config.broker_type == "paper" or self.config.dry_run:
            self._connected = True
            logger.info("模拟交易模式已连接")
            return True

        # 真实券商连接（需要 dry_run=False）
        if not self.config.dry_run:
            raise RuntimeError(
                "真实券商连接需要显式确认。"
                "请确保 config.dry_run=False 并且已充分测试。"
            )

        self._connected = True
        return True

    def disconnect(self) -> None:
        """断开券商连接"""
        self._connected = False
        logger.info("已断开券商连接")

    def submit_order(
        self,
        symbol: str,
        direction: str,
        price: float,
        volume: int,
        order_type: str = "limit",
    ) -> Order:
        """提交订单

        Args:
            symbol: 标的代码
            direction: 方向 (buy/sell)
            price: 价格
            volume: 数量
            order_type: 订单类型 (market/limit)

        Returns:
            订单对象
        """
        if not self._connected:
            raise RuntimeError("未连接券商，请先调用 connect()")

        # 风控检查
        if not self._risk_check(symbol, direction, price, volume):
            raise ValueError("风控检查未通过")

        order = Order(
            symbol=symbol,
            direction=direction,
            order_type=order_type,
            price=price,
            volume=volume,
        )

        if self.config.dry_run or self.config.broker_type == "paper":
            result = self._paper_execute(order)
        else:
            # 真实券商调用 - 需要 dry_run=False
            raise RuntimeError("真实券商下单需要 dry_run=False")

        # 记录交易
        if self.config.log_trades:
            self._log_trade(result, "submit")

        return result

    def cancel_order(self, order_id: str) -> bool:
        """取消订单

        Args:
            order_id: 订单 ID

        Returns:
            是否取消成功
        """
        if not self._connected:
            raise RuntimeError("未连接券商")

        if self.config.dry_run or self.config.broker_type == "paper":
            return self._paper_broker.cancel_order(order_id)
        else:
            raise RuntimeError("真实券商撤单需要 dry_run=False")

    def get_positions(self) -> Dict[str, Position]:
        """获取当前持仓

        Returns:
            持仓字典
        """
        if self.config.dry_run or self.config.broker_type == "paper":
            return self._paper_broker.get_positions()
        else:
            raise RuntimeError("真实券商查询需要 dry_run=False")

    def get_orders(self, status: Optional[str] = None) -> List[Order]:
        """获取订单列表

        Args:
            status: 过滤状态

        Returns:
            订单列表
        """
        if self.config.dry_run or self.config.broker_type == "paper":
            return self._paper_broker.get_orders(status)
        else:
            raise RuntimeError("真实券商查询需要 dry_run=False")

    def get_account_info(self) -> AccountInfo:
        """获取账户信息

        Returns:
            账户信息
        """
        if self.config.dry_run or self.config.broker_type == "paper":
            return self._paper_broker.get_account_info()
        else:
            raise RuntimeError("真实券商查询需要 dry_run=False")

    def subscribe_market_data(
        self,
        symbols: List[str],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """订阅实时行情

        Args:
            symbols: 标的列表
            callback: 行情回调函数
        """
        for symbol in symbols:
            self._market_data_callbacks[symbol] = callback
        logger.info(f"已订阅行情: {symbols}")

    def start(self) -> None:
        """启动交易循环"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._trading_loop, daemon=True)
        self._thread.start()
        logger.info("交易循环已启动")

    def stop(self) -> None:
        """停止交易循环"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
        logger.info("交易循环已停止")

    def _trading_loop(self) -> None:
        """交易循环（后台线程）"""
        while self._running:
            try:
                # 模拟行情推送
                for symbol, callback in self._market_data_callbacks.items():
                    current_price = self._paper_broker.get_market_price(symbol)
                    if current_price > 0:
                        # 模拟价格波动
                        new_price = current_price * (1 + random.uniform(-0.001, 0.001))
                        self._paper_broker.set_market_price(symbol, round(new_price, 4))
                        try:
                            callback({
                                "symbol": symbol,
                                "price": new_price,
                                "timestamp": datetime.now().isoformat(),
                            })
                        except Exception as e:
                            logger.error(f"行情回调异常: {e}")
            except Exception as e:
                logger.error(f"交易循环异常: {e}")
            time.sleep(1)

    def _paper_execute(self, order: Order) -> Order:
        """模拟交易执行

        Args:
            order: 订单对象

        Returns:
            执行后的订单
        """
        return self._paper_broker.submit_order(order)

    def _risk_check(
        self,
        symbol: str,
        direction: str,
        price: float,
        volume: int,
    ) -> bool:
        """交易前风控检查

        Args:
            symbol: 标的代码
            direction: 方向
            price: 价格
            volume: 数量

        Returns:
            是否通过风控
        """
        account = self._paper_broker.get_account_info()

        # 检查单笔交易金额不超过总权益的 max_position_pct
        trade_value = price * volume
        if account.total_equity > 0:
            position_pct = trade_value / account.total_equity
            if position_pct > self.config.max_position_pct:
                logger.warning(
                    f"风控拒绝: 单笔交易占比 {position_pct:.2%} "
                    f"超过限制 {self.config.max_position_pct:.2%}"
                )
                return False

        # 检查买入时资金是否足够
        if direction == Direction.BUY.value:
            if trade_value > account.available_cash:
                logger.warning("风控拒绝: 可用资金不足")
                return False

        # 检查卖出时持仓是否足够
        if direction == Direction.SELL.value:
            positions = self._paper_broker.get_positions()
            position = positions.get(symbol)
            if position is None or position.quantity < volume:
                logger.warning("风控拒绝: 持仓不足")
                return False

        return True

    def _log_trade(self, order: Order, action: str) -> None:
        """记录交易日志

        Args:
            order: 订单对象
            action: 动作 (submit/cancel/fill)
        """
        try:
            os.makedirs(self._trade_log_dir, exist_ok=True)
            log_file = os.path.join(
                self._trade_log_dir,
                f"trades_{datetime.now().strftime('%Y%m%d')}.jsonl",
            )
            record = {
                "action": action,
                "order_id": order.order_id,
                "symbol": order.symbol,
                "direction": order.direction,
                "order_type": order.order_type,
                "price": order.price,
                "volume": order.volume,
                "filled_volume": order.filled_volume,
                "filled_price": order.filled_price,
                "status": order.status,
                "commission": order.commission,
                "timestamp": datetime.now().isoformat(),
            }
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"交易日志写入失败: {e}")
