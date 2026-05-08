//! WebSocket实时推送
//!
//! 提供WebSocket连接管理，用于实时推送行情数据、交易信号等

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    response::IntoResponse,
};
use finhack_core::types::{AgentMessage, MessageType};
use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::mpsc;
use tracing::{debug, info, warn};

use crate::server::AppState;

/// WebSocket升级处理
pub async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_ws_connection(socket, state))
}

/// 处理WebSocket连接
async fn handle_ws_connection(socket: WebSocket, state: Arc<AppState>) {
    info!("WebSocket客户端已连接");

    // 创建消息发送通道
    let (mut ws_sender, mut ws_receiver) = socket.split();

    // 订阅消息总线
    let bus = state.bus.read().await;
    let market_rx = match bus.subscribe(MessageType::MarketData).await {
        Ok(rx) => rx,
        Err(e) => {
            warn!(error = %e, "订阅行情数据失败");
            return;
        }
    };
    drop(bus); // 释放读锁

    // 创建内部通道用于转发消息
    let (tx, mut rx) = mpsc::channel::<AgentMessage>(100);

    // 启动消息转发任务
    let mut market_rx = market_rx;
    tokio::spawn(async move {
        loop {
            match market_rx.recv().await {
                Ok(msg) => {
                    if tx.send(msg).await.is_err() {
                        break;
                    }
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    warn!(skipped = n, "WebSocket消息处理落后，部分消息被跳过");
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                    debug!("消息总线通道已关闭");
                    break;
                }
            }
        }
    });

    // 处理接收和发送
    loop {
        tokio::select! {
            // 接收来自客户端的消息
            Some(result) = ws_receiver.next() => {
                match result {
                    Ok(Message::Text(text)) => {
                        if let Ok(msg) = serde_json::from_str::<serde_json::Value>(&text) {
                            debug!(message = %msg, "收到WebSocket消息");
                            // TODO: 处理客户端命令
                        }
                    }
                    Ok(Message::Close(_)) => {
                        info!("WebSocket客户端断开连接");
                        break;
                    }
                    Err(e) => {
                        warn!(error = %e, "WebSocket接收错误");
                        break;
                    }
                    _ => {}
                }
            }

            // 发送行情数据到客户端
            Some(msg) = rx.recv() => {
                let payload = json!({
                    "type": msg.msg_type.to_string(),
                    "sender": msg.sender,
                    "data": msg.payload,
                    "timestamp": msg.timestamp.to_rfc3339(),
                });

                let text = serde_json::to_string(&payload).unwrap_or_default();
                if ws_sender.send(Message::Text(text.into())).await.is_err() {
                    warn!("WebSocket发送失败");
                    break;
                }
            }
        }
    }

    info!("WebSocket连接已关闭");
}

/// 向所有WebSocket客户端广播消息
pub async fn broadcast_message(bus: &std::sync::Arc<tokio::sync::RwLock<finhack_bus::MessageBus>>, msg: AgentMessage) {
    let bus = bus.read().await;
    if let Err(e) = bus.publish(msg.msg_type, msg).await {
        warn!(error = %e, "广播消息失败");
    }
}
