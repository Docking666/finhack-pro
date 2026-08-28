"""
主题系统路由

提供主题文件的列表 / 读取 / 保存 / 更新 / 删除，以及壁纸上传。

设计约束（严格按 SDD）：
- 内置主题（static/themes/*.json）**只读**，任何写操作返回 403；
- 用户 / 第三方主题存 data/themes/*.json，可增删改；
- 所有写入必须通过 schema 校验：id 合法字符、mode/scheme 取值合法、色值格式合法，
  缺失的必需 token 用内置默认补齐 —— 避免"半残主题"导致界面错乱；
- 壁纸限制扩展名与大小，文件名做 sanitize，防止路径穿越；
- customCss 按用户决策保留完整自由度（前端仅对非内置主题生效并提示），此处只做长度上限。

主题 token 采用 "R G B" 分量格式（由前端注入器把 hex 转换），
以支持 Tailwind 的透明度修饰符（如 bg-surface/50）。
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from finhack_pro.webui.models import APIResponse

router = APIRouter()

_WEBUI_DIR = Path(__file__).resolve().parent
# 内置主题：随仓库分发，只读
BUILTIN_DIR = _WEBUI_DIR / "static" / "themes"
# 用户主题：运行时可写。parents: [0]=webui, [1]=finhack_pro, [2]=python
USER_DIR = _WEBUI_DIR.parents[1] / "data" / "themes"
WALLPAPER_DIR = USER_DIR / "wallpapers"

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
COLOR_PATTERN = re.compile(r"^(#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}|rgba?\([\d\s.,%]+\))$")

# 必需 token：缺失时用内置默认补齐
REQUIRED_TOKENS = (
    "bg-base", "bg-surface", "bg-elevated", "bg-inset",
    "line", "line-strong", "line-subtle",
    "fg", "fg-muted", "fg-subtle",
    "accent", "accent-fg",
    "up", "down", "ok", "warn", "danger",
)

MAX_WALLPAPER_BYTES = 5 * 1024 * 1024
ALLOWED_WALLPAPER_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_CUSTOM_CSS_CHARS = 100_000


class ThemePayload(BaseModel):
    """主题文件载荷（用于创建 / 更新）。"""

    id: str
    name: str = ""
    version: str = "1.0"
    author: str = ""
    type: str = "user"
    mode: str = "dark"
    scheme: str = "cn"
    wallpaper: Dict[str, Any] | None = None
    tokens: Dict[str, str] = Field(default_factory=dict)
    customCss: str | None = None


def ensure_dirs() -> None:
    """确保用户主题与壁纸目录存在。"""
    USER_DIR.mkdir(parents=True, exist_ok=True)
    WALLPAPER_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"主题文件 JSON 解析失败: {path.name}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"主题文件读取失败: {path.name}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail=f"主题文件格式错误（应为对象）: {path.name}")
    return data


def _summary(data: Dict[str, Any], builtin: bool) -> Dict[str, Any]:
    tokens = data.get("tokens") or {}
    return {
        "id": data.get("id"),
        "name": data.get("name") or data.get("id"),
        "author": data.get("author", ""),
        "version": data.get("version", "1.0"),
        "type": "builtin" if builtin else (data.get("type") or "user"),
        "mode": data.get("mode", "dark"),
        "scheme": data.get("scheme", "cn"),
        "hasWallpaper": bool(data.get("wallpaper")),
        "hasCustomCss": bool(data.get("customCss")),
        "preview": {
            "bg": tokens.get("bg-base"),
            "fg": tokens.get("fg"),
            "up": tokens.get("up"),
            "down": tokens.get("down"),
        },
        "editable": not builtin,
    }


def _validate(payload: Dict[str, Any]) -> None:
    """校验主题载荷，不合法直接抛 400。"""
    theme_id = str(payload.get("id", ""))
    if not ID_PATTERN.match(theme_id):
        raise HTTPException(
            status_code=400,
            detail="主题 id 只能包含小写字母、数字、- 和 _，且以字母或数字开头（最长 40 字符）",
        )
    if payload.get("mode") not in ("dark", "light"):
        raise HTTPException(status_code=400, detail="mode 只能是 dark 或 light")
    if payload.get("scheme") not in ("cn", "us"):
        raise HTTPException(status_code=400, detail="scheme 只能是 cn（涨红跌绿）或 us（涨绿跌红）")
    if str(payload.get("type", "user")) == "builtin":
        raise HTTPException(status_code=400, detail="不允许创建 type=builtin 的主题（内置主题只读）")

    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        raise HTTPException(status_code=400, detail="tokens 必须是对象")
    illegal = [v for v in tokens.values() if not COLOR_PATTERN.match(str(v))]
    if illegal:
        raise HTTPException(status_code=400, detail=f"存在非法色值（需为 #RGB / #RRGGBB / rgba()）: {illegal[:3]}")

    css = payload.get("customCss")
    if css is not None:
        if not isinstance(css, str):
            raise HTTPException(status_code=400, detail="customCss 必须是字符串")
        if len(css) > MAX_CUSTOM_CSS_CHARS:
            raise HTTPException(status_code=400, detail=f"customCss 超过上限 {MAX_CUSTOM_CSS_CHARS} 字符")


def _fill_missing_tokens(payload: Dict[str, Any]) -> None:
    """缺失的必需 token 用内置暗色主题补齐，避免半残主题。"""
    default_path = BUILTIN_DIR / "mono-dark.json"
    defaults: Dict[str, Any] = {}
    if default_path.exists():
        defaults = _load_json(default_path).get("tokens", {}) or {}
    tokens = payload.setdefault("tokens", {})
    for key in REQUIRED_TOKENS:
        if not tokens.get(key) and defaults.get(key):
            tokens[key] = defaults[key]


def _user_path(theme_id: str) -> Path:
    if not ID_PATTERN.match(theme_id):
        raise HTTPException(status_code=400, detail="非法的主题 id")
    return USER_DIR / f"{theme_id}.json"


# ============================================================
# 端点
# ============================================================

@router.get("/api/themes", response_model=APIResponse)
async def list_themes() -> APIResponse:
    """列出所有可用主题（内置 + 用户），返回摘要。"""
    ensure_dirs()
    items: list[Dict[str, Any]] = []

    if BUILTIN_DIR.exists():
        for path in sorted(BUILTIN_DIR.glob("*.json")):
            try:
                items.append(_summary(_load_json(path), builtin=True))
            except HTTPException:
                continue

    for path in sorted(USER_DIR.glob("*.json")):
        try:
            items.append(_summary(_load_json(path), builtin=False))
        except HTTPException:
            continue

    return APIResponse(success=True, message="", data={"items": items, "total": len(items)})


@router.get("/api/themes/{theme_id}", response_model=APIResponse)
async def get_theme(theme_id: str) -> APIResponse:
    """读取完整主题内容。内置优先，其次用户主题。"""
    ensure_dirs()
    if not ID_PATTERN.match(theme_id):
        raise HTTPException(status_code=400, detail="非法的主题 id")

    builtin_path = BUILTIN_DIR / f"{theme_id}.json"
    if builtin_path.exists():
        data = _load_json(builtin_path)
        data.setdefault("type", "builtin")
        return APIResponse(success=True, message="", data=data)

    user_path = _user_path(theme_id)
    if user_path.exists():
        data = _load_json(user_path)
        if data.get("type") == "builtin":
            data["type"] = "user"
        return APIResponse(success=True, message="", data=data)

    raise HTTPException(status_code=404, detail=f"主题不存在: {theme_id}")


@router.post("/api/themes", response_model=APIResponse)
async def save_theme(payload: ThemePayload) -> APIResponse:
    """保存（创建或覆盖）用户主题。内置主题 id 不可占用。"""
    ensure_dirs()
    body = payload.model_dump()
    _validate(body)
    _fill_missing_tokens(body)

    theme_id = body["id"]
    if (BUILTIN_DIR / f"{theme_id}.json").exists():
        raise HTTPException(status_code=403, detail=f"内置主题只读，不可覆盖: {theme_id}")

    if body.get("type") not in ("user", "third-party"):
        body["type"] = "user"

    path = _user_path(theme_id)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return APIResponse(success=True, message="主题已保存", data={"id": theme_id, "path": str(path)})


@router.put("/api/themes/{theme_id}", response_model=APIResponse)
async def update_theme(theme_id: str, payload: ThemePayload) -> APIResponse:
    """更新用户主题。内置主题返回 403。"""
    ensure_dirs()
    if (BUILTIN_DIR / f"{theme_id}.json").exists():
        raise HTTPException(status_code=403, detail=f"内置主题只读: {theme_id}")

    path = _user_path(theme_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"主题不存在（如需新建请用 POST）: {theme_id}")

    body = payload.model_dump()
    body["id"] = theme_id  # 以路径为准，防止 id 与路径不一致
    _validate(body)
    _fill_missing_tokens(body)

    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return APIResponse(success=True, message="主题已更新", data={"id": theme_id})


@router.delete("/api/themes/{theme_id}", response_model=APIResponse)
async def delete_theme(theme_id: str) -> APIResponse:
    """删除用户主题。内置主题返回 403。"""
    ensure_dirs()
    if (BUILTIN_DIR / f"{theme_id}.json").exists():
        raise HTTPException(status_code=403, detail=f"内置主题不可删除: {theme_id}")

    path = _user_path(theme_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"主题不存在: {theme_id}")

    path.unlink()
    return APIResponse(success=True, message="主题已删除", data={"id": theme_id})


@router.post("/api/themes/wallpaper", response_model=APIResponse)
async def upload_wallpaper(file: UploadFile = File(...)) -> APIResponse:
    """上传壁纸图片，返回可访问的 URL。"""
    ensure_dirs()
    original = file.filename or ""
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_WALLPAPER_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的图片格式 {ext or '(空)'}，仅允许 {', '.join(sorted(ALLOWED_WALLPAPER_EXT))}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > MAX_WALLPAPER_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"图片过大 {len(content)} 字节，上限 {MAX_WALLPAPER_BYTES} 字节（5MB）",
        )

    # sanitize 文件名：只保留安全字符，防止路径穿越
    stem = Path(original).stem
    stem = unicodedata.normalize("NFKC", stem)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", stem)[:40] or "wallpaper"
    filename = f"{stem}{ext}"

    path = WALLPAPER_DIR / filename
    if path.exists():
        filename = f"{stem}_{path.stat().st_size}{ext}"
        path = WALLPAPER_DIR / filename

    path.write_bytes(content)
    return APIResponse(
        success=True,
        message="壁纸已上传",
        data={"filename": filename, "url": f"/themes/wallpapers/{filename}", "size": len(content)},
    )
