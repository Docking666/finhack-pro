//! 风控管理器
//!
//! 负责执行所有风控检查，包括:
//! - 单标的仓位限制检查
//! - 总杠杆限制检查
//! - 日内亏损限制检查
//! - VaR计算
//! - 最大回撤监控
//!
//! 所有金额计算使用Decimal确保精度

use crate::rules::{RuleType, RuleViolation, ViolationSeverity};
use finhack_core::config::RiskConfig;
use finhack_core::types::{Order, OrderSide, RiskMetrics};
use rust_decimal::Decimal;
use rust_decimal::MathematicalOps;
use rust_decimal_macros::dec;
use std::collections::HashMap;
use tracing::{debug, info, warn};

/// 风控管理器
pub struct RiskManager {
    /// 风控配置
    config: RiskConfig,
    /// 当前持仓快照: symbol -> position_value
    positions: HashMap<String, Decimal>,
    /// 权益曲线(用于回撤计算)
    equity_curve: Vec<Decimal>,
    /// 历史最高权益
    peak_equity: Decimal,
    /// 当日开盘权益(用于日内亏损计算)
    daily_start_equity: Decimal,
    /// 历史收益率序列(用于VaR计算)
    returns: Vec<Decimal>,
    /// 是否已触发停止交易
    trading_stopped: bool,
}

impl RiskManager {
    /// 创建新的风控管理器
    pub fn new(config: RiskConfig) -> Self {
        Self {
            config,
            positions: HashMap::new(),
            equity_curve: Vec::new(),
            peak_equity: Decimal::ZERO,
            daily_start_equity: Decimal::ZERO,
            returns: Vec::new(),
            trading_stopped: false,
        }
    }

    /// 使用默认配置创建风控管理器
    pub fn with_defaults() -> Self {
        let config = RiskConfig {
            max_position_pct: 0.2,
            max_drawdown: 0.15,
            var_limit: 0.05,
            max_leverage: 2.0,
            daily_loss_limit: 0.03,
        };
        Self::new(config)
    }

    /// 检查订单是否通过风控
    ///
    /// # 参数
    /// - `order`: 待检查的订单
    /// - `portfolio_value`: 当前组合总价值
    ///
    /// # 返回
    /// 通过返回Ok(())，否则返回风控错误
    pub fn check_order(
        &self,
        order: &Order,
        portfolio_value: Decimal,
    ) -> Result<(), finhack_core::error::CoreError> {
        if self.trading_stopped {
            return Err(finhack_core::error::CoreError::RiskError(
                "交易已停止: 触发最大回撤限制".to_string(),
            ));
        }

        let violations = self._check_order_rules(order, portfolio_value);

        if violations.is_empty() {
            debug!(order_id = %order.id, symbol = %order.symbol, "订单通过风控检查");
            Ok(())
        } else {
            let error_msg: String = violations
                .iter()
                .map(|v| format!("[{}] {}", v.rule_type, v.description))
                .collect::<Vec<_>>()
                .join("; ");
            warn!(order_id = %order.id, violations = %error_msg, "订单未通过风控检查");
            Err(finhack_core::error::CoreError::RiskError(error_msg))
        }
    }

    /// 内部: 执行所有风控规则检查
    fn _check_order_rules(
        &self,
        order: &Order,
        portfolio_value: Decimal,
    ) -> Vec<RuleViolation> {
        let mut violations = Vec::new();

        // 1. 单标的仓位限制检查
        if let Some(v) = self.check_position_limit(&order.symbol, &order.side, order.price, order.volume, portfolio_value) {
            violations.push(v);
        }

        // 2. 总杠杆限制检查
        if let Some(v) = self.check_leverage_limit(&order.side, order.price, order.volume, portfolio_value) {
            violations.push(v);
        }

        // 3. 日内亏损限制检查
        if let Some(v) = self.check_daily_loss_limit() {
            violations.push(v);
        }

        violations
    }

    /// 检查单标的仓位限制
    ///
    /// 默认单标的持仓不超过组合总价值的20%
    fn check_position_limit(
        &self,
        symbol: &str,
        side: &OrderSide,
        price: Decimal,
        volume: i64,
        portfolio_value: Decimal,
    ) -> Option<RuleViolation> {
        if portfolio_value <= Decimal::ZERO {
            return None;
        }

        let limit = self.config.max_position_pct_decimal();
        let order_value = price * Decimal::from(volume);
        let current_position_value = self.positions.get(symbol).copied().unwrap_or(Decimal::ZERO);

        let new_position_value = match side {
            OrderSide::Buy => current_position_value + order_value,
            OrderSide::Sell => current_position_value.saturating_sub(order_value),
        };

        let position_pct = new_position_value / portfolio_value;

        if position_pct > limit {
            Some(RuleViolation::new(
                RuleType::MaxPositionPct,
                format!(
                    "标的 {} 仓位占比 {:.2}% 超过限制 {:.2}%",
                    symbol,
                    position_pct * dec!(100),
                    limit * dec!(100),
                ),
                position_pct,
                limit,
                ViolationSeverity::Error,
            ))
        } else {
            None
        }
    }

