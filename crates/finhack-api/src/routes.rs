//! API路由定义
//!
//! 定义所有HTTP API端点

use axum::{
    extract::State,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use finhack_core::types::{BacktestResult, Order, OrderSide, OrderType, Portfolio};
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tracing::{debug, info};

use crate::server::AppState;

// ============================================================================
// 请求/响应类型
// ============================================================================

/// 回测请求
#[derive(Debug, Deserialize)]
pub struct BacktestRequest {
    /// 标的列表
    pub symbols: Vec<String>,
    /// 策略名称
    pub strategy: String,
    /// 开始日期
    pub start_date: String,
    /// 结束日期
    pub end_date: String,
    /// 初始资金
    pub initial_capital: Option<f64>,
    /// 策略参数
    pub params: Option<Value>,
}

/// 下单请求
#[derive(Debug, Deserialize)]
pub struct OrderRequest {
    /// 标的代码
    pub symbol: String,
    /// 方向: buy / sell
    pub side: String,
    /// 订单类型: market / limit / stop
    pub order_type: String,
    /// 价格(限价单必填)
    pub price: Option<f64>,
    /// 数量(股)
    pub volume: i64,
    /// 策略名称
    pub strategy_name: Option<String>,
}

/// 通用API响应
#[derive(Debug, Serialize)]
pub struct ApiResponse<T: Serialize> {
    pub code: i32,
    pub message: String,
    pub data: Option<T>,
}

impl<T: Serialize> ApiResponse<T> {
    /// 成功响应
    pub fn ok(data: T) -> Self {
        Self {
            code: 0,
            message: "success".to_string(),
            data: Some(data),
        }
    }

    /// 错误响应
    pub fn error(code: i32, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            data: None,
        }
    }
}

/// 智能体状态
#[derive(Debug, Serialize)]
pub struct AgentStatus {
    pub agent_id: String,
    pub name: String,
    pub status: String,
    pub last_heartbeat: String,
}

/// 系统信息
#[derive(Debug, Serialize)]
pub struct SystemInfo {
    pub name: String,
    pub version: String,
    pub mode: String,
    pub uptime_seconds: u64,
}

// ============================================================================
// 路由构建
// ============================================================================

/// 构建API路由
pub fn build_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/api/health", get(health_check))
        .route("/api/system/info", get(system_info))
        .route("/api/backtest", post(run_backtest))
        .route("/api/portfolio", get(get_portfolio))
        .route("/api/order", post(place_order))
        .route("/api/agents/status", get(get_agents_status))
        .route("/ws/market", get(crate::ws::ws_handler))
        .with_state(state)
}

// ============================================================================
// 处理函数
// ============================================================================

/// 健康检查
async fn health_check() -> impl IntoResponse {
    Json(ApiResponse::ok(json!({
        "status": "healthy",
        "timestamp": chrono::Utc::now().to_rfc3339(),
    })))
}

/// 获取系统信息
async fn system_info(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let info = SystemInfo {
        name: state.config.system.name.clone(),
        version: state.config.system.version.clone(),
        mode: state.config.system.mode.clone(),
        uptime_seconds: state.start_time.elapsed().as_secs(),
    };
    Json(ApiResponse::ok(info))
}

/// 运行回测
async fn run_backtest(
    State(_state): State<Arc<AppState>>,
    Json(req): Json<BacktestRequest>,
) -> impl IntoResponse {
    info!(
        symbols = ?req.symbols,
        strategy = %req.strategy,
        "收到回测请求"
    );

    // TODO: 实际调用回测引擎
    // 这里返回模拟结果
    let result = BacktestResult::new();

    Json(ApiResponse::ok(result))
}

/// 获取投资组合
async fn get_portfolio(State(_state): State<Arc<AppState>>) -> impl IntoResponse {
    // TODO: 返回实际组合数据
    let portfolio = Portfolio::new(Decimal::from(1000000u64));
    Json(ApiResponse::ok(portfolio))
}

/// 下单
async fn place_order(
    State(_state): State<Arc<AppState>>,
    Json(req): Json<OrderRequest>,
) -> impl IntoResponse {
    info!(
        symbol = %req.symbol,
        side = %req.side,
        volume = req.volume,
        "收到下单请求"
    );

    // 解析订单方向
    let side = match req.side.to_lowercase().as_str() {
        "buy" => OrderSide::Buy,
        "sell" => OrderSide::Sell,
        _ => {
            return Json(ApiResponse::<Value>::error(400, "无效的订单方向"));
        }
    };

    // 解析订单类型
    let order_type = match req.order_type.to_lowercase().as_str() {
        "market" => OrderType::Market,
        "limit" => OrderType::Limit,
        "stop" => OrderType::Stop,
        _ => {
            return Json(ApiResponse::<Value>::error(400, "无效的订单类型"));
        }
    };

    let price = req
        .price
        .map(|p| Decimal::from_f64_retain(p).unwrap_or_default())
        .unwrap_or_default();

    let strategy_name = req.strategy_name.unwrap_or_else(|| "manual".to_string());

    let order = Order::new(&req.symbol, side, order_type, price, req.volume, strategy_name);

    // TODO: 通过消息总线发送订单到执行引擎
    debug!(order_id = %order.id, "订单已创建");

    // 将Order序列化为JSON返回
    let order_json = serde_json::to_value(&order).unwrap_or(json!({"id": order.id}));
    Json(ApiResponse::ok(order_json))
}

/// 获取智能体状态
async fn get_agents_status(State(_state): State<Arc<AppState>>) -> impl IntoResponse {
    let agents = vec![AgentStatus {
        agent_id: "market_analyzer".to_string(),
        name: "市场分析智能体".to_string(),
        status: "running".to_string(),
        last_heartbeat: chrono::Utc::now().to_rfc3339(),
    }];

    Json(ApiResponse::ok(agents))
}
