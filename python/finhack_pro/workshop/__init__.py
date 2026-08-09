"""
创意工坊（Workshop）模块

策略包的打包、安装、安全扫描、注册表管理。
支持从社区（未来 CloudBase / GitHub 后端）下载策略包一键安装。

设计：
- manifest.yaml：策略包标准元数据（id/version/entry/params_schema）
- PackageScanner：AST 静态安全检查（危险模块/内建/方法）
- PackageManager：打包 / 安装 / 卸载 / 注册表
- 内置策略以 allowlist 白名单身份打包，社区包强制扫描

Usage:
    from finhack_pro.workshop import PackageManager, StrategyManifest, PackageScanner

    manager = PackageManager(workshop_dir="data/workshop")
    installed = manager.install("data/workshop/dual_thrust_a-v1.2.0.zip")
"""

from finhack_pro.workshop.manifest import (
    MANIFEST_FILENAME,
    ManifestError,
    StrategyManifest,
)
from finhack_pro.workshop.packager import (
    InstalledPackage,
    PackageManager,
)
from finhack_pro.workshop.security import (
    PackageScanner,
    SecurityIssue,
)

__all__ = [
    "StrategyManifest",
    "ManifestError",
    "MANIFEST_FILENAME",
    "PackageManager",
    "InstalledPackage",
    "PackageScanner",
    "SecurityIssue",
]
