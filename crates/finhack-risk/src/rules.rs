//! 风控规则定义
//!
//! 定义各种风控规则及其检查逻辑

use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};

/// 风控规则类型
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RuleType {
    /// 单标的仓位限制
    MaxPositionPct,
    /// 总杠杆限制
    MaxLeverage,
    /// 日内亏损限制
    DailyLossLimit,
    /// 最大回撤限制
    MaxDrawdown,
    /// VaR限制
    VaRLimit,
    /// 最小交易数量
    MinTradeVolume,
    /// 价格偏离限制
    MaxPriceDeviation,
}

impl std::fmt::Display for RuleType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RuleType::MaxPositionPct => write!(f, "单标的仓位限制"),
            RuleType::MaxLeverage => write!(f, "总杠杆限制"),
            RuleType::DailyLossLimit => write!(f, "日内亏损限制"),
            RuleType::MaxDrawdown => write!(f, "最大回撤限制"),
            RuleType::VaRLimit => write!(f, "VaR限制"),
            RuleType::MinTradeVolume => write!(f, "最小交易数量"),
            RuleType::MaxPriceDeviation => write!(f, "价格偏离限制"),
        }
    }
}

/// 风控规则违反信息
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleViolation {
    /// 违反的规则类型
    pub rule_type: RuleType,
    /// 规则描述
    pub description: String,
    /// 实际值
    pub actual_value: Decimal,
    /// 限制值
    pub limit_value: Decimal,
    /// 严重程度
    pub severity: ViolationSeverity,
}

/// 违反严重程度
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ViolationSeverity {
    /// 警告(允许通过)
    Warning,
    /// 错误(拒绝订单)
    Error,
    /// 严重(停止交易)
    Critical,
}

impl RuleViolation {
    /// 创建规则违反信息
    pub fn new(
        rule_type: RuleType,
        description: impl Into<String>,
        actual_value: Decimal,
        limit_value: Decimal,
        severity: ViolationSeverity,
    ) -> Self {
        Self {
            rule_type,
            description: description.into(),
            actual_value,
            limit_value,
            severity,
        }
    }
}

/// 风控规则检查结果
#[derive(Debug, Clone)]
pub struct RuleCheckResult {
    /// 是否通过
    pub passed: bool,
    /// 违反的规则列表
    pub violations: Vec<RuleViolation>,
}

impl RuleCheckResult {
    /// 创建通过结果
    pub fn pass() -> Self {
        Self {
            passed: true,
            violations: Vec::new(),
        }
    }

    /// 创建失败结果
    pub fn fail(violations: Vec<RuleViolation>) -> Self {
        Self {
            passed: false,
            violations,
        }
    }
}
