"""
创意工坊 - 云端客户端（WorkshopCloud）

通过 CloudBase HTTP 云函数访问云端策略市场：
- 浏览 / 搜索云端策略包
- 下载策略包 zip 并安装到本地
- 上传本地策略包（分享到云端）

Usage:
    from finhack_pro.workshop.cloud import WorkshopCloud

    cloud = WorkshopCloud(
        base_url="https://ad-to-earn-xxx.ap-shanghai.app.tcloudbase.com/api/workshop/api",
    )
    # 浏览
    page = cloud.list_packages(keyword="动量")
    # 下载并安装
    installed = cloud.download_and_install("dual_thrust")
    # 上传分享
    result = cloud.upload_package("data/workshop/dual_thrust-v1.0.0.zip")
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from finhack_pro.utils.logger import get_logger
from finhack_pro.workshop.packager import PackageManager

logger = get_logger(__name__)

# 默认云端 API 地址（CloudBase 环境 ad-to-earn，网关 /api/workshop 路由）
DEFAULT_CLOUD_API = (
    "https://ad-to-earn-d8goxvb2q25d96fc2-1463991490."
    "ap-shanghai.app.tcloudbase.com/api/workshop/api"
)


class WorkshopCloudError(RuntimeError):
    """云端工坊错误"""


class WorkshopCloud:
    """创意工坊云端客户端"""

    def __init__(
        self,
        base_url: str = DEFAULT_CLOUD_API,
        timeout: int = 30,
        workshop_dir: str = "data/workshop",
        strategies_dir: str = "finhack_pro/strategies",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._local = PackageManager(
            workshop_dir=workshop_dir,
            strategies_dir=strategies_dir,
            allowlist_scope="finhack",
        )

    # ------------------------------------------------------------------
    # HTTP 基础
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("error", "")
            except Exception:
                pass
            raise WorkshopCloudError(f"云端请求失败({e.code}): {detail or e.reason}") from e
        except urllib.error.URLError as e:
            raise WorkshopCloudError(f"无法连接云端工坊: {e.reason}") from e

        if not payload.get("success", False):
            raise WorkshopCloudError(f"云端返回错误: {payload.get('error', 'unknown')}")
        return payload.get("data") or {}

    # ------------------------------------------------------------------
    # 浏览 / 搜索
    # ------------------------------------------------------------------
    def list_packages(
        self,
        keyword: str = "",
        page: int = 1,
        page_size: int = 20,
        status: str = "approved",
    ) -> Dict[str, Any]:
        """浏览 / 搜索云端策略包

        Returns:
            {"items": [...], "page": n, "pageSize": n, "total": n}
        """
        params: List[str] = [f"page={page}", f"pageSize={page_size}"]
        if keyword:
            params.append(f"q={urllib.parse.quote(keyword)}")
        if status:
            params.append(f"status={urllib.parse.quote(status)}")
        return self._request("GET", f"/packages?{'&'.join(params)}")

    def get_package(self, package_id: str) -> Dict[str, Any]:
        """获取云端策略包详情"""
        return self._request("GET", f"/packages/{urllib.parse.quote(package_id)}")

    def list_reviews(self, package_id: str) -> List[Dict[str, Any]]:
        """获取策略包评论"""
        data = self._request("GET", f"/packages/{urllib.parse.quote(package_id)}/reviews")
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------
    # 下载 / 安装
    # ------------------------------------------------------------------
    def download(self, package_id: str, save_dir: Optional[str] = None) -> Path:
        """下载策略包 zip 到本地

        Returns:
            保存的 zip 文件路径
        """
        data = self._request("GET", f"/packages/{urllib.parse.quote(package_id)}/download")
        url = data.get("url", "")
        if not url:
            raise WorkshopCloudError(f"策略包 {package_id} 无下载链接")
        file_path = data.get("file_path", f"workshop/{package_id}.zip")
        filename = Path(file_path).name or f"{package_id}.zip"

        out_dir = Path(save_dir) if save_dir else self._local.workshop_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / filename

        try:
            urllib.request.urlretrieve(url, str(target))
        except Exception as e:
            raise WorkshopCloudError(f"下载失败: {e}") from e
        logger.info(f"[WorkshopCloud] 下载完成: {target}")
        return target

    def download_and_install(self, package_id: str, force: bool = False) -> Dict[str, Any]:
        """下载并安装云端策略包到本地

        Returns:
            InstalledPackage.to_dict()
        """
        zip_path = self.download(package_id)
        installed = self._local.install(str(zip_path), force=force, scan=True)
        return installed.to_dict()

    # ------------------------------------------------------------------
    # 上传 / 分享
    # ------------------------------------------------------------------
    def upload_package(
        self,
        zip_path: str,
        package_id: Optional[str] = None,
        name: str = "",
        version: str = "",
        author: str = "anonymous",
        description: str = "",
        entry_class: str = "",
        review_status: str = "approved",
    ) -> Dict[str, Any]:
        """上传本地策略包到云端

        Args:
            zip_path: 本地 zip 包路径
            package_id: 策略 ID（默认取 zip 文件名前缀）
            name: 策略名称
            version: 版本
            author: 作者
            description: 描述
            entry_class: 策略类名
            review_status: 审核状态

        Returns:
            云端创建的策略包记录
        """
        p = Path(zip_path)
        if not p.exists():
            raise WorkshopCloudError(f"策略包不存在: {zip_path}")

        # 从文件名推导 package_id / version（如 dual_thrust-v1.0.0.zip）
        stem = p.stem
        base_id = package_id or stem.split("-v")[0] if "-v" in stem else stem.split(".")[0]
        pkg_version = version or (stem.split("-v")[1] if "-v" in stem else "1.0.0")

        body: Dict[str, Any] = {
            "package_id": base_id,
            "name": name or base_id,
            "version": pkg_version,
            "author": author,
            "description": description,
            "type": "strategy",
            "entry": "strategy.py",
            "entry_class": entry_class,
            "zip_base64": base64.b64encode(p.read_bytes()).decode(),
            "review_status": review_status,
        }
        logger.info(f"[WorkshopCloud] 上传 {p.name} → {base_id}@{pkg_version}")
        return self._request("POST", "/packages", body)

    def rate_package(self, package_id: str, rating: int, comment: str = "", author: str = "anonymous") -> Dict[str, Any]:
        """评分 / 评论策略包"""
        body = {"rating": rating, "comment": comment, "author": author}
        return self._request("POST", f"/packages/{urllib.parse.quote(package_id)}/reviews", body)
