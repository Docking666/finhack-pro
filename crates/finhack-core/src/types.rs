//! 核心数据类型定义
//!
//! 定义交易系统中所有核心数据结构，所有金额字段使用 Decimal 类型

use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use rust_decimal_macros::dec;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

// ============================================================================
// 行情数据类型
// ============================================================================

/// K线(Bar)数据 - OHLCV格式
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Bar {
    /// 标的代码，如 "000001.SZ"
    pub symbol: String,
    /// 时间戳
    pub timestamp: DateTime<Utc>,
    /// 开盘价
    pub open: Decimal,
    /// 最高价
    pub high: Decimal,
    /// 最低价
    pub low: Decimal,
    /// 收盘价
    pub close: Decimal,
    /// 成交量(股)
    pub volume: i64,
    /// 成交额(元)
    pub amount: Decimal,
    /// 复权因子
    pub adj_factor: Decimal,
}

impl Bar {
    /// 创建新的Bar数据
    pub fn new(
        symbol: impl Into<String>,
        timestamp: DateTime<Utc>,
        open: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: i64,
        amount: Decimal,
    ) -> Self {
        Self {
            symbol: symbol.into(),
            timestamp,
            open,
            high,
            low,
            close,
            volume,
            amount,
            adj_factor: dec!(1.0),
        }
    }

    /// 获取涨跌幅(百分比)
    pub fn change_pct(&self) -> Decimal {
        if self.open == Decimal::ZERO {
            return Decimal::ZERO;
        }
        (self.close - self.open) / self.open * dec!(100)
    }

    /// 获取振幅(百分比)
    pub fn amplitude(&self) -> Decimal {
        if self.open == Decimal::ZERO {
            return Decimal::ZERO;
        }
        (self.high - self.low) / self.open * dec!(100)
    }
}

/// 逐笔(Tick)行情数据
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Tick {
    /// 标的代码
    pub symbol: String,
    /// 时间戳
    pub timestamp: DateTime<Utc>,
    /// 最新价
    pub price: Decimal,
    /// 成交量(股)
    pub volume: i64,
    /// 买一价
    pub bid_price: Decimal,
    /// 卖一价
    pub ask_price: Decimal,
    /// 买一量
    pub bid_volume: i64,
    /// 卖一量
    pub ask_volume: i64,
}

// ============================================================================
// 交易信号类型
// ============================================================================

/// 信号方向
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SignalDirection {
    /// 买入
    Buy,
    /// 卖出
    Sell,
    /// 平仓
    Close,
}

impl std::fmt::Display for SignalDirection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SignalDirection::Buy => write!(f, "BUY"),
            SignalDirection::Sell => write!(f, "SELL"),
            SignalDirection::Close => write!(f, "CLOSE"),
        }
    }
}

/// 订单类型
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderType {
    /// 市价单
    Market,
    /// 限价单
    Limit,
    /// 止损单
    Stop,
}

impl std::fmt::Display for OrderType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OrderType::Market => write!(f, "MARKET"),
            OrderType::Limit => write!(f, "LIMIT"),
            OrderType::Stop => write!(f, "STOP"),
        }
    }
}

/// 交易信号 - 由策略生成
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Signal {
    /// 信号唯一ID
    pub id: String,
    /// 标的代码
    pub symbol: String,
    /// 信号方向
    pub direction: SignalDirection,
    /// 目标价格
    pub price: Decimal,
    /// 目标数量(股)
    pub volume: i64,
    /// 订单类型
    pub order_type: OrderType,
    /// 止损价
    pub stop_loss: Option<Decimal>,
    /// 止盈价
    pub take_profit: Option<Decimal>,
    /// 策略名称
    pub strategy_name: String,
    /// 信号生成时间
    pub timestamp: DateTime<Utc>,
}

impl Signal {
    /// 创建新的交易信号
    pub fn new(
        symbol: impl Into<String>,
        direction: SignalDirection,
        price: Decimal,
        volume: i64,
        strategy_name: impl Into<String>,
    ) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            symbol: symbol.into(),
            direction,
            price,
            volume,
            order_type: OrderType::Market,
            stop_loss: None,
            take_profit: None,
            strategy_name: strategy_name.into(),
            timestamp: Utc::now(),
        }
    }
}

