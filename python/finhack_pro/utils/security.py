"""
安全工具模块

提供API密钥安全管理、日志脱敏等功能。
"""
from __future__ import annotations

import os
import re
import hashlib
import base64
from typing import Any, Dict, Optional
from functools import lru_cache


class SecretManager:
    """密钥管理器
    
    提供密钥的安全存储、访问和脱敏功能。
    密钥从环境变量加载，内存中使用混淆存储。
    """
    
    # 敏感字段名模式
    SENSITIVE_PATTERNS = [
        r'.*api[_-]?key.*',
        r'.*secret.*',
        r'.*token.*',
        r'.*password.*',
        r'.*credential.*',
        r'.*private[_-]?key.*',
    ]
    
    def __init__(self, obfuscate: bool = True):
        """初始化密钥管理器
        
        Args:
            obfuscate: 是否在内存中混淆存储密钥
        """
        self._obfuscate = obfuscate
        self._secrets: Dict[str, bytes] = {}  # 混淆后的密钥存储
        self._salt = os.urandom(16)  # 随机盐值
        
    def _encode(self, value: str) -> bytes:
        """混淆编码"""
        if not self._obfuscate:
            return value.encode()
        # 简单的XOR混淆
        key_bytes = value.encode()
        salted = bytes(b ^ self._salt[i % len(self._salt)] for i, b in enumerate(key_bytes))
        return base64.b64encode(salted)
    
    def _decode(self, encoded: bytes) -> str:
        """解码"""
        if not self._obfuscate:
            return encoded.decode()
        decoded = base64.b64decode(encoded)
        original = bytes(b ^ self._salt[i % len(self._salt)] for i, b in enumerate(decoded))
        return original.decode()
    
    def set(self, name: str, value: str) -> None:
        """存储密钥"""
        self._secrets[name] = self._encode(value)
    
    def get(self, name: str, default: str = "") -> str:
        """获取密钥"""
        if name not in self._secrets:
            # 尝试从环境变量加载
            env_value = os.getenv(name.upper(), os.getenv(name, ""))
            if env_value:
                self.set(name, env_value)
                return env_value
            return default
        return self._decode(self._secrets[name])
    
    def load_from_env(self, mapping: Dict[str, str]) -> None:
        """从环境变量批量加载密钥
        
        Args:
            mapping: {内部名称: 环境变量名} 映射
        """
        for internal_name, env_name in mapping.items():
            value = os.getenv(env_name, "")
            if value:
                self.set(internal_name, value)
    
    def load_from_dict(self, data: Dict[str, Any], sensitive_keys: Optional[list] = None) -> None:
        """从字典加载敏感数据
        
        Args:
            data: 配置字典
            sensitive_keys: 需要保护的键名列表
        """
        sensitive_keys = sensitive_keys or []
        for key, value in data.items():
            if isinstance(value, str) and (key in sensitive_keys or self._is_sensitive_key(key)):
                self.set(key, value)
    
    def _is_sensitive_key(self, key: str) -> bool:
        """判断键名是否为敏感字段"""
        key_lower = key.lower()
        for pattern in self.SENSITIVE_PATTERNS:
            if re.match(pattern, key_lower):
                return True
        return False
    
    def mask(self, value: str, visible_chars: int = 4) -> str:
        """脱敏显示
        
        Args:
            value: 原始值
            visible_chars: 可见字符数
            
        Returns:
            脱敏后的字符串
        """
        if not value or len(value) <= visible_chars:
            return "***"
        return value[:visible_chars] + "*" * (len(value) - visible_chars)
    
    def mask_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """递归脱敏字典中的敏感字段
        
        Args:
            data: 原始字典
            
        Returns:
            脱敏后的字典副本
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, dict):
                result[key] = self.mask_dict(value)
            elif isinstance(value, str) and self._is_sensitive_key(key):
                result[key] = self.mask(value)
            else:
                result[key] = value
        return result


# 全局密钥管理器单例
_secret_manager: Optional[SecretManager] = None


def get_secret_manager() -> SecretManager:
    """获取全局密钥管理器"""
    global _secret_manager
    if _secret_manager is None:
        _secret_manager = SecretManager()
        # 自动加载常用密钥
        _secret_manager.load_from_env({
            "openai_api_key": "OPENAI_API_KEY",
            "anthropic_api_key": "ANTHROPIC_API_KEY",
            "tushare_token": "TUSHARE_TOKEN",
            "api_key": "FINHACK_API_KEY",
        })
    return _secret_manager


def mask_secrets(text: str, patterns: Optional[list] = None) -> str:
    """脱敏文本中的密钥
    
    Args:
        text: 原始文本
        patterns: 自定义匹配模式列表
        
    Returns:
        脱敏后的文本
    """
    patterns = patterns or [
        # OpenAI API Key: sk-xxx
        (r'sk-[a-zA-Z0-9]{20,}', 'sk-****'),
        # Anthropic API Key: sk-ant-xxx
        (r'sk-ant-[a-zA-Z0-9-]{20,}', 'sk-ant-****'),
        # Tushare Token: 32位hex
        (r'[a-f0-9]{32}', '****'),
        # 通用API Key模式
        (r'api[_-]?key["\s:=]+["\']?([a-zA-Z0-9_-]{10,})["\']?', 'api_key=****'),
        # Bearer Token
        (r'Bearer\s+[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+', 'Bearer ****'),
    ]
    
    result = text
    for pattern, replacement in patterns:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


class LogSanitizer:
    """日志脱敏过滤器
    
    可集成到loguru等日志框架中，自动脱敏敏感信息。
    """
    
    def __init__(self):
        self._patterns = [
            # OpenAI API Key
            (r'sk-[a-zA-Z0-9]{20,}', 'sk-****'),
            # Anthropic API Key
            (r'sk-ant-[a-zA-Z0-9-]{20,}', 'sk-ant-****'),
            # Tushare Token
            (r'(?<=token["\s:=]+)["\']?[a-f0-9]{32}["\']?', '****'),
            # 通用密码
            (r'(?<=password["\s:=]+)["\']?[^"\s\'"]+["\']?', '****'),
            # JWT Token
            (r'eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*', 'eyJ****'),
        ]
    
    def sanitize(self, message: str) -> str:
        """脱敏日志消息"""
        result = str(message)
        for pattern, replacement in self._patterns:
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
        return result
    
    def __call__(self, message: str) -> str:
        return self.sanitize(message)


# 全局日志脱敏器
_log_sanitizer = LogSanitizer()


def sanitize_log(message: str) -> str:
    """脱敏日志消息（便捷函数）"""
    return _log_sanitizer.sanitize(message)