    /// 检查总杠杆限制
    ///
    /// 总敞口 / 组合价值 <= max_leverage
    fn check_leverage_limit(
        &self,
        side: &OrderSide,
        price: Decimal,
        volume: i64,
        portfolio_value: Decimal,
    ) -> Option<RuleViolation> {
        if portfolio_value <= Decimal::ZERO {
            return None;
        }

        let limit = self.config.max_leverage_decimal();
        let order_value = price * Decimal::from(volume);
        let current_total_exposure: Decimal = self.positions.values().copied().sum();

        let new_exposure = match side {
            OrderSide::Buy => current_total_exposure + order_value,
            OrderSide::Sell => current_total_exposure + order_value, // 空头也计入敞口
        };

        let leverage = new_exposure / portfolio_value;

        if leverage > limit {
            Some(RuleViolation::new(
                RuleType::MaxLeverage,
                format!(
                    "总杠杆 {:.2}x 超过限制 {:.1}x",
                    leverage, limit
                ),
                leverage,
                limit,
                ViolationSeverity::Error,
            ))
        } else {
            None
        }
    }

    /// 检查日内亏损限制
    fn check_daily_loss_limit(&self) -> Option<RuleViolation> {
        if self.daily_start_equity <= Decimal::ZERO || self.equity_curve.is_empty() {
            return None;
        }

        let current_equity = *self.equity_curve.last().unwrap();
        let limit = self.config.daily_loss_limit_decimal();

        if self.daily_start_equity > Decimal::ZERO {
            let daily_loss = (self.daily_start_equity - current_equity) / self.daily_start_equity;

            if daily_loss > limit {
                return Some(RuleViolation::new(
                    RuleType::DailyLossLimit,
                    format!(
                        "日内亏损 {:.2}% 超过限制 {:.2}%",
                        daily_loss * dec!(100),
                        limit * dec!(100),
                    ),
                    daily_loss,
                    limit,
                    ViolationSeverity::Critical,
                ));
            }
        }
        None
    }

    /// 计算VaR (Value at Risk)
    ///
    /// 使用历史模拟法计算VaR
    ///
    /// # 参数
    /// - `returns`: 历史日收益率序列
    /// - `confidence`: 置信水平(如0.95表示95%)
    ///
    /// # 返回
    /// VaR值(正数表示损失)
    pub fn calculate_var(&self, returns: &[Decimal], confidence: Decimal) -> Decimal {
        if returns.is_empty() {
            return Decimal::ZERO;
        }

        // 复制并排序收益率
        let mut sorted_returns = returns.to_vec();
        sorted_returns.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        // 找到对应分位数
        let index = ((Decimal::ONE - confidence) * Decimal::from(sorted_returns.len()))
            .to_string()
            .parse::<usize>()
            .unwrap_or(0);

        let index = index.min(sorted_returns.len().saturating_sub(1));
        sorted_returns[index].abs()
    }

    /// 更新风控指标
    ///
    /// 在每个交易日结束后调用，更新权益曲线和风控指标
    pub fn update_metrics(&mut self, portfolio_value: Decimal) -> RiskMetrics {
        // 更新权益曲线
        if !self.equity_curve.is_empty() {
            let prev_value = *self.equity_curve.last().unwrap();
            if prev_value > Decimal::ZERO {
                let daily_return = (portfolio_value - prev_value) / prev_value;
                self.returns.push(daily_return);
            }
        }
        self.equity_curve.push(portfolio_value);

        // 更新峰值权益
        if portfolio_value > self.peak_equity {
            self.peak_equity = portfolio_value;
        }

        // 计算总敞口
        let total_exposure: Decimal = self.positions.values().copied().sum();
        let net_exposure = total_exposure; // 简化处理，实际需要区分多空

        // 计算VaR
        let var_95 = self.calculate_var(&self.returns, dec!(0.95));

        // 计算最大回撤
        let max_drawdown = self.calculate_max_drawdown();

        // 计算夏普比率(简化版，假设无风险利率为0)
        let sharpe_ratio = self.calculate_sharpe_ratio();

        RiskMetrics {
            portfolio_value,
            total_exposure,
            net_exposure,
            var_95,
            max_drawdown,
            sharpe_ratio,
        }
    }

    /// 计算最大回撤
    fn calculate_max_drawdown(&self) -> Decimal {
        if self.equity_curve.is_empty() {
            return Decimal::ZERO;
        }

        let mut peak = Decimal::ZERO;
        let mut max_dd = Decimal::ZERO;

        for &value in &self.equity_curve {
            if value > peak {
                peak = value;
            }
            if peak > Decimal::ZERO {
                let dd = (peak - value) / peak;
                if dd > max_dd {
                    max_dd = dd;
                }
            }
        }

        max_dd
    }

