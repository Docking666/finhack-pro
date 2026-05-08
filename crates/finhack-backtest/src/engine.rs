//! 事件驱动回测引擎
//!
//! 核心回测循环:
//! 1. 加载历史数据
//! 2. 按时间排序
//! 3. 逐Bar驱动:
//!    a. 更新持仓市值
//!    b. 调用策略生成信号
//!    c. 风控检查
//!    d. 模拟执行
//!    e. 记录状态
//! 4. 生成回测报告

use crate::broker::BacktestBroker;
use crate::report::ReportGenerator;
use chrono::{DateTime, Utc};
use finhack_core::config::{AppConfig, BacktestConfig, ExecutionConfig, RiskConfig};
use finhack_core::types::{BacktestResult, Bar, Portfolio, Signal, Trade};
use finhack_data::DataLoader;
use finhack_data::loader::CsvDataLoader;
use rust_decimal::Decimal;
use std::collections::HashMap;
use tracing::{debug, info, warn};

/// 回测引擎状态
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BacktestState {
    /// 未初始化
    Uninitialized,
    /// 已初始化(数据已加载)
    Initialized,
    /// 运行中
    Running,
    /// 已暂停
    Paused,
    /// 已完成
    Completed,
    /// 出错
    Error,
}

/// 策略回调trait
///
/// 用户实现此trait来定义交易策略
pub trait Strategy {
    /// 策略名称
    fn name(&self) -> &str;

    /// 初始化策略
    fn initialize(&mut self, _config: &AppConfig) {}

    /// 每个Bar调用，返回交易信号列表
    fn on_bar(&mut self, bar: &Bar, portfolio: &Portfolio) -> Vec<Signal>;

    /// 回测结束时调用
    fn on_finish(&mut self, _result: &BacktestResult) {}
}

/// 事件驱动回测引擎
pub struct BacktestEngine {
    /// 回测配置
    config: BacktestConfig,
    /// 风控配置
    risk_config: RiskConfig,
    /// 执行配置
    execution_config: ExecutionConfig,
    /// 投资组合
    portfolio: Portfolio,
    /// 回测经纪商(模拟撮合)
    broker: BacktestBroker,
    /// 报告生成器
    report_generator: ReportGenerator,
    /// 所有行情数据: symbol -> Vec<Bar>
    market_data: HashMap<String, Vec<Bar>>,
    /// 当前交易日索引(按时间排序的全局索引)
    #[allow(dead_code)]
    current_index: usize,
    /// 所有Bar的统一时间线(用于多标的同步)
    timeline: Vec<DateTime<Utc>>,
    /// 所有交易记录
    trades: Vec<Trade>,
    /// 权益曲线
    equity_curve: Vec<(DateTime<Utc>, Decimal)>,
    /// 回测状态
    state: BacktestState,
    /// 策略列表
    strategies: Vec<Box<dyn Strategy>>,
}

impl BacktestEngine {
    /// 创建新的回测引擎
    pub fn new(
        backtest_config: BacktestConfig,
        risk_config: RiskConfig,
        execution_config: ExecutionConfig,
    ) -> Self {
        let initial_capital = backtest_config.initial_capital_decimal();
        info!(
            initial_capital = %initial_capital,
            start = %backtest_config.start_date,
            end = %backtest_config.end_date,
            "回测引擎已创建"
        );

        Self {
            config: backtest_config,
            risk_config,
            execution_config: execution_config.clone(),
            portfolio: Portfolio::new(initial_capital),
            broker: BacktestBroker::new(execution_config),
            report_generator: ReportGenerator::new(),
            market_data: HashMap::new(),
            current_index: 0,
            timeline: Vec::new(),
            trades: Vec::new(),
            equity_curve: Vec::new(),
            state: BacktestState::Uninitialized,
            strategies: Vec::new(),
        }
    }

