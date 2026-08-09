"""
策略包格式与元数据模型 - Strategy Package Manifest

定义创意工坊流通的策略包标准格式（manifest.yaml + 代码 + 资源），
与版本管理、依赖声明、参数 schema 声明解耦。

Usage:
    manifest = StrategyManifest.from_dict({
        "id": "dual_thrust_a",
        "name": "双通道突破",
        "version": "1.2.0",
        "author": "finhack",
        ...
    })
    manifest.validate()
    pkg = manifest.to_package_dict()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

# 支持的包类型
PKG_TYPE_STRATEGY = "strategy"
PKG_TYPE_INDICATOR = "indicator"
PKG_TYPE_AGENT = "agent"
SUPPORTED_TYPES = (PKG_TYPE_STRATEGY, PKG_TYPE_INDICATOR, PKG_TYPE_AGENT)

# manifest 文件名
MANIFEST_FILENAME = "manifest.yaml"


class ManifestError(ValueError):
    """manifest 解析/校验错误"""


@dataclass
class StrategyManifest:
    """策略包元数据

    Attributes:
        id: 全局唯一 ID（如 dual_thrust_a）
        name: 显示名称
        version: 语义化版本（如 1.2.0）
        author: 作者
        description: 描述
        type: 包类型（strategy / indicator / agent）
        entry: 入口文件（如 strategy.py）
        entry_class: 策略类名（如 DualThrustStrategy）
        params_schema: 参数 JSON Schema（用于 WebUI 自动生成表单）
        deps: 依赖声明（Python 包名列表）
        benchmark: 回测报告（作者提交，JSON）
        preview: 封面图文件名
        created_at: 创建时间
        updated_at: 更新时间
    """

    id: str
    name: str
    version: str
    author: str = "anonymous"
    description: str = ""
    type: str = PKG_TYPE_STRATEGY
    entry: str = "strategy.py"
    entry_class: str = ""
    params_schema: Dict[str, Any] = field(default_factory=dict)
    deps: List[str] = field(default_factory=list)
    benchmark: Dict[str, Any] = field(default_factory=dict)
    preview: str = ""
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------
    # 解析与序列化
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyManifest":
        """从字典构建（宽松解析，缺失字段用默认值）"""
        return cls(
            id=str(data.get("id", "")).strip(),
            name=str(data.get("name", "")).strip(),
            version=str(data.get("version", "")).strip(),
            author=str(data.get("author", "anonymous")).strip(),
            description=str(data.get("description", "")).strip(),
            type=str(data.get("type", PKG_TYPE_STRATEGY)).strip(),
            entry=str(data.get("entry", "strategy.py")).strip(),
            entry_class=str(data.get("entry_class", "")).strip(),
            params_schema=data.get("params_schema") or {},
            deps=list(data.get("deps") or []),
            benchmark=data.get("benchmark") or {},
            preview=str(data.get("preview", "")).strip(),
            created_at=str(data.get("created_at", "")).strip(),
            updated_at=str(data.get("updated_at", "")).strip(),
        )

    @classmethod
    def from_yaml(cls, text: str) -> "StrategyManifest":
        """从 YAML 文本解析"""
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ManifestError(f"YAML 解析失败: {e}") from e
        if not isinstance(data, dict):
            raise ManifestError("manifest.yaml 顶层必须是映射")
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, path: str) -> "StrategyManifest":
        """从文件解析"""
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            raise ManifestError(f"manifest 文件不存在: {path}")
        return cls.from_yaml(p.read_text(encoding="utf-8"))

    def to_dict(self) -> Dict[str, Any]:
        """转字典（序列化用）"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "description": self.description,
            "type": self.type,
            "entry": self.entry,
            "entry_class": self.entry_class,
            "params_schema": self.params_schema,
            "deps": self.deps,
            "benchmark": self.benchmark,
            "preview": self.preview,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_yaml(self) -> str:
        """转 YAML 文本"""
        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """校验必填字段，返回错误列表（空列表 = 通过）"""
        errors: List[str] = []
        if not self.id:
            errors.append("id 不能为空")
        if not self.name:
            errors.append("name 不能为空")
        if not self.version:
            errors.append("version 不能为空")
        if self.type not in SUPPORTED_TYPES:
            errors.append(f"type 必须为 {SUPPORTED_TYPES} 之一，当前: {self.type}")
        if not self.entry:
            errors.append("entry 不能为空")
        return errors

    def assert_valid(self) -> None:
        """校验，失败抛 ManifestError"""
        errors = self.validate()
        if errors:
            raise ManifestError("; ".join(errors))

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def touch(self) -> None:
        """更新 updated_at / created_at 时间戳"""
        now = datetime.now().isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = now
        self.updated_at = now

    @property
    def package_id(self) -> str:
        """包唯一标识（id@version）"""
        return f"{self.id}@{self.version}"

    @staticmethod
    def default_params_schema() -> Dict[str, Any]:
        """默认参数 schema（双均线示例，策略可覆盖）"""
        return {
            "type": "object",
            "properties": {
                "fast_period": {"type": "integer", "minimum": 1, "default": 5, "title": "快线周期"},
                "slow_period": {"type": "integer", "minimum": 2, "default": 20, "title": "慢线周期"},
            },
            "required": ["fast_period", "slow_period"],
        }
