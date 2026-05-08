//! FinHack Pro - 执行引擎模块
//!
//! 提供订单执行功能，包括:
//! - TWAP/VWAP/冰山等算法执行
//! - 模拟撮合引擎
//! - 滑点和手续费模拟

pub mod engine;
pub mod simulator;

pub use engine::ExecutionEngine;
pub use simulator::SimulatedBroker;