// ============================================================================
// 订单类型
// ============================================================================

/// 订单方向
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderSide {
    /// 买入
    Buy,
    /// 卖出
    Sell,
}

impl std::fmt::Display for OrderSide {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OrderSide::Buy => write!(f, "BUY"),
            OrderSide::Sell => write!(f, "SELL"),
        }
    }
}

/// 订单状态
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderStatus {
    /// 待提交
    Pending,
    /// 已提交
    Submitted,
    /// 部分成交
    PartiallyFilled,
    /// 完全成交
    Filled,
    /// 已取消
    Cancelled,
    /// 已拒绝
    Rejected,
}

impl std::fmt::Display for OrderStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OrderStatus::Pending => write!(f, "PENDING"),
            OrderStatus::Submitted => write!(f, "SUBMITTED"),
            OrderStatus::PartiallyFilled => write!(f, "PARTIALLY_FILLED"),
            OrderStatus::Filled => write!(f, "FILLED"),
            OrderStatus::Cancelled => write!(f, "CANCELLED"),
            OrderStatus::Rejected => write!(f, "REJECTED"),
        }
    }
}

/// 订单
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Order {
    /// 订单唯一ID
    pub id: String,
    /// 标的代码
    pub symbol: String,
    /// 订单方向
    pub side: OrderSide,
    /// 订单类型
    pub order_type: OrderType,
    /// 订单价格
    pub price: Decimal,
    /// 订单数量(股)
    pub volume: i64,
    /// 已成交数量(股)
    pub filled_volume: i64,
    /// 订单状态
    pub status: OrderStatus,
    /// 创建时间
    pub created_at: DateTime<Utc>,
    /// 策略名称
    pub strategy_name: String,
}

impl Order {
    /// 创建新订单
    pub fn new(
        symbol: impl Into<String>,
        side: OrderSide,
        order_type: OrderType,
        price: Decimal,
        volume: i64,
        strategy_name: impl Into<String>,
    ) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            symbol: symbol.into(),
            side,
            order_type,
            price,
            volume,
            filled_volume: 0,
            status: OrderStatus::Pending,
            created_at: Utc::now(),
            strategy_name: strategy_name.into(),
        }
    }

    /// 订单是否已完成
    pub fn is_finished(&self) -> bool {
        matches!(
            self.status,
            OrderStatus::Filled | OrderStatus::Cancelled | OrderStatus::Rejected
        )
    }

    /// 剩余未成交数量
    pub fn remaining_volume(&self) -> i64 {
        self.volume - self.filled_volume
    }
}

// ============================================================================
// 持仓与交易记录
// ============================================================================

/// 持仓信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    /// 标的代码
    pub symbol: String,
    /// 持仓数量(股)，正数为多头，负数为空头
    pub quantity: i64,
    /// 平均持仓成本
    pub avg_price: Decimal,
    /// 当前市值
    pub market_value: Decimal,
    /// 未实现盈亏
    pub unrealized_pnl: Decimal,
}

impl Position {
    /// 创建新持仓
    pub fn new(symbol: impl Into<String>) -> Self {
        Self {
            symbol: symbol.into(),
            quantity: 0,
            avg_price: Decimal::ZERO,
            market_value: Decimal::ZERO,
            unrealized_pnl: Decimal::ZERO,
        }
    }

    /// 更新持仓市值和盈亏
    pub fn update_market_value(&mut self, current_price: Decimal) {
        self.market_value = current_price * Decimal::from(self.quantity);
        if self.quantity != 0 {
            self.unrealized_pnl =
                (current_price - self.avg_price) * Decimal::from(self.quantity);
        }
    }

    /// 是否有空仓
    pub fn is_empty(&self) -> bool {
        self.quantity == 0
    }
}

/// 交易记录
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trade {
    /// 关联订单ID
    pub order_id: String,
    /// 标的代码
    pub symbol: String,
    /// 交易方向
    pub side: OrderSide,
    /// 成交价格
    pub price: Decimal,
    /// 成交数量(股)
    pub volume: i64,
    /// 手续费
    pub commission: Decimal,
    /// 成交时间
    pub timestamp: DateTime<Utc>,
}

