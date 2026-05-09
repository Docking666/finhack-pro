"""
FinHack Pro 工具模块

提供日志配置、安全工具、熔断限流、可观测性、监控告警等辅助功能。
"""

from finhack_pro.utils.circuit_breaker import (
    BudgetExceededError,
    CircuitBreaker,
    CircuitBreakerOpenError,
    CostController,
    LLMProtection,
    RateLimitExceededError,
    TokenBucket,
    get_llm_protection,
)
from finhack_pro.utils.helpers import (
    calculate_max_drawdown,
    calculate_sharpe_ratio,
    format_number,
    generate_order_id,
    timestamp_to_datetime,
)
from finhack_pro.utils.logger import get_logger, setup_logger
from finhack_pro.utils.metrics import (
    MetricsCollector,
    get_metrics,
    track_agent_call,
    track_llm_call,
    track_llm_tokens,
    track_memory_operation,
    track_signal_processing,
    track_websocket_message,
    update_memory_entries,
    update_websocket_connections,
)
from finhack_pro.utils.monitoring import (
    Alert,
    AlertLevel,
    AlertRule,
    MetricsServer,
    MonitoringConfig,
    MonitoringService,
)
from finhack_pro.utils.security import (
    LogSanitizer,
    SecretManager,
    get_secret_manager,
    mask_secrets,
    sanitize_log,
)

__all__ = [
    # 日志
    "get_logger",
    "setup_logger",
    # 辅助函数
    "calculate_sharpe_ratio",
    "calculate_max_drawdown",
    "format_number",
    "generate_order_id",
    "timestamp_to_datetime",
    # 安全
    "SecretManager",
    "get_secret_manager",
    "mask_secrets",
    "LogSanitizer",
    "sanitize_log",
    # 熔断限流
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "TokenBucket",
    "CostController",
    "LLMProtection",
    "RateLimitExceededError",
    "BudgetExceededError",
    "get_llm_protection",
    # 可观测性
    "MetricsCollector",
    "get_metrics",
    "track_agent_call",
    "track_llm_call",
    "track_llm_tokens",
    "track_signal_processing",
    "track_memory_operation",
    "update_memory_entries",
    "update_websocket_connections",
    "track_websocket_message",
    # 监控告警
    "Alert",
    "AlertLevel",
    "AlertRule",
    "MetricsServer",
    "MonitoringConfig",
    "MonitoringService",
]
