//! 模拟撮合引擎
//!
//! 模拟真实交易环境的撮合过程，支持:
//! - 涨跌停检查(A股规则)
//! - T+1规则(当日买入不能卖出)
//! - 手续费计算(万三佣金+千一印花税)
//! - 滑点模拟(固定基点)

use finhack_core::config::ExecutionConfig;
use finhack_core::types::{
    Bar, Order, OrderSide, OrderStatus, Signal, SignalDirection, Trade,
};
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use std::collections::HashSet;
use tracing::{debug, warn};

/// 模拟经纪商 - 撮合引擎
///
/// 模拟A股交易规则进行订单撮合
pub struct SimulatedBroker {
    /// 执行配置
    config: ExecutionConfig,
    /// 当日买入的标的集合(T+1限制)
    bought_today: HashSet<String>,
    /// 涨停价缓存: symbol -> limit_up_price
    limit_up_prices: HashSet<String>,
    /// 跌停价缓存: symbol -> limit_down_price
    limit_down_prices: HashSet<String>,
    /// 累计手续费
    total_commission: Decimal,
    /// 累计印花税
    total_stamp_tax: Decimal,
}

impl SimulatedBroker {
    /// 创建新的模拟经纪商
    pub fn new(config: ExecutionConfig) -> Self {
        debug!(
            commission = config.commission_rate,
            stamp_tax = config.stamp_tax_rate,
            slippage = config.slippage_bps,
            "模拟经纪商已初始化"
        );
        Self {
            config,
            bought_today: HashSet::new(),
            limit_up_prices: HashSet::new(),
            limit_down_prices: HashSet::new(),
            total_commission: Decimal::ZERO,
            total_stamp_tax: Decimal::ZERO,
        }
    }

    /// 使用默认配置创建
    pub fn with_defaults() -> Self {
        let config = ExecutionConfig {
            algorithm: "twap".to_string(),
            slippage_bps: 2,
            commission_rate: 0.0003,
            stamp_tax_rate: 0.001,
        };
        Self::new(config)
    }

    /// 执行交易信号
    ///
    /// 根据当前Bar数据模拟撮合，返回成交记录列表
    pub fn execute(
        &mut self,
        signal: &Signal,
        bar: &Bar,
    ) -> Result<Vec<Trade>, finhack_core::error::CoreError> {
        let mut trades = Vec::new();

        // 1. 检查涨跌停
        if let Err(e) = self.check_price_limit(signal, bar) {
            warn!(symbol = %signal.symbol, error = %e, "涨跌停检查未通过");
            return Ok(trades);
        }

        // 2. T+1检查(仅卖出时)
        if matches!(signal.direction, SignalDirection::Sell | SignalDirection::Close) {
            if self.bought_today.contains(&signal.symbol) {
                warn!(
                    symbol = %signal.symbol,
                    "T+1限制: 当日买入的标的不能卖出"
                );
                return Ok(trades);
            }
        }

        // 3. 计算成交价(加入滑点)
        let execution_price = self.apply_slippage(signal.price, signal.direction);

        // 4. 计算手续费
        let trade_amount = execution_price * Decimal::from(signal.volume);
        let commission = self.calculate_commission(trade_amount);
        let stamp_tax = self.calculate_stamp_tax(
            trade_amount,
            matches!(signal.direction, SignalDirection::Sell | SignalDirection::Close),
        );

        // 5. 创建交易记录
        let side = match signal.direction {
            SignalDirection::Buy => OrderSide::Buy,
            SignalDirection::Sell | SignalDirection::Close => OrderSide::Sell,
        };

        let trade = Trade::new(
            format!("sim_{}", signal.id),
            &signal.symbol,
            side,
            execution_price,
            signal.volume,
            commission + stamp_tax,
        );

        // 6. 更新状态
        if matches!(signal.direction, SignalDirection::Buy) {
            self.bought_today.insert(signal.symbol.clone());
        }

        self.total_commission += commission;
        self.total_stamp_tax += stamp_tax;

        debug!(
            symbol = %signal.symbol,
            price = %execution_price,
            volume = signal.volume,
            commission = %commission,
            stamp_tax = %stamp_tax,
            "模拟成交"
        );

        trades.push(trade);
        Ok(trades)
    }

