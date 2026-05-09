"""
FinHack Pro 监控与告警模块

提供 Prometheus 指标暴露、告警规则引擎、Grafana Dashboard 生成等功能。
使用 http.server 标准库实现指标 HTTP 服务，无需额外依赖。

Usage:
    from finhack_pro.utils.monitoring import MonitoringService, MetricsServer

    service = MonitoringService()
    service.register_metric("trades_total", "counter", "Total trades")
    service.increment_counter("trades_total")

    server = MetricsServer(port=9090)
    server.start()
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional
from functools import partial

import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Enums
# ============================================================================


class AlertLevel(str, Enum):
    """告警级别"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class Alert:
    """告警"""
    alert_id: str = ""
    level: str = AlertLevel.INFO.value
    title: str = ""
    message: str = ""
    source: str = ""
    timestamp: float = 0.0
    acknowledged: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.alert_id:
            self.alert_id = str(uuid.uuid4())[:8]
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class AlertRule:
    """告警规则"""
    name: str = ""
    condition: Optional[Callable[[Dict[str, Any]], bool]] = None
    level: str = AlertLevel.WARNING.value
    cooldown_seconds: float = 300.0
    enabled: bool = True
    _last_triggered: float = 0.0

    def check(self, context: Dict[str, Any]) -> bool:
        """检查告警条件

        Args:
            context: 上下文数据

        Returns:
            是否触发告警
        """
        if not self.enabled or self.condition is None:
            return False

        now = time.time()
        if now - self._last_triggered < self.cooldown_seconds:
            return False

        try:
            triggered = self.condition(context)
            if triggered:
                self._last_triggered = now
            return triggered
        except Exception as e:
            logger.error(f"告警规则 {self.name} 检查异常: {e}")
            return False


@dataclass
class MonitoringConfig:
    """监控配置"""
    metrics_host: str = "0.0.0.0"
    metrics_port: int = 9090
    alert_cooldown: float = 300.0
    retention_hours: float = 24.0
    check_interval: float = 60.0


# ============================================================================
# MetricsServer - Prometheus HTTP Server
# ============================================================================


class _MetricsHandler(BaseHTTPRequestHandler):
    """Prometheus 指标 HTTP 请求处理器"""

    monitoring_service: Optional[MonitoringService] = None

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/metrics":
            if self.monitoring_service is not None:
                content = self.monitoring_service.export_prometheus()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.end_headers()
                self.wfile.write(content.encode("utf-8"))
            else:
                self.send_response(503)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress default request logging
        pass


