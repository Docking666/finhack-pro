"""
创意工坊 - 策略包打包与安装

- 打包：把策略目录打包为 zip（manifest + 代码 + 资源）
- 安装：校验 → 安全扫描 → 解压到策略目录 → 注册
- 卸载 / 升级 / 本地注册表管理

Usage:
    # 打包
    packager = PackageManager(workshop_dir="data/workshop")
    pkg_path = packager.pack(
        strategy_dir="finhack_pro/strategies",
        manifest=manifest,
    )

    # 安装（从 zip）
    installed = packager.install("data/workshop/dual_thrust_a-v1.2.0.zip")

    # 列出已安装
    packager.list_installed()
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from finhack_pro.utils.logger import get_logger
from finhack_pro.workshop.manifest import MANIFEST_FILENAME, ManifestError, StrategyManifest
from finhack_pro.workshop.security import PackageScanner, SecurityIssue

logger = get_logger(__name__)


@dataclass
class InstalledPackage:
    """已安装的策略包记录"""
    manifest: StrategyManifest
    install_dir: str
    installed_at: str = ""
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "install_dir": self.install_dir,
            "installed_at": self.installed_at,
            "active": self.active,
        }


class PackageManager:
    """策略包管理器"""

    def __init__(
        self,
        workshop_dir: str = "data/workshop",
        strategies_dir: str = "finhack_pro/strategies",
        allowlist_scope: Optional[str] = None,
    ):
        """
        Args:
            workshop_dir: 工坊本地目录（存放下载的 zip）
            strategies_dir: 策略安装目录
            allowlist_scope: 安全扫描白名单（"finhack" 表示内置包跳过扫描）
        """
        self.workshop_dir = Path(workshop_dir)
        self.strategies_dir = Path(strategies_dir)
        self.workshop_dir.mkdir(parents=True, exist_ok=True)
        self.strategies_dir.mkdir(parents=True, exist_ok=True)
        self.scanner = PackageScanner(allowlist_scope=allowlist_scope)
        self._registry_file = self.workshop_dir / "registry.json"

    # ------------------------------------------------------------------
    # 打包
    # ------------------------------------------------------------------
    def pack(
        self,
        strategy_dir: str,
        manifest: StrategyManifest,
        out_dir: Optional[str] = None,
    ) -> Path:
        """打包策略目录为 zip

        Args:
            strategy_dir: 策略源码目录
            manifest: 策略元数据
            out_dir: 输出目录（默认 workshop_dir）

        Returns:
            zip 文件路径
        """
        src = Path(strategy_dir)
        if not src.exists():
            raise ManifestError(f"策略目录不存在: {strategy_dir}")

        manifest.assert_valid()
        manifest.touch()

        out = Path(out_dir) if out_dir else self.workshop_dir
        out.mkdir(parents=True, exist_ok=True)
        pkg_path = out / f"{manifest.id}-v{manifest.version}.zip"

        with zipfile.ZipFile(pkg_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # manifest.yaml 写入根目录
            zf.writestr(MANIFEST_FILENAME, manifest.to_yaml())
            # 策略代码 + 资源
            for f in sorted(src.rglob("*")):
                if f.is_file() and not f.name.endswith((".pyc", ".pyo")):
                    rel = f.relative_to(src)
                    zf.write(f, arcname=str(rel))

        logger.info(f"[Workshop] 打包完成: {pkg_path}")
        return pkg_path

    # ------------------------------------------------------------------
    # 安装
    # ------------------------------------------------------------------
    def install(
        self,
        package_path: str,
        force: bool = False,
        scan: bool = True,
    ) -> InstalledPackage:
        """安装策略包

        Args:
            package_path: zip 包路径
            force: 同版本已安装时是否覆盖
            scan: 是否执行安全扫描

        Returns:
            InstalledPackage
        """
        pkg = Path(package_path)
        if not pkg.exists():
            raise ManifestError(f"策略包不存在: {package_path}")

        # 解压到临时目录
        tmp_dir = self.workshop_dir / f".tmp_{pkg.stem}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        try:
            with zipfile.ZipFile(pkg, "r") as zf:
                # zip 路径穿越防护
                for member in zf.namelist():
                    target = (tmp_dir / member).resolve()
                    if not str(target).startswith(str(tmp_dir.resolve())):
                        raise ManifestError(f"策略包包含非法路径: {member}")
                zf.extractall(tmp_dir)

            # 解析 manifest
            manifest_file = tmp_dir / MANIFEST_FILENAME
            if not manifest_file.exists():
                raise ManifestError(f"策略包缺少 {MANIFEST_FILENAME}")
            manifest = StrategyManifest.from_yaml_file(str(manifest_file))
            manifest.assert_valid()

            # 安全扫描
            if scan:
                issues = self.scanner.scan_package(str(tmp_dir), manifest.entry)
                high_issues = [i for i in issues if i.severity == "high"]
                if high_issues:
                    detail = "; ".join(i.message for i in high_issues[:5])
                    raise ManifestError(f"策略包存在高危安全问题，拒绝安装: {detail}")

            # 安装到策略目录
            target_dir = self.strategies_dir / manifest.id
            if target_dir.exists():
                if force:
                    shutil.rmtree(target_dir)
                else:
                    raise ManifestError(
                        f"策略已存在: {manifest.id}（使用 force=True 覆盖）"
                    )
            shutil.copytree(tmp_dir, target_dir)

            installed = InstalledPackage(
                manifest=manifest,
                install_dir=str(target_dir),
                installed_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._save_registry_entry(installed)
            logger.info(f"[Workshop] 安装成功: {manifest.package_id} → {target_dir}")
            return installed

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 卸载 / 注册表
    # ------------------------------------------------------------------
    def uninstall(self, package_id: str) -> bool:
        """卸载策略包"""
        registry = self._load_registry()
        removed = False
        for pkg_id, entry in list(registry.items()):
            if pkg_id == package_id or pkg_id.startswith(f"{package_id}@"):
                install_dir = Path(entry.get("install_dir", ""))
                if install_dir.exists():
                    shutil.rmtree(install_dir, ignore_errors=True)
                del registry[pkg_id]
                removed = True
        if removed:
            self._save_registry(registry)
        return removed

    def list_installed(self) -> List[InstalledPackage]:
        """列出已安装策略包"""
        registry = self._load_registry()
        result: List[InstalledPackage] = []
        for entry in registry.values():
            try:
                m = StrategyManifest.from_dict(entry.get("manifest", {}))
                result.append(InstalledPackage(
                    manifest=m,
                    install_dir=entry.get("install_dir", ""),
                    installed_at=entry.get("installed_at", ""),
                    active=entry.get("active", True),
                ))
            except Exception:
                continue
        return result

    def get(self, package_id: str) -> Optional[InstalledPackage]:
        """按 ID 查询已安装包"""
        for pkg in self.list_installed():
            if pkg.manifest.id == package_id or pkg.manifest.package_id == package_id:
                return pkg
        return None

    # ------------------------------------------------------------------
    # 内部：注册表
    # ------------------------------------------------------------------
    def _load_registry(self) -> Dict[str, Dict[str, Any]]:
        if not self._registry_file.exists():
            return {}
        try:
            return json.loads(self._registry_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_registry(self, registry: Dict[str, Dict[str, Any]]) -> None:
        self._registry_file.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save_registry_entry(self, pkg: InstalledPackage) -> None:
        registry = self._load_registry()
        registry[pkg.manifest.package_id] = pkg.to_dict()
        self._save_registry(registry)
