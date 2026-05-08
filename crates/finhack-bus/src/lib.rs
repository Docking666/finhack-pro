//! FinHack Pro - 消息总线模块
//!
//! 基于tokio channels实现的事件驱动消息总线，支持:
//! - 发布-订阅模式(广播)
//! - 点对点消息(智能体间通信)
//! - 按消息类型路由

pub mod message;
pub mod bus;

pub use message::*;
pub use bus::MessageBus;
