//! FinHack Pro - 风控引擎模块
//!
//! 提供全面的风险管理功能:
//! - 仓位限制检查
//! - 回撤控制
//! - VaR计算
//! - 杠杆限制
//! - 日内亏损限制

pub mod manager;
pub mod rules;

pub use manager::RiskManager;
pub use rules::*;
