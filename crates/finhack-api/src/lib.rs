//! FinHack Pro - REST API + WebSocket 服务模块
//!
//! 提供HTTP API和WebSocket实时推送功能:
//! - REST API: 回测、组合、订单管理
//! - WebSocket: 实时行情推送

pub mod server;
pub mod routes;
pub mod ws;

pub use server::ApiServer;