class MetricsServer:
    """Prometheus 指标 HTTP 服务器

    使用标准库 http.server 实现，暴露 /metrics 端点。

    Args:
        host: 监听地址
        port: 监听端口
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 9090) -> None:
        self.host = host
        self.port = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """启动 Prometheus 指标 HTTP 服务器"""
        if self._running:
            return

        self._server = HTTPServer((self.host, self.port), _MetricsHandler)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        logger.info(f"Metrics server started on {self.host}:{self.port}")

    def stop(self) -> None:
        """停止服务器"""
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None
        logger.info("Metrics server stopped")

    def _serve(self) -> None:
        """服务循环"""
        try:
            self._server.serve_forever()
        except Exception as e:
            if self._running:
                logger.error(f"Metrics server error: {e}")

    def _handle_metrics(self) -> str:
        """处理 /metrics 请求"""
        if _MetricsHandler.monitoring_service is not None:
            return _MetricsHandler.monitoring_service.export_prometheus()
        return "# No monitoring service configured\n"


# ============================================================================
# MonitoringService - 核心监控服务
# ============================================================================


class MonitoringService:
    """监控服务

    提供指标注册、告警规则管理、Prometheus 格式导出、Grafana Dashboard 生成。

    Usage:
        service = MonitoringService()
        service.register_metric("requests_total", "counter", "Total requests")
        service.increment_counter("requests_total")
        service.set_gauge("active_connections", 42)
    """

    def __init__(self, config: Optional[MonitoringConfig] = None) -> None:
        self.config = config or MonitoringConfig()

        # 指标存储
        self._metrics: Dict[str, Dict[str, Any]] = {}
        self._metric_definitions: Dict[str, Dict[str, Any]] = {}

        # 告警
        self._alert_rules: List[AlertRule] = []
        self._alerts: List[Alert] = []
        self._alerts_lock = threading.Lock()

        # 注册内置指标和告警规则
        self._register_builtin_metrics()
        self._register_builtin_alert_rules()

    # ------------------------------------------------------------------
    # Metric Registration
    # ------------------------------------------------------------------

    def register_metric(
        self,
        name: str,
        metric_type: str,
        help_text: str = "",
        labels: Optional[List[str]] = None,
    ) -> None:
        """注册指标

        Args:
            name: 指标名称
            metric_type: 指标类型 (counter/gauge/histogram/summary)
            help_text: 帮助文本
            labels: 标签名称列表
        """
        self._metric_definitions[name] = {
            "type": metric_type,
            "help": help_text,
            "labels": labels or [],
        }

        if metric_type == "counter":
            self._metrics[name] = {}
        elif metric_type == "gauge":
            self._metrics[name] = {}
        elif metric_type == "histogram":
            self._metrics[name] = {}
            # 初始化桶
            if name not in self._metrics:
                self._metrics[name] = {}
        elif metric_type == "summary":
            self._metrics[name] = {}

    def increment_counter(self, name: str, labels: Optional[Dict[str, str]] = None, value: float = 1) -> None:
        """增加计数器

        Args:
            name: 指标名称
            labels: 标签
            value: 增加值
        """
        labels = labels or {}
        label_key = self._make_label_key(labels)

        if name not in self._metrics:
            self.register_metric(name, "counter", "")

        self._metrics[name][label_key] = self._metrics[name].get(label_key, 0) + value

    def set_gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """设置仪表值

        Args:
            name: 指标名称
            value: 值
            labels: 标签
        """
        labels = labels or {}
        label_key = self._make_label_key(labels)

        if name not in self._metrics:
            self.register_metric(name, "gauge", "")

        self._metrics[name][label_key] = value

    def observe_histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """记录直方图观测值

        Args:
            name: 指标名称
            value: 观测值
            labels: 标签
        """
        labels = labels or {}
        label_key = self._make_label_key(labels)

        if name not in self._metrics:
            self.register_metric(name, "histogram", "")

        if label_key not in self._metrics[name]:
            self._metrics[name][label_key] = []

        observations = self._metrics[name][label_key]
        if isinstance(observations, list):
            observations.append(value)
            # 保留最近 1000 个观测值
            if len(observations) > 1000:
                self._metrics[name][label_key] = observations[-1000:]

    # ------------------------------------------------------------------
    # Alert Management
    # ------------------------------------------------------------------

    def add_alert_rule(self, rule: AlertRule) -> None:
        """添加告警规则

        Args:
            rule: 告警规则
        """
        self._alert_rules.append(rule)

    def check_alerts(self, context: Optional[Dict[str, Any]] = None) -> List[Alert]:
        """评估所有告警规则

        Args:
            context: 上下文数据（包含指标值等）

        Returns:
            触发的告警列表
        """
        if context is None:
            context = self.get_metrics_snapshot()

        triggered: List[Alert] = []

        for rule in self._alert_rules:
            if rule.check(context):
                alert = Alert(
                    level=rule.level,
                    title=f"[{rule.level.upper()}] {rule.name}",
                    message=f"Alert rule '{rule.name}' triggered",
                    source="monitoring",
                    metadata={"rule": rule.name, "context": context},
                )
                triggered.append(alert)

                with self._alerts_lock:
                    self._alerts.append(alert)

        return triggered

    def get_alerts(
        self,
        level: Optional[str] = None,
        acknowledged: bool = False,
    ) -> List[Alert]:
        """获取告警列表

        Args:
            level: 过滤告警级别
            acknowledged: 是否包含已确认告警

        Returns:
            告警列表
        """
        with self._alerts_lock:
            alerts = list(self._alerts)
            if not acknowledged:
                alerts = [a for a in alerts if not a.acknowledged]
            if level:
                alerts = [a for a in alerts if a.level == level]
            return alerts

    def acknowledge_alert(self, alert_id: str) -> bool:
        """确认告警

        Args:
            alert_id: 告警 ID

        Returns:
            是否确认成功
        """
        with self._alerts_lock:
            for alert in self._alerts:
                if alert.alert_id == alert_id:
                    alert.acknowledged = True
                    return True
        return False

    # ------------------------------------------------------------------
    # Metrics Export
    # ------------------------------------------------------------------

    def get_metrics_snapshot(self) -> dict:
        """获取所有当前指标值

        Returns:
            指标快照字典
        """
        snapshot: Dict[str, Any] = {}
        namespace = "finhack"

        for name, label_data in self._metrics.items():
            definition = self._metric_definitions.get(name, {})
            metric_type = definition.get("type", "gauge")

            if metric_type in ("counter", "gauge"):
                for label_key, value in label_data.items():
                    key = f"{namespace}_{name}{{{label_key}}}" if label_key else f"{namespace}_{name}"
                    snapshot[key] = value
            elif metric_type == "histogram":
                for label_key, observations in label_data.items():
                    if isinstance(observations, list) and observations:
                        key = f"{namespace}_{name}{{{label_key}}}" if label_key else f"{namespace}_{name}"
                        snapshot[f"{key}_count"] = len(observations)
                        snapshot[f"{key}_sum"] = sum(observations)
                        sorted_obs = sorted(observations)
                        n = len(sorted_obs)
                        snapshot[f"{key}_avg"] = sum(observations) / n
                        snapshot[f"{key}_p50"] = sorted_obs[int(n * 0.5)]
                        snapshot[f"{key}_p95"] = sorted_obs[int(n * 0.95)]
                        snapshot[f"{key}_p99"] = sorted_obs[min(int(n * 0.99), n - 1)]

        return snapshot

    def export_prometheus(self) -> str:
        """导出 Prometheus 文本格式

        Returns:
            Prometheus 格式字符串
        """
        lines: List[str] = []
        namespace = "finhack"

        for name, definition in self._metric_definitions.items():
            metric_type = definition.get("type", "gauge")
            help_text = definition.get("help", "")
            full_name = f"{namespace}_{name}"

            lines.append(f"# HELP {full_name} {help_text}")
            lines.append(f"# TYPE {full_name} {metric_type}")

            label_data = self._metrics.get(name, {})

            if metric_type in ("counter", "gauge"):
                for label_key, value in label_data.items():
                    if label_key:
                        lines.append(f"{full_name}{{{label_key}}} {value}")
                    else:
                        lines.append(f"{full_name} {value}")

            elif metric_type == "histogram":
                buckets = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
                for label_key, observations in label_data.items():
                    if not isinstance(observations, list) or not observations:
                        continue

                    sorted_obs = sorted(observations)
                    total = len(sorted_obs)
                    sum_val = sum(observations)

                    for bucket in buckets:
                        count = sum(1 for v in sorted_obs if v <= bucket)
                        bucket_label = f'{label_key},le="{bucket}"' if label_key else f'le="{bucket}"'
                        lines.append(f"{full_name}_bucket{{{bucket_label}}} {count}")

                    # +Inf bucket
                    inf_label = f'{label_key},le="+Inf"' if label_key else 'le="+Inf"'
                    lines.append(f"{full_name}_bucket{{{inf_label}}} {total}")

                    if label_key:
                        lines.append(f"{full_name}_sum{{{label_key}}} {sum_val}")
                        lines.append(f"{full_name}_count{{{label_key}}} {total}")
                    else:
                        lines.append(f"{full_name}_sum {sum_val}")
                        lines.append(f"{full_name}_count {total}")

            lines.append("")

        return "\n".join(lines)

    def export_grafana_dashboard(self) -> dict:
        """生成 Grafana Dashboard JSON

        Returns:
            可导入 Grafana 的 Dashboard JSON
        """
        dashboard = {
            "uid": str(uuid.uuid4())[:8],
            "title": "FinHack Pro Trading Dashboard",
            "description": "FinHack Pro 量化交易系统监控面板",
            "tags": ["finhack", "trading"],
            "timezone": "browser",
            "schemaVersion": 30,
            "version": 1,
            "refresh": "10s",
            "time": {"from": "now-1h", "to": "now"},
            "templating": {
                "list": [
                    {
                        "name": "datasource",
                        "type": "datasource",
                        "query": "prometheus",
                        "current": {"selected": True, "text": "Prometheus", "value": "Prometheus"},
                    }
                ]
            },
            "panels": [
                # Equity Curve
                {
                    "id": 1,
                    "title": "Equity Curve",
                    "type": "timeseries",
                    "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": "finhack_total_equity",
                            "legendFormat": "Total Equity",
                        },
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": "finhack_available_cash",
                            "legendFormat": "Available Cash",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "palette-classic"},
                            "unit": "currencyCNY",
                        },
                        "overrides": [],
                    },
                },
                # Drawdown
                {
                    "id": 2,
                    "title": "Drawdown",
                    "type": "timeseries",
                    "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": "finhack_drawdown_pct",
                            "legendFormat": "Drawdown %",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "thresholds"},
                            "thresholds": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "green", "value": None},
                                    {"color": "yellow", "value": 5},
                                    {"color": "red", "value": 15},
                                ],
                            },
                            "unit": "percent",
                        },
                        "overrides": [],
                    },
                },
                # Trade Count
                {
                    "id": 3,
                    "title": "Trade Count",
                    "type": "stat",
                    "gridPos": {"h": 4, "w": 6, "x": 0, "y": 8},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": "finhack_trades_total",
                            "legendFormat": "Total Trades",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "thresholds"},
                            "thresholds": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "green", "value": None},
                                ],
                            },
                        },
                        "overrides": [],
                    },
                },
                # Win Rate
                {
                    "id": 4,
                    "title": "Win Rate",
                    "type": "gauge",
                    "gridPos": {"h": 4, "w": 6, "x": 6, "y": 8},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": "finhack_win_rate",
                            "legendFormat": "Win Rate",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "thresholds"},
                            "thresholds": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "red", "value": None},
                                    {"color": "yellow", "value": 40},
                                    {"color": "green", "value": 55},
                                ],
                            },
                            "unit": "percent",
                            "min": 0,
                            "max": 100,
                        },
                        "overrides": [],
                    },
                },
                # API Latency
                {
                    "id": 5,
                    "title": "API Latency",
                    "type": "timeseries",
                    "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": 'histogram_quantile(0.95, rate(finhack_api_latency_seconds_bucket[5m]))',
                            "legendFormat": "p95 Latency",
                        },
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": 'histogram_quantile(0.50, rate(finhack_api_latency_seconds_bucket[5m]))',
                            "legendFormat": "p50 Latency",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "palette-classic"},
                            "unit": "s",
                        },
                        "overrides": [],
                    },
                },
                # Error Rate
                {
                    "id": 6,
                    "title": "Error Rate",
                    "type": "timeseries",
                    "gridPos": {"h": 8, "w": 12, "x": 12, "y": 12},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": 'rate(finhack_api_errors_total[5m]) / rate(finhack_api_requests_total[5m]) * 100',
                            "legendFormat": "Error Rate %",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "thresholds"},
                            "thresholds": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "green", "value": None},
                                    {"color": "yellow", "value": 2},
                                    {"color": "red", "value": 5},
                                ],
                            },
                            "unit": "percent",
                        },
                        "overrides": [],
                    },
                },
                # Realized PnL
                {
                    "id": 7,
                    "title": "Realized PnL",
                    "type": "stat",
                    "gridPos": {"h": 4, "w": 6, "x": 12, "y": 8},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": "finhack_realized_pnl",
                            "legendFormat": "Realized PnL",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "thresholds"},
                            "thresholds": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "red", "value": None},
                                    {"color": "green", "value": 0},
                                ],
                            },
                            "unit": "currencyCNY",
                        },
                        "overrides": [],
                    },
                },
                # Memory Usage
                {
                    "id": 8,
                    "title": "Memory Usage",
                    "type": "gauge",
                    "gridPos": {"h": 4, "w": 6, "x": 0, "y": 20},
                    "targets": [
                        {
                            "datasource": {"type": "prometheus", "uid": "${datasource}"},
                            "expr": "finhack_memory_usage_pct",
                            "legendFormat": "Memory %",
                        },
                    ],
                    "fieldConfig": {
                        "defaults": {
                            "color": {"mode": "thresholds"},
                            "thresholds": {
                                "mode": "absolute",
                                "steps": [
                                    {"color": "green", "value": None},
                                    {"color": "yellow", "value": 60},
                                    {"color": "red", "value": 80},
                                ],
                            },
                            "unit": "percent",
                            "min": 0,
                            "max": 100,
                        },
                        "overrides": [],
                    },
                },
            ],
        }

        return {
            "__inputs": [
                {
                    "name": "DS_PROMETHEUS",
                    "label": "Prometheus",
                    "description": "",
                    "type": "datasource",
                    "pluginId": "prometheus",
                    "pluginName": "Prometheus",
                }
            ],
            "__requires": [
                {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "9.0.0"},
                {"type": "datasource", "id": "prometheus", "name": "Prometheus", "version": "1.0.0"},
                {"type": "panel", "id": "timeseries", "name": "Time series", "version": ""},
                {"type": "panel", "id": "stat", "name": "Stat", "version": ""},
                {"type": "panel", "id": "gauge", "name": "Gauge", "version": ""},
            ],
            "annotations": {"list": []},
            "editable": True,
            "fiscalYearStartMonth": 0,
            "graphTooltip": 1,
            "id": None,
            "links": [],
            "liveNow": False,
            "panels": dashboard["panels"],
            "refresh": dashboard["refresh"],
            "schemaVersion": dashboard["schemaVersion"],
            "tags": dashboard["tags"],
            "templating": dashboard["templating"],
            "time": dashboard["time"],
            "timepicker": {},
            "timezone": dashboard["timezone"],
            "title": dashboard["title"],
            "uid": dashboard["uid"],
            "version": dashboard["version"],
            "description": dashboard["description"],
        }

    # ------------------------------------------------------------------
    # Built-in Metrics & Alert Rules
    # ------------------------------------------------------------------

    def _register_builtin_metrics(self) -> None:
        """注册内置指标"""
        builtin_metrics = [
            ("total_equity", "gauge", "Total account equity"),
            ("available_cash", "gauge", "Available cash"),
            ("drawdown_pct", "gauge", "Current drawdown percentage"),
            ("trades_total", "counter", "Total number of trades"),
            ("win_rate", "gauge", "Win rate percentage"),
            ("realized_pnl", "gauge", "Realized profit and loss"),
            ("unrealized_pnl", "gauge", "Unrealized profit and loss"),
            ("api_requests_total", "counter", "Total API requests"),
            ("api_errors_total", "counter", "Total API errors"),
            ("api_latency_seconds", "histogram", "API request latency"),
            ("memory_usage_pct", "gauge", "Memory usage percentage"),
            ("position_concentration_pct", "gauge", "Largest position concentration"),
            ("daily_pnl", "gauge", "Daily profit and loss"),
        ]
        for name, mtype, help_text in builtin_metrics:
            self.register_metric(name, mtype, help_text)

    def _register_builtin_alert_rules(self) -> None:
        """注册内置告警规则"""
        builtin_rules = [
            AlertRule(
                name="high_drawdown",
                condition=lambda ctx: ctx.get("finhack_drawdown_pct", 0) > 15,
                level=AlertLevel.CRITICAL.value,
                cooldown_seconds=300,
            ),
            AlertRule(
                name="api_error_rate_high",
                condition=lambda ctx: self._calc_error_rate(ctx) > 5,
                level=AlertLevel.WARNING.value,
                cooldown_seconds=60,
            ),
            AlertRule(
                name="memory_usage_high",
                condition=lambda ctx: ctx.get("finhack_memory_usage_pct", 0) > 80,
                level=AlertLevel.WARNING.value,
                cooldown_seconds=120,
            ),
            AlertRule(
                name="position_concentration_high",
                condition=lambda ctx: ctx.get("finhack_position_concentration_pct", 0) > 30,
                level=AlertLevel.WARNING.value,
                cooldown_seconds=300,
            ),
            AlertRule(
                name="daily_loss_limit_breach",
                condition=lambda ctx: ctx.get("finhack_daily_pnl", 0) < -50000,
                level=AlertLevel.CRITICAL.value,
                cooldown_seconds=60,
            ),
        ]
        for rule in builtin_rules:
            self.add_alert_rule(rule)

    @staticmethod
    def _calc_error_rate(context: Dict[str, Any]) -> float:
        """计算 API 错误率"""
        total = context.get("finhack_api_requests_total", 0)
        errors = context.get("finhack_api_errors_total", 0)
        if total <= 0:
            return 0.0
        return (errors / total) * 100

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_label_key(labels: Dict[str, str]) -> str:
        """生成标签键"""
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
