//! FinHack Pro - 回测引擎模块
//!
//! 事件驱动的回测引擎，支持:
//! - 逐Bar驱动回测
//! - 多标的回测
//! - 风控集成
//! - 完整的回测报告生成

pub mod engine;
pub mod broker;
pub mod report;

pub use engine::BacktestEngine;
pub use broker::BacktestBroker;
pub use report::ReportGenerator;