impl Trade {
    /// 创建交易记录
    pub fn new(
        order_id: impl Into<String>,
        symbol: impl Into<String>,
        side: OrderSide,
        price: Decimal,
        volume: i64,
        commission: Decimal,
    ) -> Self {
        Self {
            order_id: order_id.into(),
            symbol: symbol.into(),
            side,
            price,
            volume,
            commission,
            timestamp: Utc::now(),
        }
    }

    /// 计算交易金额
    pub fn amount(&self) -> Decimal {
        self.price * Decimal::from(self.volume)
    }
}

// ============================================================================
// 投资组合
// ============================================================================

/// 投资组合
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Portfolio {
    /// 可用现金
    pub cash: Decimal,
    /// 持仓列表: symbol -> Position
    pub positions: HashMap<String, Position>,
    /// 总资产
    pub total_value: Decimal,
    /// 未实现盈亏总额
    pub unrealized_pnl: Decimal,
    /// 已实现盈亏总额
    pub realized_pnl: Decimal,
}

impl Portfolio {
    /// 创建新投资组合
    pub fn new(initial_capital: Decimal) -> Self {
        Self {
            cash: initial_capital,
            positions: HashMap::new(),
            total_value: initial_capital,
            unrealized_pnl: Decimal::ZERO,
            realized_pnl: Decimal::ZERO,
        }
    }

    /// 获取指定标的的持仓
    pub fn get_position(&self, symbol: &str) -> Option<&Position> {
        self.positions.get(symbol)
    }

    /// 获取或创建持仓
    pub fn get_or_create_position(&mut self, symbol: &str) -> &mut Position {
        self.positions
            .entry(symbol.to_string())
            .or_insert_with(|| Position::new(symbol))
    }

    /// 更新所有持仓的市值
    pub fn update_market_values(&mut self, prices: &HashMap<String, Decimal>) {
        let mut total_unrealized = Decimal::ZERO;
        for (symbol, position) in &mut self.positions {
            if let Some(price) = prices.get(symbol) {
                position.update_market_value(*price);
                total_unrealized += position.unrealized_pnl;
            }
        }
        self.unrealized_pnl = total_unrealized;
        self.total_value = self.cash + self.unrealized_pnl;
        for position in self.positions.values() {
            self.total_value += position.market_value;
        }
    }

    /// 计算总市值(不含现金)
    pub fn total_market_value(&self) -> Decimal {
        self.positions.values().map(|p| p.market_value).sum()
    }
}

// ============================================================================
// 风控指标
// ============================================================================

/// 风控指标
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskMetrics {
    /// 组合总价值
    pub portfolio_value: Decimal,
    /// 总敞口(多头+空头绝对值之和)
    pub total_exposure: Decimal,
    /// 净敞口(多头-空头)
    pub net_exposure: Decimal,
    /// 95% VaR (Value at Risk)
    pub var_95: Decimal,
    /// 最大回撤
    pub max_drawdown: Decimal,
    /// 夏普比率
    pub sharpe_ratio: Decimal,
}

impl RiskMetrics {
    /// 创建默认风控指标
    pub fn new(portfolio_value: Decimal) -> Self {
        Self {
            portfolio_value,
            total_exposure: Decimal::ZERO,
            net_exposure: Decimal::ZERO,
            var_95: Decimal::ZERO,
            max_drawdown: Decimal::ZERO,
            sharpe_ratio: Decimal::ZERO,
        }
    }
}

// ============================================================================
// 回测结果
// ============================================================================

/// 回测结果
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestResult {
    /// 总收益率
    pub total_return: Decimal,
    /// 年化收益率
    pub annual_return: Decimal,
    /// 夏普比率
    pub sharpe_ratio: Decimal,
    /// 最大回撤
    pub max_drawdown: Decimal,
    /// 胜率
    pub win_rate: Decimal,
    /// 盈亏比
    pub profit_factor: Decimal,
    /// 总交易次数
    pub num_trades: u64,
    /// 权益曲线(时间 -> 组合价值)
    pub equity_curve: Vec<(DateTime<Utc>, Decimal)>,
    /// 所有交易记录
    pub trades: Vec<Trade>,
    /// Calmar比率
    pub calmar_ratio: Decimal,
    /// Sortino比率
    pub sortino_ratio: Decimal,
    /// 月度收益率
    pub monthly_returns: Vec<(String, Decimal)>,
}

