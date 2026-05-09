"""
LLM客户端封装模块

统一封装OpenAI和Anthropic的API调用，提供:
- 多模型支持(GPT-4, GPT-4o, Claude-3.5-Sonnet等)
- 结构化输出(JSON mode)
- 重试机制(指数退避)
- Token用量追踪与成本估算
- 异步调用(asyncio)
- Function calling / Tool use支持

优化:
- 熔断器保护：防止级联故障
- 令牌桶限流：平滑请求速率
- 成本预算控制：每日/每月预算限制
"""

from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar

from loguru import logger
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(str, Enum):
    """LLM提供商"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


# 模型定价表 (每1K token, USD)
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
    "claude-3-5-sonnet-latest": {"input": 0.003, "output": 0.015},
    "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
    "claude-3-haiku-20240307": {"input": 0.00025, "output": 0.00125},
}


class TokenUsage(BaseModel):
    """Token用量统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class LLMClient:
    """LLM客户端封装

    统一封装OpenAI和Anthropic API调用，支持结构化输出、重试、成本追踪等。

    Usage:
        client = LLMClient(provider="openai", api_key="sk-xxx", model="gpt-4o")
        response = await client.chat("分析一下当前市场")
        # 结构化输出
        report = await client.chat_structured(
            "分析市场",
            response_model=MarketAnalysisReport,
            system="你是一个专业的量化分析师"
        )
    """

    def __init__(
        self,
        provider: str = "openai",
        api_key: str = "",
        base_url: Optional[str] = None,
        model: str = "gpt-4o",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int = 60,
        max_retries: int = 3,
        # 熔断限流配置
        circuit_fail_max: int = 5,
        circuit_reset_timeout: float = 60.0,
        rate_limit: float = 10.0,
        rate_capacity: float = 20.0,
        daily_budget: float = 10.0,
        monthly_budget: float = 200.0,
        enable_protection: bool = True,
    ) -> None:
        """初始化LLM客户端

        Args:
            provider: LLM提供商 (openai / anthropic)
            api_key: API密钥
            base_url: 自定义API地址(仅OpenAI)
            model: 模型名称
            temperature: 生成温度
            max_tokens: 最大生成token数
            timeout: 请求超时(秒)
            max_retries: 最大重试次数
            circuit_fail_max: 熔断器失败阈值
            circuit_reset_timeout: 熔断器重置超时
            rate_limit: 请求速率限制（请求/秒）
            rate_capacity: 突发容量
            daily_budget: 每日预算（美元）
            monthly_budget: 每月预算（美元）
            enable_protection: 是否启用熔断限流保护
        """
        self.provider = LLMProvider(provider)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.enable_protection = enable_protection

        # Token用量累计
        self._total_usage = TokenUsage()

        # 初始化熔断限流保护
        self._protection = None
        if enable_protection:
            try:
                from finhack_pro.utils.circuit_breaker import LLMProtection
                self._protection = LLMProtection(
                    circuit_fail_max=circuit_fail_max,
                    circuit_reset_timeout=circuit_reset_timeout,
                    rate_limit=rate_limit,
                    rate_capacity=rate_capacity,
                    daily_budget=daily_budget,
                    monthly_budget=monthly_budget,
                    name=f"llm_{provider}",
                )
            except ImportError:
                logger.warning("circuit_breaker模块未找到，熔断限流保护已禁用")
                self.enable_protection = False

        # 初始化对应客户端
        self._openai_client: Optional[Any] = None
        self._anthropic_client: Optional[Any] = None

        if self.provider == LLMProvider.OPENAI:
            try:
                from openai import AsyncOpenAI
                kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": timeout}
                if base_url:
                    kwargs["base_url"] = base_url
                self._openai_client = AsyncOpenAI(**kwargs)
                logger.info(f"OpenAI客户端初始化完成, model={model}")
            except ImportError:
                logger.error("openai包未安装，请执行: pip install openai")
                raise
        elif self.provider == LLMProvider.ANTHROPIC:
            try:
                import anthropic
                self._anthropic_client = anthropic.AsyncAnthropic(
                    api_key=api_key, timeout=timeout
                )
                logger.info(f"Anthropic客户端初始化完成, model={model}")
            except ImportError:
                logger.error("anthropic包未安装，请执行: pip install anthropic")
                raise

    @property
    def total_usage(self) -> TokenUsage:
        """获取累计Token用量"""
        return self._total_usage

    @property
    def protection(self):
        """获取熔断限流保护器"""
        return self._protection

    def reset_usage(self) -> None:
        """重置Token用量统计"""
        self._total_usage = TokenUsage()

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """估算API调用成本

        Args:
            prompt_tokens: 输入token数
            completion_tokens: 输出token数

        Returns:
            估算成本(USD)
        """
        pricing = MODEL_PRICING.get(self.model)
        if not pricing:
            # 未知模型按GPT-4o价格估算
            pricing = MODEL_PRICING["gpt-4o"]
        cost = (
            prompt_tokens / 1000.0 * pricing["input"]
            + completion_tokens / 1000.0 * pricing["output"]
        )
        return cost

    def get_protection_stats(self) -> Dict[str, Any]:
        """获取保护器统计信息"""
        if self._protection:
            return self._protection.get_stats()
        return {"protection_enabled": False}

    async def chat(
        self,
        message: str,
        system: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
        estimated_cost: float = 0.01,
    ) -> str:
        """发送聊天请求

        Args:
            message: 用户消息
            system: 系统提示词
            history: 对话历史 [{"role": "user/assistant", "content": "..."}]
            temperature: 生成温度(覆盖默认值)
            max_tokens: 最大token数(覆盖默认值)
            tools: 工具定义列表(function calling)
            tool_choice: 工具选择策略
            estimated_cost: 预估成本（用于预算检查）

        Returns:
            LLM响应文本
        """
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        # 熔断限流检查
        if self.enable_protection and self._protection:
            try:
                await self._protection.check_before_call(estimated_cost)
            except Exception as e:
                logger.error(f"LLM调用被保护器拒绝: {e}")
                raise

        # 构建消息列表
        messages: List[Dict[str, str]] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        # 根据提供商调用不同API
        try:
            if self.provider == LLMProvider.OPENAI:
                result = await self._chat_openai(
                    messages, system, temp, max_tok, tools, tool_choice
                )
            else:
                result = await self._chat_anthropic(messages, system, temp, max_tok)
            
            # 成功回调
            if self.enable_protection and self._protection:
                await self._protection.on_success(self._total_usage.estimated_cost_usd)
            
            return result
            
        except Exception as e:
            # 失败回调
            if self.enable_protection and self._protection:
                await self._protection.on_failure()
            raise

    async def _chat_openai(
        self,
        messages: List[Dict[str, str]],
        system: str,
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[str],
    ) -> str:
        """调用OpenAI API"""
        assert self._openai_client is not None

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["messages"] = [{"role": "system", "content": system}] + kwargs["messages"]
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = await self._openai_client.chat.completions.create(**kwargs)
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                completion_tokens = usage.completion_tokens if usage else 0
                total_tokens = usage.total_tokens if usage else 0
                cost = self._estimate_cost(prompt_tokens, completion_tokens)

                # 更新用量统计
                self._total_usage.prompt_tokens += prompt_tokens
                self._total_usage.completion_tokens += completion_tokens
                self._total_usage.total_tokens += total_tokens
                self._total_usage.estimated_cost_usd += cost

                logger.debug(
                    f"OpenAI调用完成: tokens={total_tokens}, cost=${cost:.4f}"
                )
                return response.choices[0].message.content or ""

            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt + 1  # 指数退避: 1, 3, 7...
                    logger.warning(
                        f"OpenAI调用失败(第{attempt + 1}次), "
                        f"{wait_time}秒后重试: {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"OpenAI调用失败(已重试{self.max_retries}次): {e}")

        raise RuntimeError(f"LLM调用失败: {last_error}")

    async def _chat_anthropic(
        self,
        messages: List[Dict[str, str]],
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """调用Anthropic API"""
        assert self._anthropic_client is not None

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                response = await self._anthropic_client.messages.create(**kwargs)
                prompt_tokens = response.usage.input_tokens
                completion_tokens = response.usage.output_tokens
                total_tokens = prompt_tokens + completion_tokens
                cost = self._estimate_cost(prompt_tokens, completion_tokens)

                self._total_usage.prompt_tokens += prompt_tokens
                self._total_usage.completion_tokens += completion_tokens
                self._total_usage.total_tokens += total_tokens
                self._total_usage.estimated_cost_usd += cost

                logger.debug(
                    f"Anthropic调用完成: tokens={total_tokens}, cost=${cost:.4f}"
                )
                # 提取文本内容
                text_blocks = [
                    block.text for block in response.content if block.type == "text"
                ]
                return "\n".join(text_blocks)

            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt + 1
                    logger.warning(
                        f"Anthropic调用失败(第{attempt + 1}次), "
                        f"{wait_time}秒后重试: {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        f"Anthropic调用失败(已重试{self.max_retries}次): {e}"
                    )

        raise RuntimeError(f"LLM调用失败: {last_error}")

    async def chat_structured(
        self,
        message: str,
        response_model: Type[T],
        system: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        temperature: Optional[float] = None,
    ) -> T:
        """结构化输出 - 返回Pydantic模型实例

        通过在system prompt中要求JSON输出，然后解析为Pydantic模型。

        Args:
            message: 用户消息
            response_model: 目标Pydantic模型类
            system: 系统提示词
            history: 对话历史
            temperature: 生成温度

        Returns:
            解析后的Pydantic模型实例
        """
        # 构建JSON schema提示
        schema = response_model.model_json_schema()
        json_instruction = (
            "\n\n你必须严格按照以下JSON Schema格式输出结果，"
            "只输出JSON，不要输出其他任何内容:\n"
            f"```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```"
        )
        full_system = system + json_instruction if system else json_instruction

        response_text = await self.chat(
            message=message,
            system=full_system,
            history=history,
            temperature=temperature,
        )

        # 解析JSON响应
        try:
            # 尝试提取JSON块
            json_str = response_text.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            data = json.loads(json_str)
            return response_model.model_validate(data)

        except (json.JSONDecodeError, Exception) as e:
            logger.error(f"JSON解析失败: {e}\n原始响应: {response_text[:500]}")
            raise ValueError(
                f"无法将LLM响应解析为 {response_model.__name__}: {e}"
            ) from e

    async def chat_with_tools(
        self,
        message: str,
        tools: List[Dict[str, Any]],
        system: str = "",
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """使用Function Calling / Tool Use

        Args:
            message: 用户消息
            tools: 工具定义列表
            system: 系统提示词
            history: 对话历史

        Returns:
            包含content和tool_calls的字典
        """
        if self.provider == LLMProvider.OPENAI:
            return await self._chat_with_tools_openai(
                message, tools, system, history
            )
        else:
            return await self._chat_with_tools_anthropic(
                message, tools, system, history
            )

    async def _chat_with_tools_openai(
        self,
        message: str,
        tools: List[Dict[str, Any]],
        system: str,
        history: Optional[List[Dict[str, str]]],
    ) -> Dict[str, Any]:
        """OpenAI Function Calling"""
        assert self._openai_client is not None

        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        response = await self._openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )

        choice = response.choices[0]
        result: Dict[str, Any] = {
            "content": choice.message.content or "",
            "tool_calls": [],
        }

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                result["tool_calls"].append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                })

        return result

    async def _chat_with_tools_anthropic(
        self,
        message: str,
        tools: List[Dict[str, Any]],
        system: str,
        history: Optional[List[Dict[str, str]]],
    ) -> Dict[str, Any]:
        """Anthropic Tool Use"""
        assert self._anthropic_client is not None

        # 转换工具格式
        anthropic_tools = []
        for tool in tools:
            func = tool.get("function", tool)
            anthropic_tools.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })

        messages: List[Dict[str, str]] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = await self._anthropic_client.messages.create(**kwargs)

        result: Dict[str, Any] = {
            "content": "",
            "tool_calls": [],
        }

        for block in response.content:
            if block.type == "text":
                result["content"] += block.text
            elif block.type == "tool_use":
                result["tool_calls"].append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })

        return result