    /// 计算夏普比率(简化版)
    ///
    /// Sharpe = mean(returns) / std(returns) * sqrt(252)
    fn calculate_sharpe_ratio(&self) -> Decimal {
        if self.returns.len() < 2 {
            return Decimal::ZERO;
        }

        // 计算均值
        let sum: Decimal = self.returns.iter().copied().sum();
        let mean = sum / Decimal::from(self.returns.len());

        // 计算标准差
        let variance: Decimal = self
            .returns
            .iter()
            .map(|r| (*r - mean) * (*r - mean))
            .sum::<Decimal>()
            / Decimal::from(self.returns.len());
        let std_dev = variance.sqrt().unwrap_or(Decimal::ZERO);

        if std_dev == Decimal::ZERO {
            return Decimal::ZERO;
        }

        // 年化: * sqrt(252)
        let sqrt_252 = Decimal::from(252u32).sqrt().unwrap_or(Decimal::from(15));
        let annualized_sharpe = (mean / std_dev) * sqrt_252;
        annualized_sharpe
    }

    /// 判断是否应该停止交易
    ///
    /// 当最大回撤超过限制时返回true
    pub fn should_stop_trading(&self) -> bool {
        if self.trading_stopped {
            return true;
        }

        let max_dd = self.calculate_max_drawdown();
        let limit = self.config.max_drawdown_decimal();

        if max_dd > limit {
            warn!(
                max_drawdown = %max_dd,
                limit = %limit,
                "最大回撤超限，触发停止交易"
            );
            true
        } else {
            false
        }
    }

    /// 更新持仓信息
    pub fn update_position(&mut self, symbol: &str, position_value: Decimal) {
        self.positions.insert(symbol.to_string(), position_value);
    }

    /// 移除持仓信息
    pub fn remove_position(&mut self, symbol: &str) {
        self.positions.remove(symbol);
    }

    /// 设置当日开盘权益
    pub fn set_daily_start_equity(&mut self, equity: Decimal) {
        self.daily_start_equity = equity;
    }

    /// 强制停止交易
    pub fn stop_trading(&mut self) {
        self.trading_stopped = true;
        warn!("交易已被强制停止");
    }

    /// 恢复交易
    pub fn resume_trading(&mut self) {
        self.trading_stopped = false;
        info!("交易已恢复");
    }

    /// 获取当前权益曲线
    pub fn equity_curve(&self) -> &[Decimal] {
        &self.equity_curve
    }

    /// 获取历史收益率
    pub fn returns(&self) -> &[Decimal] {
        &self.returns
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use finhack_core::types::{Order, OrderSide, OrderType};

    fn create_test_order(symbol: &str, side: OrderSide, price: Decimal, volume: i64) -> Order {
        Order::new(symbol, side, OrderType::Market, price, volume, "test_strategy")
    }

    #[test]
    fn test_position_limit_check() {
        let manager = RiskManager::with_defaults();
        let portfolio_value = dec!(1000000);
        let order = create_test_order("000001.SZ", OrderSide::Buy, dec!(10), 30000); // 30万

        // 30万/100万 = 30% > 20%，应该被拒绝
        let result = manager.check_order(&order, portfolio_value);
        assert!(result.is_err());
    }

    #[test]
    fn test_position_limit_pass() {
        let manager = RiskManager::with_defaults();
        let portfolio_value = dec!(1000000);
        let order = create_test_order("000001.SZ", OrderSide::Buy, dec!(10), 15000); // 15万

        // 15万/100万 = 15% < 20%，应该通过
        let result = manager.check_order(&order, portfolio_value);
        assert!(result.is_ok());
    }

    #[test]
    fn test_var_calculation() {
        let manager = RiskManager::with_defaults();
        let returns = vec![
            dec!(-0.02),
            dec!(0.01),
            dec!(-0.03),
            dec!(0.02),
            dec!(-0.01),
            dec!(0.015),
            dec!(-0.025),
            dec!(0.005),
            dec!(-0.04),
            dec!(0.03),
        ];
        let var = manager.calculate_var(&returns, dec!(0.95));
        assert!(var > Decimal::ZERO);
    }

    #[test]
    fn test_max_drawdown() {
        let mut manager = RiskManager::with_defaults();
        manager.update_metrics(dec!(1000000));
        manager.update_metrics(dec!(950000)); // -5%
        manager.update_metrics(dec!(900000)); // -10%
        manager.update_metrics(dec!(850000)); // -15%

        let dd = manager.calculate_max_drawdown();
        assert!(dd >= dec!(0.14)); // 接近15%
    }

    #[test]
    fn test_should_stop_trading() {
        let mut manager = RiskManager::with_defaults();
        manager.update_metrics(dec!(1000000));
        manager.update_metrics(dec!(800000)); // -20% > 15%限制

        assert!(manager.should_stop_trading());
    }
}
