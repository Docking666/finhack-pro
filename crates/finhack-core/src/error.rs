//! 错误类型定义
//!
//! 定义系统中所有可能的错误类型，使用 thiserror 进行错误派生

use thiserror::Error;

/// FinHack Pro 核心错误类型
#[derive(Debug, Error)]
pub enum CoreError {
    /// 配置错误
    #[error("配置错误: {0}")]
    ConfigError(String),

    /// 数据加载错误
    #[error("数据加载错误: {0}")]
    DataError(String),

    /// 订单错误
    #[error("订单错误: {0}")]
    OrderError(String),

    /// 风控错误
    #[error("风控错误: {0}")]
    RiskError(String),

    /// 执行错误
    #[error("执行错误: {0}")]
    ExecutionError(String),

    /// 消息总线错误
    #[error("消息总线错误: {0}")]
    BusError(String),

    /// 回测错误
    #[error("回测错误: {0}")]
    BacktestError(String),

    /// API错误
    #[error("API错误: {0}")]
    ApiError(String),

    /// IO错误
    #[error("IO错误: {0}")]
    IoError(#[from] std::io::Error),

    /// 序列化/反序列化错误
    #[error("序列化错误: {0}")]
    SerializationError(String),

    /// 数值计算错误
    #[error("数值计算错误: {0}")]
    MathError(String),
}

impl From<serde_json::Error> for CoreError {
    fn from(err: serde_json::Error) -> Self {
        CoreError::SerializationError(err.to_string())
    }
}

impl From<serde_yaml::Error> for CoreError {
    fn from(err: serde_yaml::Error) -> Self {
        CoreError::SerializationError(err.to_string())
    }
}

/// 类型别名，简化错误处理
pub type Result<T> = std::result::Result<T, CoreError>;
