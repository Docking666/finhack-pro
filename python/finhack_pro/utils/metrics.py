"""
可观测性模块 - Observability

提供 Prometheus 指标收集、OpenTelemetry 追踪等可观测性功能。

Usage:
    from finhack_pro.utils.metrics import get_metrics, track_agent_call
    
    # 获取指标收集器
    metrics = get_metrics()
    
    # 记录 Agent 调用
    with track_agent_call("market_analyzer"):
        await agent.analyze(...)
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from functools import wraps
import logging

logger = logging.getLogger(__name__)


@dataclass
class MetricValue:
    """指标值"""
    name: str
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """指标收集器
    
    收集和暴露系统运行指标，支持 Prometheus 格式输出。
    
    指标类型:
    - Counter: 只增计数器
    - Gauge: 可增可减仪表
    - Histogram: 直方图（分布统计）
    """
    
    def __init__(self, namespace: str = "finhack"):
        """初始化指标收集器
        
        Args:
            namespace: 指标命名空间，用于 Prometheus 输出
        """
        self._namespace = namespace
        self._counters: Dict[str, Dict[str, float]] = {}
        self._gauges: Dict[str, Dict[str, float]] = {}
        self._histograms: Dict[str, Dict[str, List[float]]] = {}
        self._histogram_buckets = [0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
        
        # 预定义系统指标
        self._define_system_metrics()
    
    def _define_system_metrics(self) -> None:
        """定义系统级指标"""
        # Agent 指标
        self._counters["agent_calls_total"] = {}
        self._counters["agent_errors_total"] = {}
        self._histograms["agent_duration_seconds"] = {}
        
        # LLM 指标
        self._counters["llm_calls_total"] = {}
        self._counters["llm_tokens_total"] = {}
        self._counters["llm_cost_dollars_total"] = {}
        self._histograms["llm_duration_seconds"] = {}
        
        # 信号处理指标
        self._counters["signals_processed_total"] = {}
        self._histograms["signal_processing_seconds"] = {}
        
        # 记忆系统指标
        self._counters["memory_operations_total"] = {}
        self._gauges["memory_entries"] = {}
        
        # WebSocket 指标
        self._gauges["websocket_connections"] = {}
        self._counters["websocket_messages_total"] = {}
    
    def _make_label_key(self, labels: Dict[str, str]) -> str:
        """生成标签键"""
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    
    def counter(self, name: str, labels: Optional[Dict[str, str]] = None, value: float = 1.0) -> None:
        """增加计数器
        
        Args:
            name: 指标名称
            labels: 标签键值对
            value: 增加值
        """
        labels = labels or {}
        label_key = self._make_label_key(labels)
        
        if name not in self._counters:
            self._counters[name] = {}
        
        full_key = f"{name}{{{label_key}}}" if label_key else name
        self._counters[name][label_key] = self._counters[name].get(label_key, 0) + value
    
    def gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """设置仪表值
        
        Args:
            name: 指标名称
            value: 当前值
            labels: 标签键值对
        """
        labels = labels or {}
        label_key = self._make_label_key(labels)
        
        if name not in self._gauges:
            self._gauges[name] = {}
        
        self._gauges[name][label_key] = value
    
    def histogram(self, name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """记录直方图值
        
        Args:
            name: 指标名称
            value: 观测值
            labels: 标签键值对
        """
        labels = labels or {}
        label_key = self._make_label_key(labels)
        
        if name not in self._histograms:
            self._histograms[name] = {}
        
        if label_key not in self._histograms[name]:
            self._histograms[name][label_key] = []
        
        self._histograms[name][label_key].append(value)
        
        # 保持最近1000个值
        if len(self._histograms[name][label_key]) > 1000:
            self._histograms[name][label_key] = self._histograms[name][label_key][-1000:]
    
    def observe_duration(self, name: str, duration: float, labels: Optional[Dict[str, str]] = None) -> None:
        """记录持续时间（直方图）"""
        self.histogram(name, duration, labels)
    
    def get_counter(self, name: str, labels: Optional[Dict[str, str]] = None) -> float:
        """获取计数器值"""
        labels = labels or {}
        label_key = self._make_label_key(labels)
        return self._counters.get(name, {}).get(label_key, 0)
    
    def get_gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> float:
        """获取仪表值"""
        labels = labels or {}
        label_key = self._make_label_key(labels)
        return self._gauges.get(name, {}).get(label_key, 0)
    
    def get_histogram_stats(self, name: str, labels: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        """获取直方图统计"""
        labels = labels or {}
        label_key = self._make_label_key(labels)
        values = self._histograms.get(name, {}).get(label_key, [])
        
        if not values:
            return {"count": 0, "sum": 0, "avg": 0, "p50": 0, "p95": 0, "p99": 0}
        
        import statistics
        sorted_values = sorted(values)
        n = len(sorted_values)
        
        return {
            "count": n,
            "sum": sum(values),
            "avg": statistics.mean(values),
            "p50": sorted_values[int(n * 0.5)] if n > 0 else 0,
            "p95": sorted_values[int(n * 0.95)] if n > 0 else 0,
            "p99": sorted_values[int(n * 0.99)] if n > 0 else 0,
        }
    
    def export_prometheus(self) -> str:
        """导出 Prometheus 格式指标
        
        Returns:
            Prometheus 文本格式字符串
        """
        lines = []
        
        # 导出计数器
        for name, values in self._counters.items():
            metric_name = f"{self._namespace}_{name}"
            lines.append(f"# TYPE {metric_name} counter")
            lines.append(f"# HELP {metric_name} Counter metric")
            for label_key, value in values.items():
                if label_key:
                    lines.append(f"{metric_name}{{{label_key}}} {value}")
                else:
                    lines.append(f"{metric_name} {value}")
            lines.append("")
        
        # 导出仪表
        for name, values in self._gauges.items():
            metric_name = f"{self._namespace}_{name}"
            lines.append(f"# TYPE {metric_name} gauge")
            lines.append(f"# HELP {metric_name} Gauge metric")
            for label_key, value in values.items():
                if label_key:
                    lines.append(f"{metric_name}{{{label_key}}} {value}")
                else:
                    lines.append(f"{metric_name} {value}")
            lines.append("")
        
        # 导出直方图
        for name, values in self._histograms.items():
            metric_name = f"{self._namespace}_{name}"
            lines.append(f"# TYPE {metric_name} histogram")
            lines.append(f"# HELP {metric_name} Histogram metric")
            for label_key, observations in values.items():
                if not observations:
                    continue
                
                sorted_obs = sorted(observations)
                total = len(sorted_obs)
                sum_val = sum(observations)
                
                # 计算桶
                for bucket in self._histogram_buckets:
                    count = sum(1 for v in sorted_obs if v <= bucket)
                    bucket_label = f'{label_key},le="{bucket}"' if label_key else f'le="{bucket}"'
                    lines.append(f"{metric_name}_bucket{{{bucket_label}}} {count}")
                
                # +Inf 桶
                inf_label = f'{label_key},le="+Inf"' if label_key else 'le="+Inf"'
                lines.append(f"{metric_name}_bucket{{{inf_label}}} {total}")
                
                # sum 和 count
                if label_key:
                    lines.append(f"{metric_name}_sum{{{label_key}}} {sum_val}")
                    lines.append(f"{metric_name}_count{{{label_key}}} {total}")
                else:
                    lines.append(f"{metric_name}_sum {sum_val}")
                    lines.append(f"{metric_name}_count {total}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    def get_all_metrics(self) -> Dict[str, Any]:
        """获取所有指标摘要"""
        return {
            "counters": {
                name: dict(values) 
                for name, values in self._counters.items() if values
            },
            "gauges": {
                name: dict(values) 
                for name, values in self._gauges.items() if values
            },
            "histograms": {
                name: {
                    label_key: self.get_histogram_stats(name, {"_": label_key.split(",")[0].split("=")[1] if "=" in label_key else ""} if label_key else None)
                    for label_key in values
                }
                for name, values in self._histograms.items() if values
            },
        }


# 全局指标收集器
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """获取全局指标收集器"""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


@contextmanager
def track_agent_call(agent_name: str):
    """追踪 Agent 调用的上下文管理器
    
    Usage:
        with track_agent_call("market_analyzer"):
            await agent.analyze(...)
    """
    metrics = get_metrics()
    start_time = time.time()
    error = None
    
    try:
        yield
    except Exception as e:
        error = e
        metrics.counter("agent_errors_total", {"agent": agent_name})
        raise
    finally:
        duration = time.time() - start_time
        metrics.counter("agent_calls_total", {"agent": agent_name})
        metrics.observe_duration("agent_duration_seconds", duration, {"agent": agent_name})
        
        if error is None:
            logger.debug(f"[Metrics] Agent {agent_name} completed in {duration:.3f}s")
        else:
            logger.debug(f"[Metrics] Agent {agent_name} failed after {duration:.3f}s: {error}")


@contextmanager
def track_llm_call(model: str, provider: str = "openai"):
    """追踪 LLM 调用的上下文管理器"""
    metrics = get_metrics()
    start_time = time.time()
    error = None
    
    try:
        yield
    except Exception as e:
        error = e
        metrics.counter("llm_errors_total", {"model": model, "provider": provider})
        raise
    finally:
        duration = time.time() - start_time
        metrics.counter("llm_calls_total", {"model": model, "provider": provider})
        metrics.observe_duration("llm_duration_seconds", duration, {"model": model, "provider": provider})


def track_llm_tokens(model: str, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
    """记录 LLM Token 使用量"""
    metrics = get_metrics()
    metrics.counter("llm_tokens_total", {"model": model, "type": "prompt"}, prompt_tokens)
    metrics.counter("llm_tokens_total", {"model": model, "type": "completion"}, completion_tokens)
    metrics.counter("llm_cost_dollars_total", {"model": model}, cost)


def track_signal_processing(strategy: str, signal_count: int, duration: float) -> None:
    """记录信号处理"""
    metrics = get_metrics()
    metrics.counter("signals_processed_total", {"strategy": strategy}, signal_count)
    metrics.observe_duration("signal_processing_seconds", duration, {"strategy": strategy})


def track_memory_operation(operation: str, success: bool = True) -> None:
    """记录记忆操作"""
    metrics = get_metrics()
    metrics.counter("memory_operations_total", {
        "operation": operation,
        "status": "success" if success else "failure"
    })


def update_memory_entries(count: int, memory_type: Optional[str] = None) -> None:
    """更新记忆条目数"""
    metrics = get_metrics()
    labels = {"type": memory_type} if memory_type else {}
    metrics.gauge("memory_entries", count, labels)


def update_websocket_connections(channel: str, count: int) -> None:
    """更新 WebSocket 连接数"""
    metrics = get_metrics()
    metrics.gauge("websocket_connections", count, {"channel": channel})


def track_websocket_message(channel: str, direction: str = "out") -> None:
    """记录 WebSocket 消息"""
    metrics = get_metrics()
    metrics.counter("websocket_messages_total", {"channel": channel, "direction": direction})
