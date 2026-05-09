"""
熔断与限流模块

提供LLM调用的熔断保护、令牌桶限流、成本预算控制。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """熔断器状态"""
    CLOSED = "closed"      # 正常状态，允许请求
    OPEN = "open"          # 熔断状态，拒绝请求
    HALF_OPEN = "half_open"  # 半开状态，允许探测请求


@dataclass
class CircuitStats:
    """熔断器统计"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    consecutive_failures: int = 0
    last_failure_time: Optional[float] = None
    last_state_change: float = field(default_factory=time.time)


class CircuitBreaker:
    """熔断器
    
    当失败次数达到阈值时自动熔断，防止级联故障。
    
    Usage:
        breaker = CircuitBreaker(fail_max=5, reset_timeout=60)
        
        @breaker.protect
        async def call_llm():
            ...
    """
    
    def __init__(
        self,
        fail_max: int = 5,
        reset_timeout: float = 60.0,
        half_open_max_calls: int = 3,
        name: str = "default",
    ):
        """初始化熔断器
        
        Args:
            fail_max: 连续失败次数阈值，达到后熔断
            reset_timeout: 熔断后等待时间(秒)，之后进入半开状态
            half_open_max_calls: 半开状态允许的最大探测请求数
            name: 熔断器名称（用于日志）
        """
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._half_open_max_calls = half_open_max_calls
        self._name = name
        
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._half_open_calls = 0
        self._lock = asyncio.Lock()
    
    @property
    def state(self) -> CircuitState:
        """获取当前状态"""
        return self._state
    
    @property
    def stats(self) -> CircuitStats:
        """获取统计信息"""
        return self._stats
    
    def is_open(self) -> bool:
        """检查是否处于熔断状态"""
        if self._state == CircuitState.OPEN:
            # 检查是否应该进入半开状态
            if time.time() - self._stats.last_state_change >= self._reset_timeout:
                self._transition_to(CircuitState.HALF_OPEN)
                return False
            return True
        return False
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """状态转换"""
        old_state = self._state
        self._state = new_state
        self._stats.last_state_change = time.time()
        self._half_open_calls = 0
        logger.info(f"[CircuitBreaker:{self._name}] 状态转换: {old_state.value} -> {new_state.value}")
    
    async def _on_success(self) -> None:
        """调用成功回调"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.successful_calls += 1
            self._stats.consecutive_failures = 0
            
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1
                if self._half_open_calls >= self._half_open_max_calls:
                    self._transition_to(CircuitState.CLOSED)
    
    async def _on_failure(self) -> None:
        """调用失败回调"""
        async with self._lock:
            self._stats.total_calls += 1
            self._stats.failed_calls += 1
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                # 半开状态下失败，立即熔断
                self._transition_to(CircuitState.OPEN)
            elif self._stats.consecutive_failures >= self._fail_max:
                self._transition_to(CircuitState.OPEN)
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """通过熔断器调用函数
        
        Args:
            func: 要调用的异步函数
            *args, **kwargs: 函数参数
            
        Returns:
            函数返回值
            
        Raises:
            CircuitBreakerOpenError: 熔断器处于开启状态
        """
        if self.is_open():
            raise CircuitBreakerOpenError(
                f"熔断器 [{self._name}] 处于开启状态，拒绝请求。"
                f"连续失败: {self._stats.consecutive_failures}/{self._fail_max}"
            )
        
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise
    
    def protect(self, func: Callable) -> Callable:
        """装饰器模式保护函数"""
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await self.call(func, *args, **kwargs)
        return wrapper
    
    def reset(self) -> None:
        """重置熔断器"""
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._half_open_calls = 0
        logger.info(f"[CircuitBreaker:{self._name}] 已重置")


class CircuitBreakerOpenError(Exception):
    """熔断器开启异常"""
    pass


@dataclass
class TokenBucketState:
    """令牌桶状态"""
    tokens: float
    last_update: float


class TokenBucket:
    """令牌桶限流器
    
    实现平滑的请求速率限制。
    
    Usage:
        bucket = TokenBucket(rate=10, capacity=20)  # 10请求/秒，最大突发20
        
        if bucket.consume():
            # 允许请求
        else:
            # 限流
    """
    
    def __init__(
        self,
        rate: float = 10.0,
        capacity: float = 20.0,
        name: str = "default",
    ):
        """初始化令牌桶
        
        Args:
            rate: 令牌产生速率（令牌/秒）
            capacity: 桶容量（最大突发请求数）
            name: 限流器名称
        """
        self._rate = rate
        self._capacity = capacity
        self._name = name
        self._state = TokenBucketState(tokens=capacity, last_update=time.time())
        self._lock = asyncio.Lock()
    
    @property
    def rate(self) -> float:
        return self._rate
    
    @property
    def capacity(self) -> float:
        return self._capacity
    
    async def _refill(self) -> None:
        """补充令牌"""
        now = time.time()
        elapsed = now - self._state.last_update
        new_tokens = elapsed * self._rate
        self._state.tokens = min(self._capacity, self._state.tokens + new_tokens)
        self._state.last_update = now
    
    async def consume(self, tokens: float = 1.0) -> bool:
        """消费令牌
        
        Args:
            tokens: 需要消费的令牌数
            
        Returns:
            是否成功消费
        """
        async with self._lock:
            await self._refill()
            if self._state.tokens >= tokens:
                self._state.tokens -= tokens
                return True
            return False
    
    async def wait_for_token(self, tokens: float = 1.0, timeout: float = 30.0) -> bool:
        """等待直到获取令牌
        
        Args:
            tokens: 需要的令牌数
            timeout: 最大等待时间（秒）
            
        Returns:
            是否成功获取令牌
        """
        start = time.time()
        while True:
            if await self.consume(tokens):
                return True
            
            if time.time() - start > timeout:
                return False
            
            # 计算需要等待的时间
            async with self._lock:
                needed = tokens - self._state.tokens
                wait_time = needed / self._rate
                wait_time = min(wait_time, 1.0)  # 最多等待1秒
            
            await asyncio.sleep(wait_time)
    
    async def get_tokens(self) -> float:
        """获取当前可用令牌数"""
        async with self._lock:
            await self._refill()
            return self._state.tokens


@dataclass
class CostBudget:
    """成本预算"""
    daily_limit: float = 10.0      # 每日预算（美元）
    monthly_limit: float = 200.0   # 每月预算（美元）
    daily_spent: float = 0.0
    monthly_spent: float = 0.0
    last_daily_reset: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    last_monthly_reset: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m"))


class CostController:
    """成本控制器
    
    追踪LLM API调用成本，支持每日/每月预算限制。
    """
    
    def __init__(
        self,
        daily_limit: float = 10.0,
        monthly_limit: float = 200.0,
        name: str = "default",
    ):
        """初始化成本控制器
        
        Args:
            daily_limit: 每日预算上限（美元）
            monthly_limit: 每月预算上限（美元）
            name: 控制器名称
        """
        self._name = name
        self._budget = CostBudget(
            daily_limit=daily_limit,
            monthly_limit=monthly_limit,
        )
        self._lock = asyncio.Lock()
    
    @property
    def daily_limit(self) -> float:
        return self._budget.daily_limit
    
    @property
    def monthly_limit(self) -> float:
        return self._budget.monthly_limit
    
    @property
    def daily_spent(self) -> float:
        return self._budget.daily_spent
    
    @property
    def monthly_spent(self) -> float:
        return self._budget.monthly_spent
    
    @property
    def daily_remaining(self) -> float:
        return max(0, self._budget.daily_limit - self._budget.daily_spent)
    
    @property
    def monthly_remaining(self) -> float:
        return max(0, self._budget.monthly_limit - self._budget.monthly_spent)
    
    def _check_reset(self) -> None:
        """检查并重置过期预算"""
        today = datetime.now().strftime("%Y-%m-%d")
        this_month = datetime.now().strftime("%Y-%m")
        
        if self._budget.last_daily_reset != today:
            self._budget.daily_spent = 0.0
            self._budget.last_daily_reset = today
            logger.info(f"[CostController:{self._name}] 每日预算已重置")
        
        if self._budget.last_monthly_reset != this_month:
            self._budget.monthly_spent = 0.0
            self._budget.last_monthly_reset = this_month
            logger.info(f"[CostController:{self._name}] 每月预算已重置")
    
    async def can_spend(self, estimated_cost: float = 0.0) -> bool:
        """检查是否可以支出
        
        Args:
            estimated_cost: 预估成本
            
        Returns:
            是否在预算内
        """
        async with self._lock:
            self._check_reset()
            
            if self._budget.daily_spent + estimated_cost > self._budget.daily_limit:
                logger.warning(
                    f"[CostController:{self._name}] 每日预算不足: "
                    f"已用=${self._budget.daily_spent:.2f}, "
                    f"预算=${self._budget.daily_limit:.2f}"
                )
                return False
            
            if self._budget.monthly_spent + estimated_cost > self._budget.monthly_limit:
                logger.warning(
                    f"[CostController:{self._name}] 每月预算不足: "
                    f"已用=${self._budget.monthly_spent:.2f}, "
                    f"预算=${self._budget.monthly_limit:.2f}"
                )
                return False
            
            return True
    
    async def record_cost(self, cost: float) -> None:
        """记录实际成本
        
        Args:
            cost: 实际成本（美元）
        """
        async with self._lock:
            self._check_reset()
            self._budget.daily_spent += cost
            self._budget.monthly_spent += cost
            
            logger.debug(
                f"[CostController:{self._name}] 记录成本: ${cost:.4f}, "
                f"今日=${self._budget.daily_spent:.2f}/${self._budget.daily_limit:.2f}, "
                f"本月=${self._budget.monthly_spent:.2f}/${self._budget.monthly_limit:.2f}"
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        self._check_reset()
        return {
            "daily_limit": self._budget.daily_limit,
            "daily_spent": self._budget.daily_spent,
            "daily_remaining": self.daily_remaining,
            "monthly_limit": self._budget.monthly_limit,
            "monthly_spent": self._budget.monthly_spent,
            "monthly_remaining": self.monthly_remaining,
        }


class LLMProtection:
    """LLM调用保护
    
    整合熔断器、限流器、成本控制器，提供完整的LLM调用保护。
    
    Usage:
        protection = LLMProtection(
            circuit_fail_max=5,
            rate_limit=10,
            daily_budget=10.0,
        )
        
        async with protection.protect():
            result = await llm_client.chat(...)
    """
    
    def __init__(
        self,
        circuit_fail_max: int = 5,
        circuit_reset_timeout: float = 60.0,
        rate_limit: float = 10.0,
        rate_capacity: float = 20.0,
        daily_budget: float = 10.0,
        monthly_budget: float = 200.0,
        name: str = "llm",
    ):
        """初始化LLM保护
        
        Args:
            circuit_fail_max: 熔断器失败阈值
            circuit_reset_timeout: 熔断器重置超时
            rate_limit: 请求速率限制（请求/秒）
            rate_capacity: 突发容量
            daily_budget: 每日预算（美元）
            monthly_budget: 每月预算（美元）
            name: 保护器名称
        """
        self._name = name
        self._circuit = CircuitBreaker(
            fail_max=circuit_fail_max,
            reset_timeout=circuit_reset_timeout,
            name=name,
        )
        self._rate_limiter = TokenBucket(
            rate=rate_limit,
            capacity=rate_capacity,
            name=name,
        )
        self._cost_controller = CostController(
            daily_limit=daily_budget,
            monthly_limit=monthly_budget,
            name=name,
        )
    
    @property
    def circuit(self) -> CircuitBreaker:
        return self._circuit
    
    @property
    def rate_limiter(self) -> TokenBucket:
        return self._rate_limiter
    
    @property
    def cost_controller(self) -> CostController:
        return self._cost_controller
    
    async def check_before_call(self, estimated_cost: float = 0.01) -> None:
        """调用前检查
        
        Raises:
            CircuitBreakerOpenError: 熔断器开启
            RateLimitExceededError: 限流
            BudgetExceededError: 预算超限
        """
        # 检查熔断器
        if self._circuit.is_open():
            raise CircuitBreakerOpenError(
                f"[{self._name}] 熔断器开启，拒绝请求"
            )
        
        # 检查预算
        if not await self._cost_controller.can_spend(estimated_cost):
            raise BudgetExceededError(
                f"[{self._name}] 预算超限，拒绝请求"
            )
        
        # 等待令牌
        if not await self._rate_limiter.wait_for_token(timeout=5.0):
            raise RateLimitExceededError(
                f"[{self._name}] 限流，拒绝请求"
            )
    
    async def on_success(self, cost: float = 0.0) -> None:
        """调用成功回调"""
        await self._circuit._on_success()
        if cost > 0:
            await self._cost_controller.record_cost(cost)
    
    async def on_failure(self) -> None:
        """调用失败回调"""
        await self._circuit._on_failure()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取完整统计"""
        return {
            "circuit": {
                "state": self._circuit.state.value,
                "total_calls": self._circuit.stats.total_calls,
                "failed_calls": self._circuit.stats.failed_calls,
                "consecutive_failures": self._circuit.stats.consecutive_failures,
            },
            "rate_limiter": {
                "rate": self._rate_limiter.rate,
                "capacity": self._rate_limiter.capacity,
            },
            "cost": self._cost_controller.get_stats(),
        }


class RateLimitExceededError(Exception):
    """限流异常"""
    pass


class BudgetExceededError(Exception):
    """预算超限异常"""
    pass


# 全局LLM保护器
_llm_protection: Optional[LLMProtection] = None


def get_llm_protection() -> LLMProtection:
    """获取全局LLM保护器"""
    global _llm_protection
    if _llm_protection is None:
        _llm_protection = LLMProtection()
    return _llm_protection