    /// 加载行情数据
    pub fn load_data(&mut self, data_dir: &str, symbols: &[String]) -> anyhow::Result<()> {
        let loader = CsvDataLoader::new(data_dir.to_string());

        for symbol in symbols {
            match loader.load_bars(symbol) {
                Ok(bars) => {
                    info!(symbol = %symbol, bars = bars.len(), "行情数据已加载");
                    self.market_data.insert(symbol.clone(), bars);
                }
                Err(e) => {
                    warn!(symbol = %symbol, error = %e, "行情数据加载失败，跳过");
                }
            }
        }

        // 构建统一时间线
        self.build_timeline();
        self.state = BacktestState::Initialized;
        Ok(())
    }

    /// 直接设置行情数据(用于测试)
    pub fn set_market_data(&mut self, market_data: HashMap<String, Vec<Bar>>) {
        self.market_data = market_data;
        self.build_timeline();
        self.state = BacktestState::Initialized;
    }

    /// 构建统一时间线
    ///
    /// 收集所有标的的Bar时间戳，排序去重，用于多标的同步回测
    fn build_timeline(&mut self) {
        let mut timestamps: Vec<DateTime<Utc>> = Vec::new();
        for bars in self.market_data.values() {
            for bar in bars {
                timestamps.push(bar.timestamp);
            }
        }
        timestamps.sort();
        timestamps.dedup();
        self.timeline = timestamps;
        info!(timeline_len = self.timeline.len(), "统一时间线已构建");
    }

    /// 添加策略
    pub fn add_strategy(&mut self, strategy: Box<dyn Strategy>) {
        info!(strategy = strategy.name(), "策略已添加");
        self.strategies.push(strategy);
    }

    /// 运行回测
    pub fn run(&mut self) -> anyhow::Result<BacktestResult> {
        if self.state != BacktestState::Initialized {
            anyhow::bail!("回测引擎未初始化");
        }

        self.state = BacktestState::Running;
        info!("回测开始运行...");

        // 初始化策略
        let app_config = self._build_app_config();
        for strategy in &mut self.strategies {
            strategy.initialize(&app_config);
        }

        // 预处理: 为每个时间戳收集对应的Bar索引(避免借用冲突)
        // 构建时间戳 -> Vec<(symbol, bar_index)> 的映射
        let mut bars_by_timestamp: HashMap<DateTime<Utc>, Vec<Bar>> = HashMap::new();
        for bars in self.market_data.values() {
            for bar in bars {
                bars_by_timestamp
                    .entry(bar.timestamp)
                    .or_insert_with(Vec::new)
                    .push(bar.clone());
            }
        }

        // 逐时间步回测
        let timeline_len = self.timeline.len();
        let timeline: Vec<DateTime<Utc>> = self.timeline.clone();
        for (step_idx, timestamp) in timeline.iter().enumerate() {
            if step_idx % 100 == 0 {
                debug!(
                    step = step_idx,
                    total = timeline_len,
                    date = %timestamp.format("%Y-%m-%d"),
                    "回测进度"
                );
            }

            // 获取当前时间步的Bar(已clone，避免借用冲突)
            let current_bars = match bars_by_timestamp.get(timestamp) {
                Some(bars) => bars.clone(),
                None => continue,
            };

            if current_bars.is_empty() {
                continue;
            }

            // 1. 更新持仓市值
            let prices: HashMap<String, Decimal> = current_bars
                .iter()
                .map(|bar| (bar.symbol.clone(), bar.close))
                .collect();
            self.portfolio.update_market_values(&prices);

            // 2. 记录权益曲线
            let current_value = self.portfolio.total_value;
            self.equity_curve.push((*timestamp, current_value));

            // 3. 调用策略生成信号
            let mut all_signals: Vec<Signal> = Vec::new();
            for strategy in &mut self.strategies {
                for bar in &current_bars {
                    let signals = strategy.on_bar(bar, &self.portfolio);
                    all_signals.extend(signals);
                }
            }

            // 4. 处理每个信号: 风控检查 -> 执行
            for signal in all_signals {
                // 风控检查
                let portfolio_value = self.portfolio.total_value;
                if let Err(e) = self.broker.risk_check(&signal, portfolio_value) {
                    debug!(signal_id = %signal.id, error = %e, "信号未通过风控");
                    continue;
                }

                // 查找对应的Bar进行撮合
                if let Some(bar) = current_bars.iter().find(|b| b.symbol == signal.symbol) {
                    match self.broker.execute(&signal, bar) {
                        Ok(new_trades) => {
                            for trade in &new_trades {
                                self.apply_trade(trade);
                                self.trades.push(trade.clone());
                            }
                        }
                        Err(e) => {
                            debug!(signal_id = %signal.id, error = %e, "信号执行失败");
                        }
                    }
                }
            }

            // 5. 检查是否需要停止交易
            if self.broker.should_stop_trading() {
                warn!("触发风控停止条件，回测终止");
                break;
            }
        }

        self.state = BacktestState::Completed;
        info!(
            total_trades = self.trades.len(),
            final_value = %self.portfolio.total_value,
            "回测完成"
        );

        // 生成回测报告
        let result = self.report_generator.generate(
            &self.equity_curve,
            &self.trades,
            self.config.initial_capital_decimal(),
        );

        // 通知策略回测结束
        for strategy in &mut self.strategies {
            strategy.on_finish(&result);
        }

        Ok(result)
    }

