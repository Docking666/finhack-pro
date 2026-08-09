"""
创意工坊 API 路由

提供策略包的浏览、上传（本地打包）、安装、卸载、注册表查询等接口。
社区后端（CloudBase / GitHub）接入后，将在此追加远端列表/下载端点。

Usage:
    GET  /api/workshop/packages        # 列出已安装策略包
    POST /api/workshop/install         # 安装本地 zip 包
    POST /api/workshop/pack            # 打包策略目录为 zip
    POST /api/workshop/{id}/uninstall  # 卸载
    POST /api/workshop/scan            # 安全扫描（仅检测，不安装）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from finhack_pro.webui.models import APIResponse
from finhack_pro.workshop import ManifestError, PackageManager, PackageScanner, StrategyManifest

router = APIRouter(prefix="/api/workshop", tags=["workshop"])


# ============================================================
# 请求模型
# ============================================================

class InstallRequest(BaseModel):
    """安装请求（服务器端已存在的 zip 路径）"""
    package_path: str = Field(..., description="策略包 zip 路径")
    force: bool = Field(False, description="同版本已安装时是否覆盖")


class PackRequest(BaseModel):
    """打包请求"""
    strategy_dir: str = Field(..., description="策略源码目录")
    id: str = Field(..., description="策略 ID")
    name: str = Field(..., description="策略名称")
    version: str = Field("1.0.0", description="版本")
    author: str = Field("anonymous", description="作者")
    description: str = Field("", description="描述")
    entry_class: str = Field("", description="策略类名")
    params_schema: Dict[str, Any] = Field(default_factory=dict, description="参数 JSON Schema")


class ScanRequest(BaseModel):
    """安全扫描请求"""
    code: str = Field(..., description="策略代码")


def _get_manager() -> PackageManager:
    """获取默认 PackageManager（内置包白名单）"""
    return PackageManager(
        workshop_dir="data/workshop",
        strategies_dir="finhack_pro/strategies",
        allowlist_scope="finhack",
    )


# ============================================================
# 路由
# ============================================================

@router.get("/packages", response_model=APIResponse)
async def list_packages() -> APIResponse:
    """列出已安装策略包"""
    try:
        manager = _get_manager()
        packages = manager.list_installed()
        return APIResponse(success=True, data=[p.to_dict() for p in packages])
    except Exception as e:
        logger.error(f"[Workshop] 列出失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/install", response_model=APIResponse)
async def install_package(req: InstallRequest) -> APIResponse:
    """安装策略包（服务器端路径）"""
    try:
        manager = _get_manager()
        installed = manager.install(req.package_path, force=req.force)
        return APIResponse(success=True, data=installed.to_dict(), message="安装成功")
    except ManifestError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"[Workshop] 安装失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/install/upload", response_model=APIResponse)
async def install_upload(file: UploadFile = File(...), force: bool = False) -> APIResponse:
    """上传 zip 包并安装"""
    try:
        from finhack_pro.workshop import PackageManager
        manager = PackageManager(
            workshop_dir="data/workshop",
            strategies_dir="finhack_pro/strategies",
        )
        save_path = Path("data/workshop") / file.filename
        save_path.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        save_path.write_bytes(content)
        installed = manager.install(str(save_path), force=force)
        return APIResponse(success=True, data=installed.to_dict(), message="安装成功")
    except ManifestError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"[Workshop] 上传安装失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/pack", response_model=APIResponse)
async def pack_package(req: PackRequest) -> APIResponse:
    """打包策略目录为 zip"""
    try:
        manager = _get_manager()
        manifest = StrategyManifest.from_dict({
            "id": req.id,
            "name": req.name,
            "version": req.version,
            "author": req.author,
            "description": req.description,
            "type": "strategy",
            "entry": "strategy.py",
            "entry_class": req.entry_class,
            "params_schema": req.params_schema or StrategyManifest.default_params_schema(),
        })
        pkg_path = manager.pack(strategy_dir=req.strategy_dir, manifest=manifest)
        return APIResponse(success=True, data={"path": str(pkg_path), "name": pkg_path.name}, message="打包成功")
    except ManifestError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"[Workshop] 打包失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/{package_id}/uninstall", response_model=APIResponse)
async def uninstall_package(package_id: str) -> APIResponse:
    """卸载策略包"""
    try:
        manager = _get_manager()
        removed = manager.uninstall(package_id)
        if not removed:
            raise HTTPException(status_code=404, detail=f"未找到策略包: {package_id}")
        return APIResponse(success=True, message="卸载成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Workshop] 卸载失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/scan", response_model=APIResponse)
async def scan_code(req: ScanRequest) -> APIResponse:
    """安全扫描策略代码（仅检测，不安装）"""
    try:
        scanner = PackageScanner()
        issues = scanner.scan_code(req.code)
        high = [i for i in issues if i.severity == "high"]
        return APIResponse(
            success=len(high) == 0,
            data={"issues": [i.to_dict() for i in issues], "safe": len(high) == 0},
            message="扫描通过" if not high else f"发现 {len(high)} 个高危问题",
        )
    except Exception as e:
        logger.error(f"[Workshop] 扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
