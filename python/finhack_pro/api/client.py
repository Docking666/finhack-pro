"""
Rust核心API客户端

提供与Rust核心引擎通信的HTTP和WebSocket接口。
包括回测提交、实时行情订阅、订单管理等。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class RustCoreClient:
    """Rust核心API客户端

    通过HTTP/WebSocket与Rust核心引擎通信。

    Usage:
        client = RustCoreClient(api_url="http://localhost:8080")
        health = await client.health_check()
        result = await client.submit_backtest(config)
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8080",
        ws_url: str = "ws://localhost:8081",
        api_key: str = "",
        timeout: int = 30,
    ) -> None:
        """初始化Rust核心客户端

        Args:
            api_url: HTTP API地址
            ws_url: WebSocket地址
            api_key: API密钥
            timeout: 请求超时(秒)
        """
        self.api_url = api_url.rstrip("/")
        self.ws_url = ws_url
        self.api_key = api_key
        self.timeout = timeout
        self._headers: Dict[str, str] = {
            "Content-Type": "application/json",
        }
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _get_client(self) -> httpx.AsyncClient:
        """获取HTTP客户端"""
        return httpx.AsyncClient(
            base_url=self.api_url,
            headers=self._headers,
            timeout=self.timeout,
        )

    async def health_check(self) -> Dict[str, Any]:
        """健康检查

        Returns:
            健康状态字典
        """
        try:
            async with self._get_client() as client:
                resp = await client.get("/health")
                if resp.status_code == 200:
                    return resp.json()
                return {"status": "unhealthy", "code": resp.status_code}
        except Exception as e:
            logger.error(f"健康检查失败: {e}")
            return {"status": "unreachable", "error": str(e)}

    async def submit_backtest(
        self,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """提交回测任务到Rust引擎

        Args:
            config: 回测配置字典，包含:
                - strategy: 策略名称
                - symbols: 标的列表
                - start_date: 开始日期
                - end_date: 结束日期
                - initial_capital: 初始资金
                - commission_rate: 佣金费率
                - parameters: 策略参数

        Returns:
            回测任务ID和状态
        """
        try:
            async with self._get_client() as client:
                resp = await client.post("/api/v1/backtest", json=config)
                resp.raise_for_status()
                result = resp.json()
                logger.info(f"回测任务已提交: {result.get('task_id', '')}")
                return result
        except httpx.HTTPStatusError as e:
            logger.error(f"回测提交失败: {e.response.status_code} - {e.response.text}")
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"回测提交失败: {e}")
            return {"error": str(e)}

    async def get_backtest_result(self, task_id: str) -> Dict[str, Any]:
        """获取回测结果

        Args:
            task_id: 回测任务ID

        Returns:
            回测结果字典
        """
        try:
            async with self._get_client() as client:
                resp = await client.get(f"/api/v1/backtest/{task_id}")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"获取回测结果失败: {e}")
            return {"error": str(e)}

    async def submit_order(
        self,
        symbol: str,
        direction: str,
        price: float,
        volume: int,
        order_type: str = "limit",
    ) -> Dict[str, Any]:
        """提交订单

        Args:
            symbol: 标的代码
            direction: 方向 (buy/sell)
            price: 价格
            volume: 数量
            order_type: 订单类型 (market/limit)

        Returns:
            订单提交结果
        """
        order = {
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "volume": volume,
            "order_type": order_type,
        }

        try:
            async with self._get_client() as client:
                resp = await client.post("/api/v1/orders", json=order)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"订单提交失败: {e}")
            return {"error": str(e)}

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """撤销订单

        Args:
            order_id: 订单ID

        Returns:
            撤单结果
        """
        try:
            async with self._get_client() as client:
                resp = await client.delete(f"/api/v1/orders/{order_id}")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"撤单失败: {e}")
            return {"error": str(e)}

    async def get_positions(self) -> Dict[str, Any]:
        """获取当前持仓

        Returns:
            持仓信息
        """
        try:
            async with self._get_client() as client:
                resp = await client.get("/api/v1/positions")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return {"error": str(e)}

    async def get_account(self) -> Dict[str, Any]:
        """获取账户信息

        Returns:
            账户信息
        """
        try:
            async with self._get_client() as client:
                resp = await client.get("/api/v1/account")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
            return {"error": str(e)}

    async def subscribe_market_data(
        self,
        symbols: List[str],
        callback: Any,
    ) -> None:
        """订阅实时行情(WebSocket)

        Args:
            symbols: 标的列表
            callback: 行情回调函数
        """
        try:
            import websockets

            uri = f"{self.ws_url}/market?symbols={','.join(symbols)}"
            logger.info(f"连接WebSocket: {uri}")

            async with websockets.connect(uri) as ws:
                logger.info("WebSocket连接成功")
                async for message in ws:
                    try:
                        data = json.loads(message)
                        if callback:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(data)
                            else:
                                callback(data)
                    except json.JSONDecodeError:
                        logger.warning(f"无效的WebSocket消息: {message}")
        except ImportError:
            logger.error("websockets包未安装")
        except Exception as e:
            logger.error(f"WebSocket连接失败: {e}")

    async def get_strategy_list(self) -> List[str]:
        """获取可用策略列表

        Returns:
            策略名称列表
        """
        try:
            async with self._get_client() as client:
                resp = await client.get("/api/v1/strategies")
                resp.raise_for_status()
                return resp.json().get("strategies", [])
        except Exception as e:
            logger.error(f"获取策略列表失败: {e}")
            return []