    /// 应用交易结果到投资组合
    fn apply_trade(&mut self, trade: &Trade) {
        let trade_amount = trade.price * Decimal::from(trade.volume);
        let symbol = trade.symbol.clone();
        let side = trade.side;
        let volume = trade.volume;
        let commission = trade.commission;

        // 先读取持仓信息
        let avg_price = self.portfolio.get_position(&symbol)
            .map(|p| p.avg_price)
            .unwrap_or(Decimal::ZERO);
        let current_qty = self.portfolio.get_position(&symbol)
            .map(|p| p.quantity)
            .unwrap_or(0);

        match side {
            finhack_core::types::OrderSide::Buy => {
                // 买入: 更新持仓成本
                let total_cost = avg_price * Decimal::from(current_qty) + trade_amount;
                let total_volume = current_qty + volume;
                let new_avg_price = if total_volume > 0 {
                    total_cost / Decimal::from(total_volume)
                } else {
                    Decimal::ZERO
                };

                let position = self.portfolio.get_or_create_position(&symbol);
                position.avg_price = new_avg_price;
                position.quantity = total_volume;
                self.portfolio.cash -= trade_amount + commission;
            }
            finhack_core::types::OrderSide::Sell => {
                // 卖出: 减少持仓
                let new_qty = current_qty - volume;
                let new_avg_price = if new_qty == 0 {
                    Decimal::ZERO
                } else {
                    avg_price
                };

                let position = self.portfolio.get_or_create_position(&symbol);
                position.quantity = new_qty;
                position.avg_price = new_avg_price;

                let realized = trade_amount
                    - avg_price * Decimal::from(volume)
                    - commission;
                self.portfolio.cash += trade_amount - commission;
                self.portfolio.realized_pnl += realized;
            }
        }
    }

    /// 构建AppConfig(简化版，用于策略初始化)
    fn _build_app_config(&self) -> AppConfig {
        AppConfig {
            system: finhack_core::config::SystemConfig {
                name: "FinHack Pro Backtest".to_string(),
                version: "1.0.0".to_string(),
                mode: "backtest".to_string(),
            },
            data: finhack_core::config::DataConfig {
                storage_type: "csv".to_string(),
                data_dir: "./data".to_string(),
                symbols: self.market_data.keys().cloned().collect(),
            },
            risk: self.risk_config.clone(),
            execution: self.execution_config.clone(),
            backtest: self.config.clone(),
            api: finhack_core::config::ApiConfig {
                host: "0.0.0.0".to_string(),
                port: 8080,
            },
            agents: HashMap::new(),
        }
    }

    /// 获取当前投资组合
    pub fn portfolio(&self) -> &Portfolio {
        &self.portfolio
    }

    /// 获取回测状态
    pub fn state(&self) -> BacktestState {
        self.state
    }

    /// 获取权益曲线
    pub fn equity_curve(&self) -> &[(DateTime<Utc>, Decimal)] {
        &self.equity_curve
    }

    /// 获取交易记录
    pub fn trades(&self) -> &[Trade] {
        &self.trades
    }
}
