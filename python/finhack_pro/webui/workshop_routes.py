"""
创意工坊 API 路由

提供策略包的浏览、上传（本地打包）、安装、卸载、注册表查询等接口。
已接入 CloudBase 云端（云函数 API + 云数据库 + 云存储），支持云端浏览/下载/上传。

Usage:
    GET  /api/workshop/packages            # 列出已安装策略包
    POST /api/workshop/install             # 安装本地 zip 包
    POST /api/workshop/share-generated     # 分享策略工坊生成的策略代码
    POST /api/workshop/pack                # 打包策略目录为 zip
    POST /api/workshop/{id}/uninstall      # 卸载
    POST /api/workshop/scan                # 安全扫描（仅检测，不安装）
    # 云端（CloudBase）
    GET  /api/workshop/cloud/packages      # 浏览云端策略市场
    GET  /api/workshop/cloud/packages/{id} # 云端策略详情
    POST /api/workshop/cloud/install       # 下载云端策略并安装到本地
    POST /api/workshop/cloud/upload        # 上传本地策略包到云端
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from finhack_pro.webui.models import APIResponse
from finhack_pro.workshop import (
    ManifestError,
    PackageManager,
    PackageScanner,
    StrategyManifest,
    WorkshopCloud,
    WorkshopCloudError,
)

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


class ShareGeneratedRequest(BaseModel):
    """分享策略工坊生成的策略代码"""
    code: str = Field(..., description="策略代码（LLM 生成）")
    name: str = Field("生成的策略", description="策略名称")
    description: str = Field("", description="策略描述")
    version: str = Field("0.1.0", description="版本")
    author: str = Field("workshop", description="作者")
    entry_class: str = Field("", description="策略类名")
    params_schema: Dict[str, Any] = Field(default_factory=dict, description="参数 JSON Schema")
    strategy_id: str = Field("", description="策略 ID（留空自动生成）")


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


@router.post("/share-generated", response_model=APIResponse)
async def share_generated(req: ShareGeneratedRequest) -> APIResponse:
    """分享策略工坊生成的策略代码

    把 LLM 生成的策略代码打包为标准工坊 zip（manifest + strategy.py），
    输出到 workshop 目录供下载分发；同时返回包 ID，安装方可直接安装。

    - 自动生成策略 ID（gen_ 前缀 + 随机串，可指定 strategy_id 覆盖）
    - 分享前执行安全扫描：含高危调用则拒绝分享
    """
    import re
    import tempfile
    import uuid

    # 1. 安全扫描（分享的代码会被他人安装执行，必须过安全关）
    scanner = PackageScanner()
    issues = scanner.scan_code(req.code)
    high_issues = [i for i in issues if i.severity == "high"]
    if high_issues:
        detail = "; ".join(i.message for i in high_issues[:5])
        raise HTTPException(
            status_code=400,
            detail=f"策略代码含高危安全风险，禁止分享: {detail}",
        )

    # 2. 构造策略 ID 与 manifest
    pkg_id = req.strategy_id.strip() or f"gen_{uuid.uuid4().hex[:10]}"
    manifest = StrategyManifest.from_dict({
        "id": pkg_id,
        "name": req.name,
        "version": req.version,
        "author": req.author,
        "description": req.description,
        "type": "strategy",
        "entry": "strategy.py",
        "entry_class": req.entry_class,
        "params_schema": req.params_schema or StrategyManifest.default_params_schema(),
    })
    manifest.touch()

    # 3. 写入临时目录并打包
    manager = _get_manager()
    with tempfile.TemporaryDirectory(prefix="workshop_share_") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "strategy.py").write_text(req.code, encoding="utf-8")
        (tmp_dir / "manifest.yaml").write_text(manifest.to_yaml(), encoding="utf-8")
        pkg_path = manager.pack(strategy_dir=str(tmp_dir), manifest=manifest)

    return APIResponse(
        success=True,
        data={
            "path": str(pkg_path),
            "name": pkg_path.name,
            "package_id": manifest.package_id,
            "strategy_id": pkg_id,
            "manifest": manifest.to_dict(),
        },
        message="分享成功",
    )


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


# ============================================================
# 云端工坊（CloudBase）
# ============================================================

class CloudInstallRequest(BaseModel):
    """云端安装请求"""
    package_id: str = Field(..., description="云端策略 ID")
    force: bool = Field(False, description="同版本已安装时是否覆盖")


class CloudUploadRequest(BaseModel):
    """云端上传请求"""
    zip_path: str = Field(..., description="本地策略包 zip 路径")
    name: str = Field("", description="策略名称（默认取 ID）")
    version: str = Field("", description="版本（默认从文件名推导）")
    author: str = Field("anonymous", description="作者")
    description: str = Field("", description="描述")
    entry_class: str = Field("", description="策略类名")
    package_id: str = Field("", description="策略 ID（默认从文件名推导）")


def _get_cloud() -> WorkshopCloud:
    """获取云端客户端"""
    return WorkshopCloud()


@router.get("/cloud/packages", response_model=APIResponse)
async def cloud_list_packages(q: str = "", page: int = 1, page_size: int = 20) -> APIResponse:
    """浏览云端策略市场"""
    try:
        data = _get_cloud().list_packages(keyword=q, page=page, page_size=page_size)
        return APIResponse(success=True, data=data)
    except WorkshopCloudError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error(f"[Workshop] 云端列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/cloud/packages/{package_id}", response_model=APIResponse)
async def cloud_get_package(package_id: str) -> APIResponse:
    """获取云端策略详情"""
    try:
        data = _get_cloud().get_package(package_id)
        return APIResponse(success=True, data=data)
    except WorkshopCloudError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error(f"[Workshop] 云端详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/cloud/install", response_model=APIResponse)
async def cloud_install(req: CloudInstallRequest) -> APIResponse:
    """下载云端策略并安装到本地"""
    try:
        installed = _get_cloud().download_and_install(req.package_id, force=req.force)
        return APIResponse(success=True, data=installed, message="云端策略安装成功")
    except WorkshopCloudError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error(f"[Workshop] 云端安装失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/cloud/upload", response_model=APIResponse)
async def cloud_upload(req: CloudUploadRequest) -> APIResponse:
    """上传本地策略包到云端"""
    try:
        data = _get_cloud().upload_package(
            zip_path=req.zip_path,
            package_id=req.package_id,
            name=req.name,
            version=req.version,
            author=req.author,
            description=req.description,
            entry_class=req.entry_class,
        )
        return APIResponse(success=True, data=data, message="上传云端成功")
    except WorkshopCloudError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.error(f"[Workshop] 云端上传失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
