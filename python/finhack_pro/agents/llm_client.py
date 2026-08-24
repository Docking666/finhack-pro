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
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from loguru import logger
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(str, Enum):
    """LLM提供商"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


# 模型定价表 (每1K token, USD)
# 国内模型价格按官方美元/人民币报价折算（汇率约 7.2），仅用于成本估算；
# 价格会随厂商调价变化，可通过 LLMClient(model_pricing_override=...) 或
# config 中 llm.model_pricing 覆盖。
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    # Anthropic
    "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
    "claude-3-5-sonnet-latest": {"input": 0.003, "output": 0.015},
    "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
    "claude-3-haiku-20240307": {"input": 0.00025, "output": 0.00125},
    "claude-3-7-sonnet-latest": {"input": 0.003, "output": 0.015},
    # DeepSeek（官方美元价）
    "deepseek-chat": {"input": 0.00027, "output": 0.0011},
    "deepseek-reasoner": {"input": 0.00055, "output": 0.00219},
    # 智谱 GLM（官方人民币价折算）
    "glm-4-plus": {"input": 0.007, "output": 0.007},
    "glm-4-flash": {"input": 0.0001, "output": 0.0001},
    "glm-4-air": {"input": 0.0007, "output": 0.0007},
    # 阿里 Qwen（官方人民币价折算）
    "qwen-plus": {"input": 0.00011, "output": 0.00028},
    "qwen-max": {"input": 0.0028, "output": 0.0028},
    "qwen-turbo": {"input": 0.00003, "output": 0.00007},
    # 月之暗面 Kimi
    "moonshot-v1-8k": {"input": 0.00083, "output": 0.0028},
    "moonshot-v1-32k": {"input": 0.00083, "output": 0.0028},
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
        model_pricing_override: Optional[Dict[str, Dict[str, float]]] = None,
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
            model_pricing_override: 模型定价覆盖（每1K token, USD），
                优先于内置 MODEL_PRICING，用于厂商调价后的成本精确估算
        """
        # 协议归一化：配置里 provider 可能存服务商名（deepseek/orca/zhipu 等），
        # 调用层只认协议（openai/anthropic）——OpenAI 兼容服务商统一按 openai。
        if provider not in {p.value for p in LLMProvider}:
            provider = LLMProvider.OPENAI.value
        self.provider = LLMProvider(provider)
        self.model = model
        self.temperature = temperature
        self._pricing_override = model_pricing_override or {}
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
        # 查找顺序：调用方覆盖价 → 内置定价表 → 兜底模型价
        pricing = self._pricing_override.get(self.model)
        if pricing is None:
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
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        response_format: Optional[Dict[str, Any]] = None,
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
            stream: 是否流式输出（WebUI 思考过程展示用）
            on_token: 流式回调，每产出一段文本调用一次
            response_format: 结构化输出格式（如 {"type": "json_object"}）
                优先使用原生响应格式约束，替代提示词方式

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
                    messages, system, temp, max_tok, tools, tool_choice,
                    stream=stream, on_token=on_token,
                    response_format=response_format,
                )
            else:
                result = await self._chat_anthropic(
                    messages, system, temp, max_tok,
                    stream=stream, on_token=on_token,
                )
            
            # 成功回调
            if self.enable_protection and self._protection:
                await self._protection.on_success(self._total_usage.estimated_cost_usd)
            
            return result
            
        except Exception:
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
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        response_format: Optional[Dict[str, Any]] = None,
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
        if response_format:
            kwargs["response_format"] = response_format
        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                if stream:
                    # 流式输出：逐段回调，累积完整文本，从流中提取 usage
                    full_text: List[str] = []
                    prompt_tokens = 0
                    completion_tokens = 0
                    async for chunk in await self._openai_client.chat.completions.create(**kwargs):
                        if not chunk.choices:
                            # 流结束时的 usage 块
                            if chunk.usage:
                                prompt_tokens = chunk.usage.prompt_tokens or 0
                                completion_tokens = chunk.usage.completion_tokens or 0
                            continue
                        delta = chunk.choices[0].delta
                        piece = delta.content or ""
                        if piece:
                            full_text.append(piece)
                            if on_token:
                                on_token(piece)
                    text = "".join(full_text)
                    if not prompt_tokens and not completion_tokens:
                        # 服务商未返回 usage，按文本粗估
                        prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 2
                        completion_tokens = len(text) // 2
                else:
                    response = await self._openai_client.chat.completions.create(**kwargs)
                    usage = response.usage
                    prompt_tokens = usage.prompt_tokens if usage else 0
                    completion_tokens = usage.completion_tokens if usage else 0
                    text = response.choices[0].message.content or ""

                total_tokens = prompt_tokens + completion_tokens
                cost = self._estimate_cost(prompt_tokens, completion_tokens)

                # 更新用量统计
                self._total_usage.prompt_tokens += prompt_tokens
                self._total_usage.completion_tokens += completion_tokens
                self._total_usage.total_tokens += total_tokens
                self._total_usage.estimated_cost_usd += cost

                logger.debug(
                    f"OpenAI调用完成: tokens={total_tokens}, cost=${cost:.4f}"
                )
                return text

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
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
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
        if stream:
            kwargs["stream"] = True

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                if stream:
                    # 流式输出：Anthropic stream 返回事件序列
                    full_text: List[str] = []
                    prompt_tokens = 0
                    completion_tokens = 0
                    async with self._anthropic_client.messages.stream(**kwargs) as s:
                        async for text in s.text_stream:
                            full_text.append(text)
                            if on_token:
                                on_token(text)
                        final_usage = await s.get_final_message()
                        if final_usage.usage:
                            prompt_tokens = final_usage.usage.input_tokens
                            completion_tokens = final_usage.usage.output_tokens
                    text = "".join(full_text)
                    if not prompt_tokens and not completion_tokens:
                        prompt_tokens = sum(len(m.get("content", "")) for m in messages) // 2
                        completion_tokens = len(text) // 2
                else:
                    response = await self._anthropic_client.messages.create(**kwargs)
                    prompt_tokens = response.usage.input_tokens
                    completion_tokens = response.usage.output_tokens
                    # 提取文本内容
                    text_blocks = [
                        block.text for block in response.content if block.type == "text"
                    ]
                    text = "\n".join(text_blocks)

                total_tokens = prompt_tokens + completion_tokens
                cost = self._estimate_cost(prompt_tokens, completion_tokens)

                self._total_usage.prompt_tokens += prompt_tokens
                self._total_usage.completion_tokens += completion_tokens
                self._total_usage.total_tokens += total_tokens
                self._total_usage.estimated_cost_usd += cost

                logger.debug(
                    f"Anthropic调用完成: tokens={total_tokens}, cost=${cost:.4f}"
                )
                return text

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

        优先级（从强到弱）：
        1. 原生 Structured Outputs（response_format=json_schema，OpenAI）
        2. 原生 JSON mode（response_format=json_object）
        3. 提示词约束 + 宽松 JSON 提取（兼容所有 OpenAI 兼容服务商）

        解析失败时自动携带错误信息重试，最多重试 parse_retries 次。

        Args:
            message: 用户消息
            response_model: 目标Pydantic模型类
            system: 系统提示词
            history: 对话历史
            temperature: 生成温度

        Returns:
            解析后的Pydantic模型实例
        """
        # 构建JSON schema提示（作为最终兜底）
        schema = response_model.model_json_schema()
        json_instruction = (
            "\n\n你必须严格按照以下JSON Schema格式输出结果，"
            "只输出JSON，不要输出其他任何内容:\n"
            f"```json\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n```"
        )
        full_system = system + json_instruction if system else json_instruction

        # 原生 JSON mode（OpenAI 兼容服务商普遍支持）
        native_response_format: Optional[Dict[str, Any]] = None
        if self.provider == LLMProvider.OPENAI:
            # 服务商如果支持 json_schema 用 json_schema，否则降级 json_object
            native_response_format = {
                "type": "json_object",
            }

        last_error: Optional[Exception] = None
        data: Any = None
        for attempt in range(max(1, self.max_retries)):
            try:
                response_text = await self.chat(
                    message=message,
                    system=full_system,
                    history=history,
                    temperature=temperature,
                    response_format=native_response_format,
                )

                # 解析JSON响应
                try:
                    data = self._extract_json(response_text)
                    return response_model.model_validate(data)
                except Exception as e:
                    last_error = e
                    if attempt < max(1, self.max_retries) - 1:
                        logger.warning(
                            f"结构化输出解析失败(第{attempt + 1}次)，"
                            f"携带错误重试: {e}"
                        )
                        # 重试时把错误信息带给模型，帮助其修正输出
                        native_response_format = None  # 降级为提示词模式
                        message = (
                            f"{message}\n\n[上一次输出无法解析: {e}，"
                            f"请严格按照 JSON Schema 输出合法 JSON]"
                        )
                        continue
                    raise

            except Exception as e:
                last_error = e
                if attempt < max(1, self.max_retries) - 1:
                    await asyncio.sleep(2 ** attempt + 1)
                    continue
                raise

        # 重试用尽后兜底：尝试补全必填字段（如 symbol 等可从上下文推断的字段）
        try:
            fallback_data = self._try_fill_required_fields(data, response_model)
            if fallback_data is not None:
                return response_model.model_validate(fallback_data)
        except Exception as e:
            last_error = e
            logger.warning(f"结构化输出兜底补全失败: {e}")

        raise ValueError(
            f"无法将LLM响应解析为 {response_model.__name__}: {last_error}"
        )

    @staticmethod
    def _try_fill_required_fields(
        data: Any,
        response_model: Type[T],
    ) -> Any:
        """兜底补全必填字段（仅同义字段推断，不伪造）

        某些 LLM（如 DeepSeek）在 json_object 模式下会省略必填字段
        （如 symbol），导致 Pydantic 校验失败。这里仅从 LLM 输出中
        可能存在的零散字段（如 "股票代码"、"symbol" 别名）推断真实值。
        无法从真实上下文补全的字段保持缺失，交由校验失败抛错，
        绝不填充零值/空值/默认值伪装完整报告。若无法补全返回 None。
        """
        if not isinstance(data, dict):
            return None
        try:
            schema = response_model.model_json_schema()
            required = schema.get("required", [])
            props = schema.get("properties", {})
            if not required:
                return data
            filled = False
            for field in required:
                if field in data and data[field] is not None:
                    continue
                # 尝试从已有数据中找同义字段
                synonyms = {
                    "symbol": ["代码", "股票代码", "证券代码", "ticker", "code"],
                    "code": ["symbol", "代码", "股票代码"],
                }
                found = None
                for alias in synonyms.get(field, []):
                    for k, v in data.items():
                        if k != field and alias in str(k).lower():
                            found = v
                            break
                        if isinstance(v, str) and alias.lower() in str(k).lower():
                            found = v
                            break
                    if found is not None:
                        break
                if found is not None:
                    data[field] = found
                    filled = True
                    continue
                # 无法从真实上下文补全的必填字段：保留缺失，交由 Pydantic 校验失败抛错。
                # 不填充零值/空值/默认值——避免把残缺响应伪装成完整报告（SDD：禁止伪造）。
            if filled:
                return data
            return None
        except Exception:
            return None

    @staticmethod
    def _extract_json(text: str) -> Any:
        """宽松提取 JSON：处理代码块、BOM、前后杂文本

        Returns:
            解析后的 JSON 对象

        Raises:
            ValueError: 无法提取有效 JSON
        """
        import re

        json_str = text.strip().lstrip("\ufeff")
        # 去掉外层代码块
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试提取第一个 {...} 或 [...] 平衡块
            for open_ch, close_ch in (("{", "}"), ("[", "]")):
                start = json_str.find(open_ch)
                if start == -1:
                    continue
                depth = 0
                for i in range(start, len(json_str)):
                    if json_str[i] == open_ch:
                        depth += 1
                    elif json_str[i] == close_ch:
                        depth -= 1
                        if depth == 0:
                            candidate = json_str[start:i + 1]
                            try:
                                return json.loads(candidate)
                            except json.JSONDecodeError:
                                break
            raise ValueError(f"无法从响应中提取JSON: {text[:200]}")

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
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """OpenAI Function Calling

        Args:
            message: 用户消息（messages 未提供时使用）
            tools: 工具定义列表
            system: 系统提示词（messages 未提供时使用）
            history: 对话历史（messages 未提供时使用）
            messages: 完整消息列表（agent loop 复用，提供时忽略前三参数）
        """
        assert self._openai_client is not None

        if messages is None:
            messages = []
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

    async def run_agent_loop(
        self,
        message: str,
        tools: List[Dict[str, Any]],
        system: str = "",
        history: Optional[List[Dict[str, str]]] = None,
        tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        max_iterations: int = 5,
        on_step: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """多轮工具调用 Agent Loop

        实现完整的 ReAct 循环：
        模型 → 工具调用请求 → 执行工具 → 结果回填 → 模型（直到无工具调用或达到上限）

        这是"聊天机器人"与"Agent"的分水岭 —— 此前 chat_with_tools 只能发一次
        工具请求，无法根据工具结果继续推理。

        Args:
            message: 用户消息
            tools: OpenAI 格式工具定义列表
            system: 系统提示词
            history: 对话历史
            tool_executor: 工具执行器回调，签名 (tool_name, arguments) -> result
                未提供时返回错误结果，工具调用不会真正执行
            max_iterations: 最大工具调用轮数（默认 5）
            on_step: 每轮回调 (iteration, step_info)，用于 WebUI 展示思考过程

        Returns:
            {
                "content": 最终文本回复,
                "tool_calls": 全部工具调用记录,
                "iterations": 实际轮数,
                "messages": 完整对话历史（含工具结果回填）,
            }
        """
        # 构造初始消息
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        tool_calls_log: List[Dict[str, Any]] = []
        final_content = ""
        iterations = 0

        for iteration in range(max_iterations):
            iterations = iteration + 1
            step_info: Dict[str, Any] = {"iteration": iteration, "tool_calls": []}

            # 调用模型（含工具），直接复用完整消息列表
            response = await self._chat_with_tools_openai(
                message="",
                tools=tools,
                system="",
                history=None,
                messages=messages,
            )

            content = response.get("content", "")
            calls = response.get("tool_calls", [])

            if content:
                final_content = content

            if on_step:
                step_info["content"] = content
                step_info["tool_calls"] = calls

            # 没有工具调用 → 模型已给出最终回答，结束
            if not calls:
                if on_step:
                    on_step(iteration, step_info)
                break

            # 有工具调用：执行并回填
            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c["arguments"], ensure_ascii=False),
                        },
                    }
                    for c in calls
                ],
            })

            for call in calls:
                tool_name = call["name"]
                tool_args = call.get("arguments", {})
                tool_calls_log.append({
                    "iteration": iteration,
                    "id": call.get("id", ""),
                    "name": tool_name,
                    "arguments": tool_args,
                })
                step_info["tool_calls"].append({
                    "name": tool_name,
                    "arguments": tool_args,
                })

                # 执行工具
                result_text: str
                if tool_executor is None:
                    result_text = (
                        f"错误: 未提供 tool_executor，无法执行工具 {tool_name}"
                    )
                else:
                    try:
                        result = tool_executor(tool_name, tool_args)
                        if asyncio.iscoroutine(result):
                            result = await result
                        result_text = (
                            result if isinstance(result, str)
                            else json.dumps(result, ensure_ascii=False, default=str)
                        )
                    except Exception as e:
                        result_text = f"工具执行异常: {type(e).__name__}: {e}"

                # 工具结果回填
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", f"call_{iteration}_{len(tool_calls_log)}"),
                    "content": result_text[:8000],  # 截断过长的工具结果
                })

            if on_step:
                on_step(iteration, step_info)

        return {
            "content": final_content,
            "tool_calls": tool_calls_log,
            "iterations": iterations,
            "messages": messages,
        }
