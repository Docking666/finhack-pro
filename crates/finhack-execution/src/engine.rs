//! 执行引擎
//!
//! 支持多种算法执行策略:
//! - TWAP (时间加权平均价格)
//! - VWAP (成交量加权平均价格)
//! - 冰山指令 (隐藏大单)

use finhack_core::config::ExecutionConfig;
use finhack_core::types::{Order, OrderSide, Signal};
use tracing::info;

/// 执行算法类型
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionAlgorithm {
    /// 时间加权平均价格
    Twap,
    /// 成交量加权平均价格
    Vwap,
    /// 冰山指令
    Iceberg,
}

impl std::fmt::Display for ExecutionAlgorithm {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ExecutionAlgorithm::Twap => write!(f, "TWAP"),
            ExecutionAlgorithm::Vwap => write!(f, "VWAP"),
            ExecutionAlgorithm::Iceberg => write!(f, "ICEBERG"),
        }
    }
}

impl std::str::FromStr for ExecutionAlgorithm {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "twap" => Ok(ExecutionAlgorithm::Twap),
            "vwap" => Ok(ExecutionAlgorithm::Vwap),
            "iceberg" => Ok(ExecutionAlgorithm::Iceberg),
            _ => Err(format!("未知的执行算法: {}", s)),
        }
    }
}

/// 执行引擎
///
/// 负责将交易信号转换为订单并执行
pub struct ExecutionEngine {
    /// 执行配置
    config: ExecutionConfig,
    /// 执行算法
    algorithm: ExecutionAlgorithm,
}

impl ExecutionEngine {
    /// 创建新的执行引擎
    pub fn new(config: ExecutionConfig) -> Self {
        let algorithm = config
            .algorithm
            .parse()
            .unwrap_or(ExecutionAlgorithm::Twap);
        info!(algorithm = %algorithm, "执行引擎已初始化");
        Self { config, algorithm }
    }

    /// 将信号转换为订单
    pub fn signal_to_order(&self, signal: &Signal) -> Order {
        let side = match signal.direction {
            finhack_core::types::SignalDirection::Buy => OrderSide::Buy,
            finhack_core::types::SignalDirection::Sell => OrderSide::Sell,
            finhack_core::types::SignalDirection::Close => {
                // 平仓信号需要根据当前持仓方向决定
                // 这里简化为卖出
                OrderSide::Sell
            }
        };

        Order::new(
            &signal.symbol,
            side,
            signal.order_type,
            signal.price,
            signal.volume,
            &signal.strategy_name,
        )
    }

    /// 获取当前执行算法
    pub fn algorithm(&self) -> ExecutionAlgorithm {
        self.algorithm
    }

    /// 获取滑点(基点)
    pub fn slippage_bps(&self) -> u32 {
        self.config.slippage_bps
    }
}
