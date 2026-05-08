//! FinHack Pro - 核心类型定义模块
//!
//! 本模块定义了整个交易系统的核心数据类型，包括:
//! - 行情数据(Bar, Tick)
//! - 交易信号(Signal)
//! - 订单(Order)
//! - 持仓(Position)
//! - 交易记录(Trade)
//! - 投资组合(Portfolio)
//! - 风控指标(RiskMetrics)
//! - 回测结果(BacktestResult)
//! - 消息类型(MessageType, AgentMessage)
//! - 交易模式(TradingMode)
//!
//! 所有金融数值均使用 rust_decimal::Decimal 确保精度

pub mod types;
pub mod error;
pub mod config;

// 重新导出常用类型
pub use types::*;
pub use error::*;
pub use config::*;
