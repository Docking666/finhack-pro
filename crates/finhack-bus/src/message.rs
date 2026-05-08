//! 消息类型定义
//!
//! 定义消息总线中使用的消息包装类型和序列化辅助方法

use finhack_core::types::{AgentMessage, MessageType};
use serde::{Deserialize, Serialize};

/// 消息总线信封 - 包装AgentMessage用于传输
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BusEnvelope {
    /// 消息内容
    pub message: AgentMessage,
    /// 序列化后的消息体
    pub body: Vec<u8>,
}

impl BusEnvelope {
    /// 从AgentMessage创建信封
    pub fn new(message: AgentMessage) -> Self {
        let body = serde_json::to_vec(&message).unwrap_or_default();
        Self { message, body }
    }

    /// 从字节反序列化
    pub fn from_bytes(bytes: &[u8]) -> anyhow::Result<Self> {
        let envelope: BusEnvelope = serde_json::from_slice(bytes)?;
        Ok(envelope)
    }
}

/// 消息总线事件 - 内部使用
#[derive(Debug, Clone)]
pub enum BusEvent {
    /// 发布消息到主题
    Publish(MessageType, AgentMessage),
    /// 发送点对点消息
    SendToAgent(String, AgentMessage),
    /// 注册智能体
    RegisterAgent(String),
    /// 注销智能体
    UnregisterAgent(String),
}
