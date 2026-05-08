//! 发布-订阅消息总线实现
//!
//! 基于tokio::sync::broadcast实现发布-订阅模式，
//! 基于tokio::sync::mpsc实现点对点通信。
//!
//! 支持功能:
//! - 按MessageType主题发布消息
//! - 智能体订阅特定消息类型
//! - 智能体之间的点对点消息传递
//! - 消息优先级排序

use finhack_core::types::{AgentMessage, MessageType};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, Mutex};
use tracing::{debug, info, warn};

/// 消息总线 - 系统的核心通信枢纽
///
/// 每个MessageType对应一个broadcast channel用于发布-订阅，
/// 每个Agent对应一个mpsc channel用于点对点通信。
pub struct MessageBus {
    /// 主题订阅者: MessageType -> broadcast::Sender
    subscribers: Arc<Mutex<HashMap<MessageType, broadcast::Sender<AgentMessage>>>>,
    /// 智能体消息队列: agent_id -> mpsc::Sender
    agent_queues: Arc<Mutex<HashMap<String, mpsc::Sender<AgentMessage>>>>,
    /// broadcast channel容量
    #[allow(dead_code)]
    channel_capacity: usize,
    /// mpsc channel容量
    agent_queue_capacity: usize,
}

impl MessageBus {
    /// 创建新的消息总线
    ///
    /// # 参数
    /// - `channel_capacity`: broadcast channel缓冲区大小
    /// - `agent_queue_capacity`: 每个智能体消息队列大小
    pub fn new(channel_capacity: usize, agent_queue_capacity: usize) -> Self {
        // 预先为所有MessageType创建broadcast channel
        let mut subscribers = HashMap::new();
        let msg_types = [
            MessageType::MarketData,
            MessageType::TradeSignal,
            MessageType::RiskAlert,
            MessageType::ExecutionReport,
            MessageType::AgentHeartbeat,
            MessageType::ControlCommand,
        ];

        for msg_type in msg_types {
            let (tx, _) = broadcast::channel(channel_capacity);
            subscribers.insert(msg_type, tx);
        }

        Self {
            subscribers: Arc::new(Mutex::new(subscribers)),
            agent_queues: Arc::new(Mutex::new(HashMap::new())),
            channel_capacity,
            agent_queue_capacity,
        }
    }

    /// 创建默认配置的消息总线
    pub fn default() -> Self {
        Self::new(1024, 256)
    }

    /// 发布消息到指定主题
    ///
    /// 所有订阅了该MessageType的接收者都会收到消息
    pub async fn publish(&self, msg_type: MessageType, message: AgentMessage) -> anyhow::Result<()> {
        let subscribers = self.subscribers.lock().await;
        if let Some(tx) = subscribers.get(&msg_type) {
            match tx.send(message) {
                Ok(n) => {
                    debug!(msg_type = %msg_type, receivers = n, "消息已发布");
                }
                Err(e) => {
                    warn!(msg_type = %msg_type, error = %e, "消息发布失败(无接收者)");
                }
            }
        } else {
            warn!(msg_type = %msg_type, "未知的消息类型");
        }
        Ok(())
    }

    /// 订阅指定消息类型
    ///
    /// 返回一个broadcast::Receiver，用于接收该类型的消息
    pub async fn subscribe(
        &self,
        msg_type: MessageType,
    ) -> anyhow::Result<broadcast::Receiver<AgentMessage>> {
        let subscribers = self.subscribers.lock().await;
        if let Some(tx) = subscribers.get(&msg_type) {
            Ok(tx.subscribe())
        } else {
            anyhow::bail!("未知的消息类型: {}", msg_type);
        }
    }

    /// 发送点对点消息到指定智能体
    pub async fn send_to_agent(
        &self,
        agent_id: &str,
        message: AgentMessage,
    ) -> anyhow::Result<()> {
        let queues = self.agent_queues.lock().await;
        if let Some(tx) = queues.get(agent_id) {
            tx.send(message)
                .await
                .map_err(|e| anyhow::anyhow!("发送消息到智能体 {} 失败: {}", agent_id, e))?;
            debug!(agent_id = agent_id, "点对点消息已发送");
        } else {
            warn!(agent_id = agent_id, "智能体未注册，消息丢弃");
        }
        Ok(())
    }

    /// 接收指定智能体的消息
    ///
    /// 如果智能体未注册，则先注册再返回Receiver
    pub async fn receive_for_agent(
        &self,
        agent_id: &str,
    ) -> anyhow::Result<mpsc::Receiver<AgentMessage>> {
        // 智能体需要重新注册以获取新的receiver
        drop(self.receive_for_agent_inner(agent_id));
        self.register_agent(agent_id).await
    }

    async fn receive_for_agent_inner(
        &self,
        agent_id: &str,
    ) -> bool {
        let queues = self.agent_queues.lock().await;
        queues.get(agent_id).is_some()
    }

    /// 注册智能体到消息总线
    ///
    /// 为智能体创建专属的消息队列，返回Receiver
    pub async fn register_agent(
        &self,
        agent_id: &str,
    ) -> anyhow::Result<mpsc::Receiver<AgentMessage>> {
        let mut queues = self.agent_queues.lock().await;
        let (tx, rx) = mpsc::channel(self.agent_queue_capacity);
        queues.insert(agent_id.to_string(), tx);
        info!(agent_id = agent_id, "智能体已注册到消息总线");
        Ok(rx)
    }

    /// 注销智能体
    pub async fn unregister_agent(&self, agent_id: &str) -> anyhow::Result<()> {
        let mut queues = self.agent_queues.lock().await;
        if queues.remove(agent_id).is_some() {
            info!(agent_id = agent_id, "智能体已从消息总线注销");
        } else {
            warn!(agent_id = agent_id, "智能体未注册，注销失败");
        }
        Ok(())
    }

    /// 获取当前已注册的智能体数量
    pub async fn agent_count(&self) -> usize {
        let queues = self.agent_queues.lock().await;
        queues.len()
    }

    /// 广播消息到所有智能体
    pub async fn broadcast_to_all(&self, message: AgentMessage) -> anyhow::Result<()> {
        let queues = self.agent_queues.lock().await;
        let mut failed = Vec::new();
        for (agent_id, tx) in queues.iter() {
            if tx.send(message.clone()).await.is_err() {
                failed.push(agent_id.clone());
            }
        }
        if !failed.is_empty() {
            warn!(failed_agents = ?failed, "部分智能体消息发送失败");
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use finhack_core::types::MessagePriority;
    use serde_json::json;

    #[tokio::test]
    async fn test_publish_subscribe() {
        let bus = MessageBus::default();
        let mut rx = bus.subscribe(MessageType::TradeSignal).await.unwrap();

        let msg = AgentMessage::new(
            "strategy",
            "",
            MessageType::TradeSignal,
            json!({"action": "buy"}),
        );

        bus.publish(MessageType::TradeSignal, msg.clone())
            .await
            .unwrap();

        let received = rx.recv().await.unwrap();
        assert_eq!(received.sender, "strategy");
    }

    #[tokio::test]
    async fn test_agent_registration() {
        let bus = MessageBus::default();
        let rx = bus.register_agent("agent_1").await.unwrap();
        assert_eq!(bus.agent_count().await, 1);

        let msg = AgentMessage::new(
            "system",
            "agent_1",
            MessageType::ControlCommand,
            json!({"cmd": "start"}),
        );

        bus.send_to_agent("agent_1", msg).await.unwrap();
        drop(rx); // 显式drop以避免unused warning
    }
}
