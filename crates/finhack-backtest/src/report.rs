//! 回测报告生成器
//!
//! 计算各种回测绩效指标:
//! - 年化收益率
//! - 夏普比率
//! - 最大回撤
//! - 胜率
//! - 盈亏比
//! - Calmar比率
//! - Sortino比率
//! - 月度/年度收益分布

use chrono::{DateTime, Datelike, Utc};
use finhack_core::types::{BacktestResult, Trade};
use rust_decimal::Decimal;
use rust_decimal::MathematicalOps;
use rust_decimal_macros::dec;
use std::collections::HashMap;
use tracing::info;

/// 回测报告生成器
pub struct ReportGenerator {
    /// 无风险年利率(默认3%)
    risk_free_rate: Decimal,
}

impl ReportGenerator {
    /// 创建新的报告生成器
    pub fn new() -> Self {
        Self {
            risk_free_rate: dec!(0.03),
        }
    }

    /// 设置无风险利率
    pub fn with_risk_free_rate(mut self, rate: Decimal) -> Self {
        self.risk_free_rate = rate;
        self
    }

    /// 生成回测报告
    ///
    /// # 参数
    /// - `equity_curve`: 权益曲线 (时间, 组合价值)
    /// - `trades`: 所有交易记录
    /// - `initial_capital`: 初始资金
    pub fn generate(
        &self,
        equity_curve: &[(DateTime<Utc>, Decimal)],
        trades: &[Trade],
        initial_capital: Decimal,
    ) -> BacktestResult {
        let mut result = BacktestResult::new();
        result.equity_curve = equity_curve.to_vec();
        result.trades = trades.to_vec();
        result.num_trades = trades.len() as u64;

        if equity_curve.is_empty() {
            return result;
        }

        let final_value = equity_curve.last().unwrap().1;
        let first_value = equity_curve[0].1;

        // 总收益率
        if first_value > Decimal::ZERO {
            result.total_return = (final_value - initial_capital) / initial_capital;
        }

        // 年化收益率
        if let Some((start_date, _)) = equity_curve.first() {
            if let Some((end_date, _)) = equity_curve.last() {
                let days = (*end_date - *start_date).num_days();
                if days > 0 && result.total_return > Decimal::ZERO {
                    let years = Decimal::from(days) / Decimal::from(365);
                    let annual_return =
                        (Decimal::ONE + result.total_return).ln() / years.ln();
                    result.annual_return = (annual_return - Decimal::ONE).round_dp(6);
                }
            }
        }

        // 最大回撤
        result.max_drawdown = self.calculate_max_drawdown(equity_curve);

        // 夏普比率
        result.sharpe_ratio = self.calculate_sharpe_ratio(equity_curve);

        // Sortino比率
        result.sortino_ratio = self.calculate_sortino_ratio(equity_curve);

        // Calmar比率
        if result.max_drawdown > Decimal::ZERO {
            result.calmar_ratio = result.annual_return / result.max_drawdown;
        }

        // 交易统计
        if !trades.is_empty() {
            let (win_rate, profit_factor) = self.calculate_trade_stats(trades);
            result.win_rate = win_rate;
            result.profit_factor = profit_factor;
        }

        // 月度收益率
        result.monthly_returns = self.calculate_monthly_returns(equity_curve);

        info!(
            total_return = %result.total_return,
            annual_return = %result.annual_return,
            sharpe = %result.sharpe_ratio,
            max_drawdown = %result.max_drawdown,
            win_rate = %result.win_rate,
            num_trades = result.num_trades,
            "回测报告已生成"
        );

        result
    }

