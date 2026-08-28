"""
主题系统测试（阶段5）

覆盖主题文件的列表 / 读取 / 保存 / 更新 / 删除、壁纸上传，
以及 schema 校验、内置主题只读、缺失 token 自动补齐。

说明：路由函数为 async，统一用 asyncio.run 调用，避免引入 TestClient/httpx 依赖。
用户主题落盘在 data/themes/，每个用例结束都会清理，不污染仓库。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from finhack_pro.webui import theme_routes as tr
from finhack_pro.webui.theme_routes import ThemePayload


def run(coro):
    """同步执行路由协程。"""
    return asyncio.run(coro)


def make_upload(filename: str, content: bytes) -> MagicMock:
    """构造 UploadFile 替身，避免依赖 UploadFile 构造签名。"""
    up = MagicMock()
    up.filename = filename
    up.read = AsyncMock(return_value=content)
    return up


@pytest.fixture(autouse=True)
def _clean_user_theme():
    """每个用例前后清理测试用的用户主题文件。"""
    target = tr.USER_DIR / "t-test-theme.json"
    yield
    if target.exists():
        target.unlink()


# ============================================================
# 列表与读取
# ============================================================

class TestThemeListing:
    def test_list_includes_builtin_themes(self):
        resp = run(tr.list_themes())
        assert resp.success
        ids = [item["id"] for item in resp.data["items"]]
        assert "mono-dark" in ids and "mono-light" in ids

    def test_builtin_themes_are_not_editable(self):
        builtin = [i for i in run(tr.list_themes()).data["items"] if i["type"] == "builtin"]
        assert builtin, "应至少有一个内置主题"
        assert all(item["editable"] is False for item in builtin)

    def test_list_summary_contains_preview_colors(self):
        item = next(i for i in run(tr.list_themes()).data["items"] if i["id"] == "mono-dark")
        assert item["preview"]["bg"] and item["preview"]["fg"]
        assert item["preview"]["up"] and item["preview"]["down"]

    def test_get_builtin_theme_returns_full_tokens(self):
        resp = run(tr.get_theme("mono-dark"))
        assert resp.success
        assert resp.data["id"] == "mono-dark"
        assert resp.data["mode"] == "dark"
        for key in tr.REQUIRED_TOKENS:
            assert resp.data["tokens"].get(key), f"内置主题缺少必需 token: {key}"

    def test_get_missing_theme_returns_404(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.get_theme("definitely-not-exist"))
        assert exc.value.status_code == 404

    def test_get_illegal_id_returns_400(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.get_theme("../etc/passwd"))
        assert exc.value.status_code == 400


# ============================================================
# Schema 校验
# ============================================================

class TestThemeValidation:
    def test_reject_illegal_id(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="../evil")))
        assert exc.value.status_code == 400

    def test_reject_id_with_uppercase(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="MyTheme")))
        assert exc.value.status_code == 400

    def test_reject_illegal_color_value(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="t-bad-color", tokens={"fg": "not-a-color"})))
        assert exc.value.status_code == 400

    def test_accept_hex_and_rgba_colors(self):
        run(tr.save_theme(ThemePayload(
            id="t-test-theme",
            tokens={"fg": "#abc", "bg-base": "#112233", "accent-soft": "rgba(1,2,3,.5)"},
        )))
        got = run(tr.get_theme("t-test-theme"))
        assert got.data["tokens"]["fg"] == "#abc"
        assert got.data["tokens"]["bg-base"] == "#112233"

    def test_reject_illegal_mode(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="t-bad-mode", mode="purple")))
        assert exc.value.status_code == 400

    def test_reject_illegal_scheme(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="t-bad-scheme", scheme="jp")))
        assert exc.value.status_code == 400

    def test_reject_claiming_builtin_type(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="t-fake-builtin", type="builtin")))
        assert exc.value.status_code == 400

    def test_missing_required_tokens_are_filled(self):
        """缺失的必需 token 必须用内置默认补齐，避免半残主题导致界面错乱。"""
        run(tr.save_theme(ThemePayload(id="t-test-theme", tokens={"fg": "#123456"})))
        tokens = run(tr.get_theme("t-test-theme")).data["tokens"]
        assert tokens["fg"] == "#123456", "用户自定义值应保留"
        for key in tr.REQUIRED_TOKENS:
            assert tokens.get(key), f"必需 token 未补齐: {key}"

    def test_reject_oversized_custom_css(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="t-big-css", customCss="a" * (tr.MAX_CUSTOM_CSS_CHARS + 1))))
        assert exc.value.status_code == 400


# ============================================================
# 内置主题只读
# ============================================================

class TestBuiltinReadOnly:
    def test_cannot_overwrite_builtin_by_save(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.save_theme(ThemePayload(id="mono-dark", tokens={})))
        assert exc.value.status_code == 403

    def test_cannot_update_builtin(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.update_theme("mono-light", ThemePayload(id="mono-light", tokens={})))
        assert exc.value.status_code == 403

    def test_cannot_delete_builtin(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.delete_theme("mono-dark"))
        assert exc.value.status_code == 403

    def test_builtin_file_untouched_after_write_attempt(self):
        before = (tr.BUILTIN_DIR / "mono-dark.json").read_text(encoding="utf-8")
        with pytest.raises(HTTPException):
            run(tr.save_theme(ThemePayload(id="mono-dark", tokens={})))
        after = (tr.BUILTIN_DIR / "mono-dark.json").read_text(encoding="utf-8")
        assert before == after


# ============================================================
# 用户主题 CRUD
# ============================================================

class TestUserThemeCRUD:
    def test_create_read_update_delete(self):
        assert run(tr.save_theme(ThemePayload(id="t-test-theme", name="测试主题",
                                              tokens={"fg": "#101010"}))).success
        got = run(tr.get_theme("t-test-theme"))
        assert got.data["name"] == "测试主题"
        assert got.data["tokens"]["fg"] == "#101010"

        run(tr.update_theme("t-test-theme", ThemePayload(
            id="t-test-theme", name="改名后", tokens={"fg": "#202020"})))
        updated = run(tr.get_theme("t-test-theme"))
        assert updated.data["name"] == "改名后"
        assert updated.data["tokens"]["fg"] == "#202020"

        run(tr.delete_theme("t-test-theme"))
        assert not (tr.USER_DIR / "t-test-theme.json").exists()

    def test_saved_theme_appears_in_list(self):
        run(tr.save_theme(ThemePayload(id="t-test-theme", name="列表可见性")))
        items = run(tr.list_themes()).data["items"]
        match = [i for i in items if i["id"] == "t-test-theme"]
        assert match and match[0]["name"] == "列表可见性"
        assert match[0]["editable"] is True

    def test_update_missing_theme_returns_404(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.update_theme("t-test-theme", ThemePayload(id="t-test-theme")))
        assert exc.value.status_code == 404

    def test_delete_missing_theme_returns_404(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.delete_theme("t-test-theme"))
        assert exc.value.status_code == 404

    def test_update_uses_path_id_over_body_id(self):
        """PUT 以路径 id 为准，防止路径与载荷 id 不一致导致写入错位。"""
        run(tr.save_theme(ThemePayload(id="t-test-theme", name="原始")))
        run(tr.update_theme("t-test-theme", ThemePayload(id="another-id", name="按路径更新")))
        assert (tr.USER_DIR / "t-test-theme.json").exists()
        assert not (tr.USER_DIR / "another-id.json").exists()
        assert run(tr.get_theme("t-test-theme")).data["name"] == "按路径更新"

    def test_third_party_type_preserved(self):
        run(tr.save_theme(ThemePayload(id="t-test-theme", type="third-party")))
        assert run(tr.get_theme("t-test-theme")).data["type"] == "third-party"

    def test_unknown_type_falls_back_to_user(self):
        run(tr.save_theme(ThemePayload(id="t-test-theme", type="something-else")))
        assert run(tr.get_theme("t-test-theme")).data["type"] == "user"


# ============================================================
# 壁纸上传
# ============================================================

class TestWallpaperUpload:
    def test_reject_disallowed_extension(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.upload_wallpaper(make_upload("evil.svg", b"<svg/>")))
        assert exc.value.status_code == 400

    def test_reject_empty_file(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.upload_wallpaper(make_upload("empty.png", b"")))
        assert exc.value.status_code == 400

    def test_reject_oversized_file(self):
        with pytest.raises(HTTPException) as exc:
            run(tr.upload_wallpaper(make_upload("big.png", b"x" * (tr.MAX_WALLPAPER_BYTES + 1))))
        assert exc.value.status_code == 400

    def test_accept_png_and_returns_accessible_url(self):
        payload = b"\x89PNG\r\n\x1a\n" + b"0" * 128
        resp = run(tr.upload_wallpaper(make_upload("my wall.png", payload)))
        assert resp.success
        assert resp.data["url"].startswith("/themes/wallpapers/")
        assert resp.data["url"].endswith(".png")
        assert " " not in resp.data["filename"], "文件名需 sanitize（空格转下划线）"

        saved = tr.WALLPAPER_DIR / resp.data["filename"]
        assert saved.exists()
        saved.unlink()

    def test_filename_is_sanitized_against_traversal(self):
        payload = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        resp = run(tr.upload_wallpaper(make_upload("../../escape.png", payload)))
        assert resp.success
        assert ".." not in resp.data["filename"]
        assert "/" not in resp.data["filename"]

        saved = tr.WALLPAPER_DIR / resp.data["filename"]
        assert saved.exists()
        saved.unlink()