impl BacktestResult {
    /// 创建默认回测结果
    pub fn new() -> Self {
        Self {
            total_return: Decimal::ZERO,
            annual_return: Decimal::ZERO,
            sharpe_ratio: Decimal::ZERO,
            max_drawdown: Decimal::ZERO,
            win_rate: Decimal::ZERO,
            profit_factor: Decimal::ZERO,
            num_trades: 0,
            equity_curve: Vec::new(),
            trades: Vec::new(),
            calmar_ratio: Decimal::ZERO,
            sortino_ratio: Decimal::ZERO,
            monthly_returns: Vec::new(),
        }
    }
}

// ============================================================================
// 消息类型
// ============================================================================

/// 消息类型枚举 - 用于消息总线的主题路由
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum MessageType {
    /// 行情数据
    MarketData,
    /// 交易信号
    TradeSignal,
    /// 风控告警
    RiskAlert,
    /// 执行报告
    ExecutionReport,
    /// 智能体心跳
    AgentHeartbeat,
    /// 控制命令
    ControlCommand,
}

impl std::fmt::Display for MessageType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MessageType::MarketData => write!(f, "market_data"),
            MessageType::TradeSignal => write!(f, "trade_signal"),
            MessageType::RiskAlert => write!(f, "risk_alert"),
            MessageType::ExecutionReport => write!(f, "execution_report"),
            MessageType::AgentHeartbeat => write!(f, "agent_heartbeat"),
            MessageType::ControlCommand => write!(f, "control_command"),
        }
    }
}

/// 消息优先级
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum MessagePriority {
    /// 低优先级
    Low = 0,
    /// 普通优先级
    Normal = 1,
    /// 高优先级
    High = 2,
    /// 紧急优先级
    Critical = 3,
}

/// 智能体消息 - 智能体之间的通信单元
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentMessage {
    /// 消息唯一ID
    pub msg_id: String,
    /// 发送者ID
    pub sender: String,
    /// 接收者ID (空字符串表示广播)
    pub receiver: String,
    /// 消息类型
    pub msg_type: MessageType,
    /// 消息负载(JSON格式)
    pub payload: serde_json::Value,
    /// 时间戳
    pub timestamp: DateTime<Utc>,
    /// 优先级
    pub priority: MessagePriority,
}

impl AgentMessage {
    /// 创建新的智能体消息
    pub fn new(
        sender: impl Into<String>,
        receiver: impl Into<String>,
        msg_type: MessageType,
        payload: serde_json::Value,
    ) -> Self {
        Self {
            msg_id: Uuid::new_v4().to_string(),
            sender: sender.into(),
            receiver: receiver.into(),
            msg_type,
            payload,
            timestamp: Utc::now(),
            priority: MessagePriority::Normal,
        }
    }

    /// 创建广播消息
    pub fn broadcast(sender: impl Into<String>, msg_type: MessageType, payload: serde_json::Value) -> Self {
        Self {
            msg_id: Uuid::new_v4().to_string(),
            sender: sender.into(),
            receiver: String::new(),
            msg_type,
            payload,
            timestamp: Utc::now(),
            priority: MessagePriority::Normal,
        }
    }

    /// 设置优先级
    pub fn with_priority(mut self, priority: MessagePriority) -> Self {
        self.priority = priority;
        self
    }
}

// ============================================================================
// 交易模式
// ============================================================================

/// 交易模式枚举
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TradingMode {
    /// 回测模式
    Backtest,
    /// 模拟交易模式
    Paper,
    /// 实盘模式
    Live,
}

impl std::fmt::Display for TradingMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TradingMode::Backtest => write!(f, "backtest"),
            TradingMode::Paper => write!(f, "paper"),
            TradingMode::Live => write!(f, "live"),
        }
    }
}

impl std::str::FromStr for TradingMode {
    type Err = crate::CoreError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "backtest" => Ok(TradingMode::Backtest),
            "paper" => Ok(TradingMode::Paper),
            "live" => Ok(TradingMode::Live),
            _ => Err(crate::error::CoreError::ConfigError(format!("未知的交易模式: {}", s))),
        }
    }
}
