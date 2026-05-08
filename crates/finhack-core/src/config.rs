//! 配置加载模块
//!
//! 从YAML配置文件加载系统配置，支持环境变量覆盖

use crate::error::Result;
use crate::types::TradingMode;
use rust_decimal::Decimal;
use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

// ============================================================================
// 配置结构体
// ============================================================================

/// 系统配置
#[derive(Debug, Clone, Deserialize)]
pub struct SystemConfig {
    pub name: String,
    pub version: String,
    pub mode: String,
}

/// 数据配置
#[derive(Debug, Clone, Deserialize)]
pub struct DataConfig {
    pub storage_type: String,
    pub data_dir: String,
    pub symbols: Vec<String>,
}

/// 风控配置
#[derive(Debug, Clone, Deserialize)]
pub struct RiskConfig {
    /// 单标的最大仓位占比
    pub max_position_pct: f64,
    /// 最大回撤限制
    pub max_drawdown: f64,
    /// VaR限制
    pub var_limit: f64,
    /// 最大杠杆
    pub max_leverage: f64,
    /// 日内亏损限制
    pub daily_loss_limit: f64,
}

impl RiskConfig {
    /// 获取单标的最大仓位占比(Decimal)
    pub fn max_position_pct_decimal(&self) -> Decimal {
        Decimal::try_from(self.max_position_pct).unwrap_or(Decimal::ZERO)
    }

    /// 获取最大回撤限制(Decimal)
    pub fn max_drawdown_decimal(&self) -> Decimal {
        Decimal::try_from(self.max_drawdown).unwrap_or(Decimal::ZERO)
    }

    /// 获取VaR限制(Decimal)
    pub fn var_limit_decimal(&self) -> Decimal {
        Decimal::try_from(self.var_limit).unwrap_or(Decimal::ZERO)
    }

    /// 获取最大杠杆(Decimal)
    pub fn max_leverage_decimal(&self) -> Decimal {
        Decimal::try_from(self.max_leverage).unwrap_or(Decimal::ZERO)
    }

    /// 获取日内亏损限制(Decimal)
    pub fn daily_loss_limit_decimal(&self) -> Decimal {
        Decimal::try_from(self.daily_loss_limit).unwrap_or(Decimal::ZERO)
    }
}

/// 执行配置
#[derive(Debug, Clone, Deserialize)]
pub struct ExecutionConfig {
    /// 执行算法: twap / vwap / iceberg
    pub algorithm: String,
    /// 滑点(基点)
    pub slippage_bps: u32,
    /// 佣金费率
    pub commission_rate: f64,
    /// 印花税率(仅卖出)
    pub stamp_tax_rate: f64,
}

impl ExecutionConfig {
    /// 获取佣金费率(Decimal)
    pub fn commission_rate_decimal(&self) -> Decimal {
        Decimal::try_from(self.commission_rate).unwrap_or(Decimal::ZERO)
    }

    /// 获取印花税率(Decimal)
    pub fn stamp_tax_rate_decimal(&self) -> Decimal {
        Decimal::try_from(self.stamp_tax_rate).unwrap_or(Decimal::ZERO)
    }

    /// 获取滑点(Decimal)
    pub fn slippage_decimal(&self) -> Decimal {
        Decimal::from(self.slippage_bps) / Decimal::from(10000)
    }
}

/// 回测配置
#[derive(Debug, Clone, Deserialize)]
pub struct BacktestConfig {
    /// 初始资金
    pub initial_capital: u64,
    /// 回测开始日期
    pub start_date: String,
    /// 回测结束日期
    pub end_date: String,
    /// 基准指数
    #[serde(default = "default_benchmark")]
    pub benchmark: String,
}

fn default_benchmark() -> String {
    "000300.SH".to_string()
}

impl BacktestConfig {
    /// 获取初始资金(Decimal)
    pub fn initial_capital_decimal(&self) -> Decimal {
        Decimal::from(self.initial_capital)
    }
}

/// API配置
#[derive(Debug, Clone, Deserialize)]
pub struct ApiConfig {
    pub host: String,
    pub port: u16,
}

/// 智能体配置
#[derive(Debug, Clone, Deserialize)]
pub struct AgentConfig {
    pub model: Option<String>,
    pub temperature: Option<f64>,
    pub enabled: Option<bool>,
}

/// 智能体配置集合
pub type AgentsConfig = HashMap<String, AgentConfig>;

// ============================================================================
// 总配置
// ============================================================================

/// 应用总配置
#[derive(Debug, Clone, Deserialize)]
pub struct AppConfig {
    pub system: SystemConfig,
    pub data: DataConfig,
    pub risk: RiskConfig,
    pub execution: ExecutionConfig,
    pub backtest: BacktestConfig,
    pub api: ApiConfig,
    pub agents: AgentsConfig,
}

impl AppConfig {
    /// 从YAML文件加载配置
    pub fn from_yaml_file(path: &Path) -> Result<Self> {
        let content = std::fs::read_to_string(path)?;
        Self::from_yaml_str(&content)
    }

    /// 从YAML字符串加载配置
    pub fn from_yaml_str(content: &str) -> Result<Self> {
        let config: AppConfig = serde_yaml::from_str(content)?;
        Ok(config)
    }

    /// 获取交易模式
    pub fn trading_mode(&self) -> Result<TradingMode> {
        self.system.mode.parse()
    }
}