    /// 执行订单(直接撮合)
    pub fn execute_order(
        &mut self,
        order: &mut Order,
        bar: &Bar,
    ) -> Result<Vec<Trade>, finhack_core::error::CoreError> {
        let mut trades = Vec::new();

        // 检查涨跌停
        let direction = match order.side {
            OrderSide::Buy => SignalDirection::Buy,
            OrderSide::Sell => SignalDirection::Sell,
        };

        let signal_for_check = Signal::new(
            &order.symbol,
            direction,
            order.price,
            order.volume,
            &order.strategy_name,
        );

        if let Err(e) = self.check_price_limit(&signal_for_check, bar) {
            order.status = OrderStatus::Rejected;
            warn!(symbol = %order.symbol, error = %e, "订单被拒绝");
            return Ok(trades);
        }

        // T+1检查
        if order.side == OrderSide::Sell && self.bought_today.contains(&order.symbol) {
            order.status = OrderStatus::Rejected;
            warn!(symbol = %order.symbol, "T+1限制: 订单被拒绝");
            return Ok(trades);
        }

        // 计算成交价
        let execution_price = self.apply_slippage_for_side(order.price, order.side);

        // 计算手续费
        let trade_amount = execution_price * Decimal::from(order.volume);
        let commission = self.calculate_commission(trade_amount);
        let is_sell = order.side == OrderSide::Sell;
        let stamp_tax = self.calculate_stamp_tax(trade_amount, is_sell);

        // 更新订单状态
        order.filled_volume = order.volume;
        order.status = OrderStatus::Filled;

        // 创建交易记录
        let trade = Trade::new(
            &order.id,
            &order.symbol,
            order.side,
            execution_price,
            order.volume,
            commission + stamp_tax,
        );

        // 更新T+1状态
        if order.side == OrderSide::Buy {
            self.bought_today.insert(order.symbol.clone());
        }

        self.total_commission += commission;
        self.total_stamp_tax += stamp_tax;

        trades.push(trade);
        Ok(trades)
    }

    /// 检查涨跌停限制
    ///
    /// A股规则: 当日涨跌幅限制为前一交易日收盘价的+-10%(ST股为5%)
    fn check_price_limit(
        &self,
        signal: &Signal,
        bar: &Bar,
    ) -> Result<(), finhack_core::error::CoreError> {
        // 简化处理: 使用前一根Bar的收盘价作为参考价
        // 涨跌停检查: 买入价不能高于涨停价，卖出价不能低于跌停价
        let limit_up = bar.close * dec!(1.1);  // 涨停价(简化，实际需要前一日收盘价)
        let limit_down = bar.close * dec!(0.9); // 跌停价

        match signal.direction {
            SignalDirection::Buy => {
                if signal.price >= limit_up {
                    return Err(finhack_core::error::CoreError::ExecutionError(format!(
                        "买入价 {} 超过涨停价 {}",
                        signal.price, limit_up
                    )));
                }
            }
            SignalDirection::Sell | SignalDirection::Close => {
                if signal.price <= limit_down {
                    return Err(finhack_core::error::CoreError::ExecutionError(format!(
                        "卖出价 {} 低于跌停价 {}",
                        signal.price, limit_down
                    )));
                }
            }
        }

        Ok(())
    }

    /// 应用滑点
    ///
    /// 买入时价格上浮，卖出时价格下浮
    fn apply_slippage(&self, price: Decimal, direction: SignalDirection) -> Decimal {
        let slippage = self.config.slippage_decimal();
        match direction {
            SignalDirection::Buy => price * (Decimal::ONE + slippage),
            SignalDirection::Sell | SignalDirection::Close => price * (Decimal::ONE - slippage),
        }
    }

    /// 根据订单方向应用滑点
    fn apply_slippage_for_side(&self, price: Decimal, side: OrderSide) -> Decimal {
        let slippage = self.config.slippage_decimal();
        match side {
            OrderSide::Buy => price * (Decimal::ONE + slippage),
            OrderSide::Sell => price * (Decimal::ONE - slippage),
        }
    }

    /// 计算佣金
    ///
    /// 佣金 = 成交金额 * 佣金费率，最低5元
    fn calculate_commission(&self, amount: Decimal) -> Decimal {
        let commission = amount * self.config.commission_rate_decimal();
        // 最低佣金5元
        if commission < dec!(5) && commission > Decimal::ZERO {
            dec!(5)
        } else {
            commission
        }
    }

