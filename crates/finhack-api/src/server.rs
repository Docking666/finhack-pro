//! Axum HTTP服务器
//!
//! 基于Axum构建的REST API服务器，支持HTTP和WebSocket

use crate::routes;
use finhack_bus::MessageBus;
use finhack_core::config::AppConfig;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::RwLock;
use tracing::info;

/// 应用共享状态
pub struct AppState {
    /// 应用配置
    pub config: AppConfig,
    /// 消息总线
    pub bus: Arc<RwLock<MessageBus>>,
    /// 服务器启动时间
    pub start_time: Instant,
}

/// API服务器
pub struct ApiServer {
    /// 应用状态
    state: Arc<AppState>,
    /// 监听地址
    host: String,
    /// 监听端口
    port: u16,
}

impl ApiServer {
    /// 创建新的API服务器
    pub fn new(config: AppConfig) -> Self {
        let host = config.api.host.clone();
        let port = config.api.port;

        let state = Arc::new(AppState {
            config,
            bus: Arc::new(RwLock::new(MessageBus::default())),
            start_time: Instant::now(),
        });

        Self { state, host, port }
    }

    /// 创建带自定义消息总线的API服务器
    pub fn with_bus(config: AppConfig, bus: MessageBus) -> Self {
        let host = config.api.host.clone();
        let port = config.api.port;

        let state = Arc::new(AppState {
            config,
            bus: Arc::new(RwLock::new(bus)),
            start_time: Instant::now(),
        });

        Self { state, host, port }
    }

    /// 启动服务器
    pub async fn run(&self) -> anyhow::Result<()> {
        let app = routes::build_router(Arc::clone(&self.state));

        let addr = format!("{}:{}", self.host, self.port);
        info!(addr = %addr, "API服务器启动中...");

        let listener = tokio::net::TcpListener::bind(&addr).await?;
        info!(addr = %addr, "API服务器已启动");

        axum::serve(listener, app).await?;

        Ok(())
    }

    /// 获取应用状态的引用
    pub fn state(&self) -> Arc<AppState> {
        Arc::clone(&self.state)
    }

    /// 获取监听地址
    pub fn addr(&self) -> String {
        format!("{}:{}", self.host, self.port)
    }
}
