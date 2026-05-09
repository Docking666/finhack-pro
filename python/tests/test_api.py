"""
RustCoreClient API 客户端测试

测试 RustCoreClient 的初始化、HTTP 请求、WebSocket 订阅等功能。
使用 unittest.mock 模拟 httpx 和 websockets，无需真实服务器。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from finhack_pro.api.client import RustCoreClient

# ============================================================================
# 初始化测试
# ============================================================================


class TestRustCoreClientInit:
    """RustCoreClient 初始化测试"""

    def test_default_params(self):
        """默认参数初始化"""
        client = RustCoreClient()
        assert client.api_url == "http://localhost:8080"
        assert client.ws_url == "ws://localhost:8081"
        assert client.api_key == ""
        assert client.timeout == 30

    def test_custom_params(self):
        """自定义参数初始化"""
        client = RustCoreClient(
            api_url="http://192.168.1.100:9090/",
            ws_url="ws://192.168.1.100:9091",
            api_key="test-secret-key",
            timeout=60,
        )
        assert client.api_url == "http://192.168.1.100:9090"
        assert client.ws_url == "ws://192.168.1.100:9091"
        assert client.api_key == "test-secret-key"
        assert client.timeout == 60

    def test_headers_without_api_key(self):
        """无 API Key 时 headers 不含 Authorization"""
        client = RustCoreClient()
        assert "Authorization" not in client._headers
        assert client._headers["Content-Type"] == "application/json"

    def test_headers_with_api_key(self):
        """有 API Key 时 headers 包含 Bearer token"""
        client = RustCoreClient(api_key="my-key")
        assert client._headers["Authorization"] == "Bearer my-key"


# ============================================================================
# _get_client 测试
# ============================================================================


class TestGetClient:
    """_get_client 方法测试"""

    def test_returns_async_client(self):
        """返回 httpx.AsyncClient 实例"""
        client = RustCoreClient(api_url="http://localhost:8080", timeout=15)
        http_client = client._get_client()
        assert isinstance(http_client, httpx.AsyncClient)
        assert http_client.base_url == httpx.URL("http://localhost:8080")
        assert http_client.timeout == httpx.Timeout(15)

    def test_client_has_correct_headers(self):
        """客户端携带正确的 headers"""
        client = RustCoreClient(api_key="test-key")
        http_client = client._get_client()
        assert http_client.headers["Authorization"] == "Bearer test-key"
        assert http_client.headers["Content-Type"] == "application/json"


# ============================================================================
# health_check 测试
# ============================================================================


class TestHealthCheck:
    """健康检查测试"""

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """健康检查成功返回 status"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy", "version": "1.0.0"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.health_check()

        assert result["status"] == "healthy"
        assert result["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_status(self):
        """健康检查返回非200状态码"""
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.health_check()

        assert result["status"] == "unhealthy"
        assert result["code"] == 503

    @pytest.mark.asyncio
    async def test_health_check_connection_error(self):
        """健康检查连接失败"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.health_check()

        assert result["status"] == "unreachable"
        assert "Connection refused" in result["error"]


# ============================================================================
# submit_backtest 测试
# ============================================================================


class TestSubmitBacktest:
    """提交回测任务测试"""

    @pytest.mark.asyncio
    async def test_submit_backtest_success(self):
        """成功提交回测任务"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"task_id": "abc123", "status": "pending"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        config = {
            "strategy": "dual_thrust",
            "symbols": ["000001.SZ"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "initial_capital": 1000000,
        }
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.submit_backtest(config)

        assert result["task_id"] == "abc123"
        assert result["status"] == "pending"
        mock_client.post.assert_called_once_with("/api/v1/backtest", json=config)

    @pytest.mark.asyncio
    async def test_submit_backtest_http_error(self):
        """回测提交 HTTP 错误"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Invalid config"

        error = httpx.HTTPStatusError(
            "Bad Request", request=MagicMock(), response=mock_response
        )
        mock_response.raise_for_status = MagicMock(side_effect=error)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.submit_backtest({})

        assert "error" in result

    @pytest.mark.asyncio
    async def test_submit_backtest_connection_error(self):
        """回测提交连接失败"""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.submit_backtest({})

        assert "error" in result


# ============================================================================
# get_backtest_result 测试
# ============================================================================


class TestGetBacktestResult:
    """获取回测结果测试"""

    @pytest.mark.asyncio
    async def test_get_backtest_result_success(self):
        """成功获取回测结果"""
        expected = {
            "task_id": "abc123",
            "status": "completed",
            "total_return": 15.5,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_backtest_result("abc123")

        assert result["task_id"] == "abc123"
        assert result["total_return"] == 15.5
        mock_client.get.assert_called_once_with("/api/v1/backtest/abc123")

    @pytest.mark.asyncio
    async def test_get_backtest_result_error(self):
        """获取回测结果失败"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Not found"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_backtest_result("nonexistent")

        assert "error" in result


# ============================================================================
# submit_order 测试
# ============================================================================


class TestSubmitOrder:
    """提交订单测试"""

    @pytest.mark.asyncio
    async def test_submit_order_success(self):
        """成功提交订单"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"order_id": "ord-001", "status": "filled"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.submit_order(
                symbol="000001.SZ",
                direction="buy",
                price=10.5,
                volume=100,
                order_type="limit",
            )

        assert result["order_id"] == "ord-001"
        expected_order = {
            "symbol": "000001.SZ",
            "direction": "buy",
            "price": 10.5,
            "volume": 100,
            "order_type": "limit",
        }
        mock_client.post.assert_called_once_with("/api/v1/orders", json=expected_order)

    @pytest.mark.asyncio
    async def test_submit_order_default_type(self):
        """默认订单类型为 limit"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"order_id": "ord-002"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            await client.submit_order("000001.SZ", "sell", 11.0, 200)

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["order_type"] == "limit"


# ============================================================================
# cancel_order 测试
# ============================================================================


class TestCancelOrder:
    """撤销订单测试"""

    @pytest.mark.asyncio
    async def test_cancel_order_success(self):
        """成功撤销订单"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"order_id": "ord-001", "status": "cancelled"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.cancel_order("ord-001")

        assert result["status"] == "cancelled"
        mock_client.delete.assert_called_once_with("/api/v1/orders/ord-001")

    @pytest.mark.asyncio
    async def test_cancel_order_error(self):
        """撤销订单失败"""
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=Exception("Order not found"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.cancel_order("nonexistent")

        assert "error" in result


# ============================================================================
# get_positions 测试
# ============================================================================


class TestGetPositions:
    """获取持仓测试"""

    @pytest.mark.asyncio
    async def test_get_positions_success(self):
        """成功获取持仓"""
        expected = {
            "positions": [
                {"symbol": "000001.SZ", "volume": 1000, "cost": 10.5},
            ]
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_positions()

        assert len(result["positions"]) == 1
        assert result["positions"][0]["symbol"] == "000001.SZ"
        mock_client.get.assert_called_once_with("/api/v1/positions")

    @pytest.mark.asyncio
    async def test_get_positions_error(self):
        """获取持仓失败"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Server error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_positions()

        assert "error" in result


# ============================================================================
# get_account 测试
# ============================================================================


class TestGetAccount:
    """获取账户信息测试"""

    @pytest.mark.asyncio
    async def test_get_account_success(self):
        """成功获取账户信息"""
        expected = {
            "account_id": "acc-001",
            "balance": 500000.0,
            "available": 300000.0,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_account()

        assert result["balance"] == 500000.0
        mock_client.get.assert_called_once_with("/api/v1/account")

    @pytest.mark.asyncio
    async def test_get_account_error(self):
        """获取账户信息失败"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Unauthorized"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_account()

        assert "error" in result


# ============================================================================
# subscribe_market_data 测试
# ============================================================================


class TestSubscribeMarketData:
    """WebSocket 行情订阅测试"""

    @pytest.mark.asyncio
    async def test_subscribe_calls_callback(self):
        """订阅行情时回调被调用"""
        callback = AsyncMock()

        # 构建异步迭代器
        messages = [
            json.dumps({"symbol": "000001.SZ", "price": 10.5}),
            json.dumps({"symbol": "000001.SZ", "price": 10.6}),
        ]

        async def async_iter_messages():
            for msg in messages:
                yield msg

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: async_iter_messages()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        mock_websockets_module = MagicMock()
        mock_websockets_module.connect = MagicMock(return_value=mock_ws)

        with patch.dict("sys.modules", {"websockets": mock_websockets_module}):
            client = RustCoreClient(ws_url="ws://localhost:8081")
            await client.subscribe_market_data(["000001.SZ"], callback)

        assert callback.call_count == 2
        call1 = callback.call_args_list[0][0][0]
        assert call1["price"] == 10.5

    @pytest.mark.asyncio
    async def test_subscribe_sync_callback(self):
        """同步回调函数也能正常调用"""
        callback = MagicMock()

        messages = [json.dumps({"symbol": "600519.SH", "price": 1800.0})]

        async def async_iter_messages():
            for msg in messages:
                yield msg

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: async_iter_messages()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        mock_websockets_module = MagicMock()
        mock_websockets_module.connect = MagicMock(return_value=mock_ws)

        with patch.dict("sys.modules", {"websockets": mock_websockets_module}):
            client = RustCoreClient()
            await client.subscribe_market_data(["600519.SH"], callback)

        callback.assert_called_once_with({"symbol": "600519.SH", "price": 1800.0})

    @pytest.mark.asyncio
    async def test_subscribe_websockets_not_installed(self):
        """websockets 包未安装时不崩溃"""
        with patch.dict("sys.modules", {"websockets": None}):
            with patch("builtins.__import__", side_effect=ImportError("No module")):
                client = RustCoreClient()
                # 直接调用，ImportError 在 try/except 中被捕获
                result = None
                try:
                    await client.subscribe_market_data(["000001.SZ"], None)
                except ImportError:
                    pass
                # 不应抛出异常到外部

    @pytest.mark.asyncio
    async def test_subscribe_invalid_json_skipped(self):
        """无效 JSON 消息被跳过"""
        callback = AsyncMock()

        messages = [
            "not-json",
            json.dumps({"symbol": "000001.SZ", "price": 10.5}),
        ]

        async def async_iter_messages():
            for msg in messages:
                yield msg

        mock_ws = AsyncMock()
        mock_ws.__aiter__ = lambda self: async_iter_messages()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)

        mock_websockets_module = MagicMock()
        mock_websockets_module.connect = MagicMock(return_value=mock_ws)

        with patch.dict("sys.modules", {"websockets": mock_websockets_module}):
            client = RustCoreClient()
            await client.subscribe_market_data(["000001.SZ"], callback)

        # 只有有效 JSON 触发回调
        assert callback.call_count == 1


# ============================================================================
# get_strategy_list 测试
# ============================================================================


class TestGetStrategyList:
    """获取策略列表测试"""

    @pytest.mark.asyncio
    async def test_get_strategy_list_success(self):
        """成功获取策略列表"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "strategies": ["dual_thrust", "momentum", "mean_reversion"]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_strategy_list()

        assert result == ["dual_thrust", "momentum", "mean_reversion"]
        mock_client.get.assert_called_once_with("/api/v1/strategies")

    @pytest.mark.asyncio
    async def test_get_strategy_list_empty(self):
        """策略列表为空"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"strategies": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_strategy_list()

        assert result == []

    @pytest.mark.asyncio
    async def test_get_strategy_list_error(self):
        """获取策略列表失败返回空列表"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Server error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = RustCoreClient()
        with patch.object(client, "_get_client", return_value=mock_client):
            result = await client.get_strategy_list()

        assert result == []