    /// 计算印花税
    ///
    /// 印花税仅卖出时收取，费率为千分之一
    fn calculate_stamp_tax(&self, amount: Decimal, is_sell: bool) -> Decimal {
        if is_sell {
            amount * self.config.stamp_tax_rate_decimal()
        } else {
            Decimal::ZERO
        }
    }

    /// 新交易日重置T+1状态
    pub fn new_trading_day(&mut self) {
        self.bought_today.clear();
        self.limit_up_prices.clear();
        self.limit_down_prices.clear();
        debug!("新交易日: T+1状态已重置");
    }

    /// 获取累计手续费
    pub fn total_commission(&self) -> Decimal {
        self.total_commission
    }

    /// 获取累计印花税
    pub fn total_stamp_tax(&self) -> Decimal {
        self.total_stamp_tax
    }

    /// 获取总交易成本
    pub fn total_cost(&self) -> Decimal {
        self.total_commission + self.total_stamp_tax
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use finhack_core::types::SignalDirection;

    fn create_test_bar(price: Decimal) -> Bar {
        Bar::new(
            "000001.SZ",
            Utc::now(),
            price,
            price * dec!(1.02),
            price * dec!(0.98),
            price * dec!(1.01),
            1000000,
            price * Decimal::from(1000000),
        )
    }

    fn create_test_signal(direction: SignalDirection, price: Decimal, volume: i64) -> Signal {
        let mut signal = Signal::new("000001.SZ", direction, price, volume, "test");
        signal.price = price;
        signal
    }

    #[test]
    fn test_buy_execution() {
        let mut broker = SimulatedBroker::with_defaults();
        let bar = create_test_bar(dec!(10));
        let signal = create_test_signal(SignalDirection::Buy, dec!(10), 1000);

        let trades = broker.execute(&signal, &bar).unwrap();
        assert_eq!(trades.len(), 1);

        let trade = &trades[0];
        assert!(trade.commission > Decimal::ZERO);
        assert_eq!(trade.volume, 1000);
    }

    #[test]
    fn test_sell_no_stamp_tax_on_buy() {
        let mut broker = SimulatedBroker::with_defaults();
        let bar = create_test_bar(dec!(10));
        let signal = create_test_signal(SignalDirection::Buy, dec!(10), 1000);

        let trades = broker.execute(&signal, &bar).unwrap();
        // 买入时没有印花税
        assert_eq!(broker.total_stamp_tax(), Decimal::ZERO);
        // 但有佣金
        assert!(broker.total_commission() > Decimal::ZERO);
        assert!(!trades.is_empty());
    }

    #[test]
    fn test_t1_rule() {
        let mut broker = SimulatedBroker::with_defaults();
        let bar = create_test_bar(dec!(10));

        // 先买入
        let buy_signal = create_test_signal(SignalDirection::Buy, dec!(10), 1000);
        let buy_trades = broker.execute(&buy_signal, &bar).unwrap();
        assert_eq!(buy_trades.len(), 1);

        // 同日卖出应该被拒绝
        let sell_signal = create_test_signal(SignalDirection::Sell, dec!(10), 1000);
        let sell_trades = broker.execute(&sell_signal, &bar).unwrap();
        assert_eq!(sell_trades.len(), 0);
    }

    #[test]
    fn test_slippage() {
        let broker = SimulatedBroker::with_defaults();
        let price = dec!(10);

        let buy_price = broker.apply_slippage(price, SignalDirection::Buy);
        let sell_price = broker.apply_slippage(price, SignalDirection::Sell);

        // 买入价应该高于原价
        assert!(buy_price > price);
        // 卖出价应该低于原价
        assert!(sell_price < price);
    }

    #[test]
    fn test_new_trading_day_reset() {
        let mut broker = SimulatedBroker::with_defaults();
        let bar = create_test_bar(dec!(10));

        // 买入
        let buy_signal = create_test_signal(SignalDirection::Buy, dec!(10), 1000);
        broker.execute(&buy_signal, &bar).unwrap();

        // 新交易日
        broker.new_trading_day();

        // 现在可以卖出了
        let sell_signal = create_test_signal(SignalDirection::Sell, dec!(10), 1000);
        let sell_trades = broker.execute(&sell_signal, &bar).unwrap();
        assert_eq!(sell_trades.len(), 1);
    }
}