    /// 计算最大回撤
    fn calculate_max_drawdown(&self, equity_curve: &[(DateTime<Utc>, Decimal)]) -> Decimal {
        if equity_curve.is_empty() {
            return Decimal::ZERO;
        }

        let mut peak = Decimal::ZERO;
        let mut max_dd = Decimal::ZERO;

        for &(_, value) in equity_curve {
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

    /// 计算夏普比率
    ///
    /// Sharpe = (mean(returns) - risk_free) / std(returns) * sqrt(252)
    fn calculate_sharpe_ratio(&self, equity_curve: &[(DateTime<Utc>, Decimal)]) -> Decimal {
        if equity_curve.len() < 2 {
            return Decimal::ZERO;
        }

        let returns = self.calculate_daily_returns(equity_curve);
        if returns.is_empty() {
            return Decimal::ZERO;
        }

        let mean_return: Decimal = returns.iter().copied().sum::<Decimal>()
            / Decimal::from(returns.len());

        let variance: Decimal = returns
            .iter()
            .map(|r| (*r - mean_return) * (*r - mean_return))
            .sum::<Decimal>()
            / Decimal::from(returns.len());

        let std_dev = variance.sqrt().unwrap_or(Decimal::ZERO);
        if std_dev == Decimal::ZERO {
            return Decimal::ZERO;
        }

        // 日无风险利率
        let daily_rf = self.risk_free_rate / Decimal::from(365);

        let sqrt_252 = Decimal::from(252u32).sqrt().unwrap_or(Decimal::from(15));
        let sharpe = (mean_return - daily_rf) / std_dev * sqrt_252;
        sharpe.round_dp(4)
    }

    /// 计算Sortino比率
    ///
    /// 类似夏普比率，但只考虑下行波动率
    fn calculate_sortino_ratio(&self, equity_curve: &[(DateTime<Utc>, Decimal)]) -> Decimal {
        if equity_curve.len() < 2 {
            return Decimal::ZERO;
        }

        let returns = self.calculate_daily_returns(equity_curve);
        if returns.is_empty() {
            return Decimal::ZERO;
        }

        let mean_return: Decimal = returns.iter().copied().sum::<Decimal>()
            / Decimal::from(returns.len());

        // 只计算下行偏差
        let downside_variance: Decimal = returns
            .iter()
            .filter(|r| **r < Decimal::ZERO)
            .map(|r| (*r - mean_return) * (*r - mean_return))
            .sum::<Decimal>()
            / Decimal::from(returns.len());

        let downside_dev = downside_variance.sqrt().unwrap_or(Decimal::ZERO);
        if downside_dev == Decimal::ZERO {
            return Decimal::ZERO;
        }

        let daily_rf = self.risk_free_rate / Decimal::from(365);
        let sqrt_252 = Decimal::from(252u32).sqrt().unwrap_or(Decimal::from(15));
        let sortino = (mean_return - daily_rf) / downside_dev * sqrt_252;
        sortino.round_dp(4)
    }

    /// 计算日收益率序列
    fn calculate_daily_returns(&self, equity_curve: &[(DateTime<Utc>, Decimal)]) -> Vec<Decimal> {
        let mut returns = Vec::new();
        for i in 1..equity_curve.len() {
            let prev = equity_curve[i - 1].1;
            let curr = equity_curve[i].1;
            if prev > Decimal::ZERO {
                returns.push((curr - prev) / prev);
            }
        }
        returns
    }

    /// 计算交易统计(胜率和盈亏比)
    fn calculate_trade_stats(&self, trades: &[Trade]) -> (Decimal, Decimal) {
        // 按订单分组计算盈亏
        let mut order_pnls: HashMap<String, Decimal> = HashMap::new();
        let mut order_sides: HashMap<String, finhack_core::types::OrderSide> = HashMap::new();
        let mut order_prices: HashMap<String, Decimal> = HashMap::new();
        let mut order_volumes: HashMap<String, i64> = HashMap::new();

        for trade in trades {
            let entry = order_pnls.entry(trade.order_id.clone()).or_insert(Decimal::ZERO);
            *entry -= trade.commission; // 扣除手续费
            order_sides.insert(trade.order_id.clone(), trade.side);
            order_prices.insert(trade.order_id.clone(), trade.price);
            *order_volumes.entry(trade.order_id.clone()).or_insert(0) += trade.volume;
        }

        // 简化计算: 使用交易方向统计
        let mut wins = 0i64;
        let mut losses = 0i64;
        let mut total_profit = Decimal::ZERO;
        let mut total_loss = Decimal::ZERO;

        // 简单胜率: 卖出价格 > 买入价格的交易为盈利
        // 这里简化处理，实际应该配对买卖交易
        let sell_trades: Vec<&Trade> = trades.iter().filter(|t| t.side == finhack_core::types::OrderSide::Sell).collect();
        let buy_trades: Vec<&Trade> = trades.iter().filter(|t| t.side == finhack_core::types::OrderSide::Buy).collect();

        // 简化: 比较相邻买卖对
        let pair_count = sell_trades.len().min(buy_trades.len());
        for i in 0..pair_count {
            let buy_price = buy_trades[i].price;
            let sell_price = sell_trades[i].price;
            let pnl = (sell_price - buy_price) * Decimal::from(sell_trades[i].volume)
                - buy_trades[i].commission
                - sell_trades[i].commission;

            if pnl > Decimal::ZERO {
                wins += 1;
                total_profit += pnl;
            } else {
                losses += 1;
                total_loss += pnl.abs();
            }
        }

        let total = wins + losses;
        let win_rate = if total > 0 {
            Decimal::from(wins) / Decimal::from(total)
        } else {
            Decimal::ZERO
        };

        let profit_factor = if total_loss > Decimal::ZERO {
            total_profit / total_loss
        } else if total_profit > Decimal::ZERO {
            Decimal::MAX // 没有亏损，盈亏比无穷大
        } else {
            Decimal::ZERO
        };

        (win_rate.round_dp(4), profit_factor.round_dp(4))
    }

    /// 计算月度收益率
    fn calculate_monthly_returns(
        &self,
        equity_curve: &[(DateTime<Utc>, Decimal)],
    ) -> Vec<(String, Decimal)> {
        if equity_curve.is_empty() {
            return Vec::new();
        }

        // 按月分组: 取每月最后一个值
        let mut monthly_values: HashMap<String, Decimal> = HashMap::new();
        let mut monthly_order: Vec<String> = Vec::new();

        for &(date, value) in equity_curve {
            let month_key = format!("{}-{:02}", date.year(), date.month());
            if !monthly_values.contains_key(&month_key) {
                monthly_order.push(month_key.clone());
            }
            monthly_values.insert(month_key, value);
        }

        // 计算月度收益率
        let mut monthly_returns = Vec::new();
        let mut prev_value: Option<Decimal> = None;

        for month_key in &monthly_order {
            if let Some(&value) = monthly_values.get(month_key) {
                if let Some(prev) = prev_value {
                    if prev > Decimal::ZERO {
                        let monthly_return = (value - prev) / prev;
                        monthly_returns.push((month_key.clone(), monthly_return.round_dp(6)));
                    }
                }
                prev_value = Some(value);
            }
        }

        monthly_returns
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, Utc};

    fn create_test_equity_curve() -> Vec<(DateTime<Utc>, Decimal)> {
        let base = Utc::now();
        let values = vec![
            dec!(1000000),
            dec!(1010000),
            dec!(1005000),
            dec!(1020000),
            dec!(1015000),
            dec!(1030000),
            dec!(1025000),
            dec!(1040000),
            dec!(1035000),
            dec!(1050000),
        ];

        values
            .into_iter()
            .enumerate()
            .map(|(i, v)| (base + Duration::days(i as i64), v))
            .collect()
    }

    #[test]
    fn test_generate_report() {
        let generator = ReportGenerator::new();
        let equity_curve = create_test_equity_curve();
        let result = generator.generate(&equity_curve, &[], dec!(1000000));

        assert!(result.total_return > Decimal::ZERO);
        assert!(result.max_drawdown < dec!(0.1)); // 回撤较小
        assert_eq!(result.num_trades, 0);
    }

    #[test]
    fn test_max_drawdown() {
        let generator = ReportGenerator::new();
        let equity_curve = create_test_equity_curve();
        let dd = generator.calculate_max_drawdown(&equity_curve);
        assert!(dd >= Decimal::ZERO);
    }

    #[test]
    fn test_monthly_returns() {
        let generator = ReportGenerator::new();
        let equity_curve = create_test_equity_curve();
        let monthly = generator.calculate_monthly_returns(&equity_curve);
        // 所有值都在同一个月内，所以月度收益为空或很少
        assert!(monthly.len() <= 1);
    }
}
