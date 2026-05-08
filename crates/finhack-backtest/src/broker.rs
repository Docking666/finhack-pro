//! 回测模拟经纪商
//!
//! 封装模拟撮合和风控检查，为回测引擎提供统一的交易接口

use finhack_core::config::{ExecutionConfig, RiskConfig};
use finhack_core::types::{Bar, Order, Signal, Trade};
use finhack_execution::SimulatedBroker;
use finhack_risk::RiskManager;
use rust_decimal::Decimal;

/// 回测经纪商
///
/// 整合模拟撮合引擎和风控管理器
pub struct BacktestBroker {
    /// 模拟撮合引擎
    simulator: SimulatedBroker,
    /// 风控管理器
    risk_manager: RiskManager,
}

impl BacktestBroker {
    /// 创建新的回测经纪商
    pub fn new(execution_config: ExecutionConfig) -> Self {
        let risk_config = RiskConfig {
            max_position_pct: 0.2,
            max_drawdown: 0.15,
            var_limit: 0.05,
            max_leverage: 2.0,
            daily_loss_limit: 0.03,
        };

        Self {
            simulator: SimulatedBroker::new(execution_config),
            risk_manager: RiskManager::new(risk_config),
        }
    }

    /// 风控检查
    ///
    /// 将信号转换为订单后进行风控检查
    pub fn risk_check(
        &self,
        signal: &Signal,
        portfolio_value: Decimal,
    ) -> Result<(), finhack_core::error::CoreError> {
        let side = match signal.direction {
            finhack_core::types::SignalDirection::Buy => finhack_core::types::OrderSide::Buy,
            finhack_core::types::SignalDirection::Sell
            | finhack_core::types::SignalDirection::Close => finhack_core::types::OrderSide::Sell,
        };

        let order = Order::new(
            &signal.symbol,
            side,
            signal.order_type,
            signal.price,
            signal.volume,
            &signal.strategy_name,
        );

        self.risk_manager.check_order(&order, portfolio_value)
    }

    /// 执行交易信号
    pub fn execute(
        &mut self,
        signal: &Signal,
        bar: &Bar,
    ) -> Result<Vec<Trade>, finhack_core::error::CoreError> {
        self.simulator.execute(signal, bar)
    }

    /// 更新风控指标
    pub fn update_risk_metrics(&mut self, portfolio_value: Decimal) {
        self.risk_manager.update_metrics(portfolio_value);
    }

    /// 检查是否应该停止交易
    pub fn should_stop_trading(&self) -> bool {
        self.risk_manager.should_stop_trading()
    }

    /// 新交易日重置
    pub fn new_trading_day(&mut self) {
        self.simulator.new_trading_day();
    }

    /// 获取总交易成本
    pub fn total_cost(&self) -> Decimal {
        self.simulator.total_cost()
    }
}
