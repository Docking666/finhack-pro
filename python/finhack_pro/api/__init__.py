"""
FinHack Pro API客户端模块

提供与Rust核心通信的HTTP/WebSocket客户端，以及API文档自动化生成。
"""

from finhack_pro.api.client import RustCoreClient
from finhack_pro.api.openapi import APIDocGenerator, EndpointDoc

__all__ = ["RustCoreClient", "APIDocGenerator", "EndpointDoc"]
