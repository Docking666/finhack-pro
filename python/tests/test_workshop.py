"""
创意工坊模块测试

覆盖:
- manifest: 解析/校验/序列化
- security: 危险模块/内建/方法检测
- packager: 打包 → 安装 → 卸载 → 注册表 全链路
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from finhack_pro.workshop import (
    InstalledPackage,
    ManifestError,
    PackageManager,
    PackageScanner,
    StrategyManifest,
)

# ============================================================================
# manifest 测试
# ============================================================================

class TestManifest:
    def test_parse_and_validate(self):
        """合法 manifest 通过校验"""
        m = StrategyManifest.from_dict({
            "id": "dual_thrust_a",
            "name": "双通道突破",
            "version": "1.2.0",
            "author": "finhack",
            "type": "strategy",
            "entry": "strategy.py",
            "entry_class": "DualThrustStrategy",
            "params_schema": {"type": "object", "properties": {}},
        })
        assert m.validate() == []
        assert m.package_id == "dual_thrust_a@1.2.0"

    def test_missing_required_fields(self):
        """缺失必填字段校验失败"""
        m = StrategyManifest.from_dict({"id": "", "name": "", "version": ""})
        errors = m.validate()
        assert len(errors) >= 3

    def test_invalid_type(self):
        """非法类型校验失败"""
        m = StrategyManifest.from_dict({
            "id": "x", "name": "x", "version": "1.0.0", "type": "hack",
        })
        assert any("type" in e for e in m.validate())

    def test_yaml_roundtrip(self):
        """YAML 序列化往返"""
        m = StrategyManifest.from_dict({
            "id": "momentum_a",
            "name": "动量突破",
            "version": "0.9.0",
            "deps": ["numpy"],
            "benchmark": {"total_return": 0.12},
        })
        m2 = StrategyManifest.from_yaml(m.to_yaml())
        assert m2.id == "momentum_a"
        assert m2.deps == ["numpy"]
        assert m2.benchmark["total_return"] == 0.12

    def test_invalid_yaml(self):
        """非法 YAML 抛 ManifestError"""
        with pytest.raises(ManifestError):
            StrategyManifest.from_yaml("not: [valid: yaml::")


# ============================================================================
# 安全扫描测试
# ============================================================================

class TestSecurityScanner:
    def test_safe_code_passes(self):
        """正常策略代码通过扫描"""
        code = """
import numpy as np

class MyStrategy:
    def on_bar(self, bar):
        close = bar.close
        ma = np.mean(close)
        return ma > 100
"""
        assert PackageScanner().scan_code(code) == []

    def test_dangerous_import_blocked(self):
        """import os 被拦截"""
        issues = PackageScanner().scan_code("import os\nprint(os.getcwd())")
        assert any(i.severity == "high" for i in issues)

    def test_dangerous_call_blocked(self):
        """eval/exec 被拦截"""
        issues = PackageScanner().scan_code("x = eval('1+1')")
        assert any("eval" in i.message for i in issues)

    def test_dangerous_method_blocked(self):
        """subprocess.run / shutil.rmtree 被拦截"""
        issues = PackageScanner().scan_code(
            "import subprocess\nsubprocess.run(['ls'])\nshutil.rmtree('/')"
        )
        assert any("subprocess" in i.message for i in issues)

    def test_allowlist_skips(self):
        """白名单作用域跳过扫描"""
        issues = PackageScanner(allowlist_scope="finhack").scan_code(
            "import os\nos.system('rm -rf /')"
        )
        assert issues == []

    def test_syntax_error_detected(self):
        """语法错误报 high"""
        issues = PackageScanner().scan_code("def broken(:")
        assert any(i.severity == "high" for i in issues)


# ============================================================================
# 打包/安装/卸载 测试
# ============================================================================

class TestPackageManager:
    @pytest.fixture()
    def workspace(self, tmp_path):
        """临时工坊目录 + 策略源码目录"""
        strategies = tmp_path / "strategies"
        strategies.mkdir()
        (strategies / "strategy.py").write_text(
            "class DualThrustStrategy:\n    def on_bar(self, bar):\n        return []\n",
            encoding="utf-8",
        )
        return tmp_path

    def _make_manifest(self):
        return StrategyManifest.from_dict({
            "id": "test_strat",
            "name": "测试策略",
            "version": "1.0.0",
            "author": "tester",
            "type": "strategy",
            "entry": "strategy.py",
            "entry_class": "DualThrustStrategy",
        })

    def test_pack_install_roundtrip(self, workspace):
        """打包 → 安装 → 注册 全链路"""
        mgr = PackageManager(
            workshop_dir=str(workspace / "workshop"),
            strategies_dir=str(workspace / "installed"),
            allowlist_scope="finhack",
        )
        pkg_path = mgr.pack(
            strategy_dir=str(workspace / "strategies"),
            manifest=self._make_manifest(),
        )
        assert pkg_path.exists()

        installed = mgr.install(str(pkg_path))
        assert installed.manifest.id == "test_strat"
        assert (workspace / "installed" / "test_strat" / "strategy.py").exists()

        # 注册表
        listed = mgr.list_installed()
        assert len(listed) == 1
        assert listed[0].manifest.package_id == "test_strat@1.0.0"

    def test_install_blocks_dangerous_package(self, workspace):
        """含危险代码的包被拒绝安装"""
        mgr = PackageManager(
            workshop_dir=str(workspace / "workshop"),
            strategies_dir=str(workspace / "installed"),
        )
        # 先放一个危险策略源码
        (workspace / "strategies" / "strategy.py").write_text(
            "import os\nos.system('rm -rf /')\n",
            encoding="utf-8",
        )
        pkg_path = mgr.pack(
            strategy_dir=str(workspace / "strategies"),
            manifest=self._make_manifest(),
        )
        with pytest.raises(ManifestError, match="高危"):
            mgr.install(str(pkg_path))

    def test_install_duplicate_requires_force(self, workspace):
        """重复安装需 force"""
        mgr = PackageManager(
            workshop_dir=str(workspace / "workshop"),
            strategies_dir=str(workspace / "installed"),
            allowlist_scope="finhack",
        )
        pkg_path = mgr.pack(
            strategy_dir=str(workspace / "strategies"),
            manifest=self._make_manifest(),
        )
        mgr.install(str(pkg_path))
        with pytest.raises(ManifestError, match="已存在"):
            mgr.install(str(pkg_path))
        # force=True 覆盖安装
        installed = mgr.install(str(pkg_path), force=True)
        assert installed.manifest.id == "test_strat"

    def test_uninstall(self, workspace):
        """卸载并清理注册表"""
        mgr = PackageManager(
            workshop_dir=str(workspace / "workshop"),
            strategies_dir=str(workspace / "installed"),
            allowlist_scope="finhack",
        )
        pkg_path = mgr.pack(
            strategy_dir=str(workspace / "strategies"),
            manifest=self._make_manifest(),
        )
        mgr.install(str(pkg_path))
        assert mgr.uninstall("test_strat") is True
        assert mgr.list_installed() == []

    def test_zip_path_traversal_blocked(self, workspace):
        """zip 路径穿越攻击被拦截"""
        mgr = PackageManager(
            workshop_dir=str(workspace / "workshop"),
            strategies_dir=str(workspace / "installed"),
            allowlist_scope="finhack",
        )
        # 构造恶意 zip：../evil.py
        import zipfile
        evil_zip = workspace / "evil.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("../evil.py", "print('evil')")
        with pytest.raises(ManifestError, match="非法路径"):
            mgr.install(str(evil_zip))


# ============================================================================
# 分享策略工坊生成代码（generate → share 闭环）
# ============================================================================

class TestShareGenerated:
    """策略工坊生成 → 分享 → 安装 闭环"""

    SAFE_CODE = '''
from finhack_pro.strategies.base import BaseStrategy, Signal, SignalDirection

class DemoStrategy(BaseStrategy):
    def on_bar(self, context, bar):
        if bar.close > 100:
            return [Signal(symbol=bar.symbol, direction=SignalDirection.BUY, price=bar.close)]
        return []
'''

    def test_share_generated_packs_zip(self, tmp_path):
        """分享生成代码 → 产出 zip 包（含 manifest + strategy.py）"""
        import zipfile

        mgr = PackageManager(
            workshop_dir=str(tmp_path / "workshop"),
            strategies_dir=str(tmp_path / "strategies"),
            allowlist_scope="finhack",
        )
        manifest = StrategyManifest.from_dict({
            "id": "gen_test01", "name": "示例策略", "version": "0.1.0",
            "author": "workshop", "type": "strategy", "entry": "strategy.py",
        })

        # 构造：把代码写入临时目录再打包（模拟分享接口的临时目录流程）
        from pathlib import Path
        src = tmp_path / "gen_src"
        src.mkdir()
        (src / "strategy.py").write_text(self.SAFE_CODE, encoding="utf-8")
        (src / "manifest.yaml").write_text(manifest.to_yaml(), encoding="utf-8")
        pkg = mgr.pack(strategy_dir=str(src), manifest=manifest)

        with zipfile.ZipFile(pkg) as zf:
            names = zf.namelist()
            assert "manifest.yaml" in names
            assert "strategy.py" in names
            assert "DemoStrategy" in zf.read("strategy.py").decode()

    def test_share_install_roundtrip(self, tmp_path):
        """分享的包可安装并出现在列表"""
        from pathlib import Path
        src = tmp_path / "gen_src"
        src.mkdir()
        (src / "strategy.py").write_text(self.SAFE_CODE, encoding="utf-8")

        mgr = PackageManager(
            workshop_dir=str(tmp_path / "workshop"),
            strategies_dir=str(tmp_path / "strategies"),
            allowlist_scope="finhack",
        )
        manifest = StrategyManifest.from_dict({
            "id": "gen_roundtrip", "name": "闭环策略", "version": "0.1.0",
            "author": "workshop", "type": "strategy", "entry": "strategy.py",
        })
        pkg = mgr.pack(strategy_dir=str(src), manifest=manifest)
        installed = mgr.install(str(pkg))
        assert installed.manifest.id == "gen_roundtrip"

        listed = mgr.list_installed()
        assert any(p.manifest.id == "gen_roundtrip" for p in listed)


# ============================================================================
# 云端客户端（WorkshopCloud）测试 - mock 网络
# ============================================================================

class TestWorkshopCloud:
    """云端客户端测试（mock HTTP，不依赖真实 CloudBase）"""

    def test_list_packages_builds_request(self, monkeypatch):
        """list_packages 构造正确请求并解析响应"""
        from finhack_pro.workshop.cloud import WorkshopCloud

        captured = {}

        def fake_request(method, path, body=None):
            captured["method"] = method
            captured["path"] = path
            return {"items": [{"package_id": "dual_thrust"}], "page": 1, "pageSize": 20, "total": 1}

        cloud = WorkshopCloud(base_url="https://example.test/api")
        monkeypatch.setattr(cloud, "_request", fake_request)

        result = cloud.list_packages(keyword="突破", page=2)
        assert result["total"] == 1
        assert captured["method"] == "GET"
        assert "page=2" in captured["path"]
        assert "q=" in captured["path"]  # 关键词已 URL 编码

    def test_download_and_install_roundtrip(self, monkeypatch, tmp_path):
        """下载 → 安装 闭环（mock 下载）"""
        from finhack_pro.workshop.cloud import WorkshopCloud

        cloud = WorkshopCloud(
            base_url="https://example.test/api",
            workshop_dir=str(tmp_path / "workshop"),
            strategies_dir=str(tmp_path / "strategies"),
        )

        # 构造一个可安装的 zip 用于"下载"
        src = tmp_path / "pkg_src"
        src.mkdir()
        (src / "strategy.py").write_text(
            "class CloudStrat:/n    def on_bar(self, bar):\n        return []\n",
            encoding="utf-8",
        )
        manifest = StrategyManifest.from_dict({
            "id": "cloud_strat", "name": "云端策略", "version": "1.0.0",
            "author": "cloud", "type": "strategy", "entry": "strategy.py",
        })
        zip_path = cloud._local.pack(strategy_dir=str(src), manifest=manifest)

        def fake_download(package_id, save_dir=None):
            return zip_path  # 返回本地预构造的 zip

        monkeypatch.setattr(cloud, "download", fake_download)
        installed = cloud.download_and_install("cloud_strat")
        assert installed["manifest"]["id"] == "cloud_strat"

    def test_upload_builds_body(self, monkeypatch, tmp_path):
        """upload_package 构造正确请求体"""
        from finhack_pro.workshop.cloud import WorkshopCloud

        cloud = WorkshopCloud(base_url="https://example.test/api")
        captured = {}

        # 造一个最小 zip
        import zipfile
        zip_path = tmp_path / "my_strat-v2.0.0.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("strategy.py", "class X: pass")

        def fake_request(method, path, body=None):
            captured["body"] = body
            return {"package_id": body["package_id"], "version": body["version"]}

        monkeypatch.setattr(cloud, "_request", fake_request)
        result = cloud.upload_package(str(zip_path))
        assert result["package_id"] == "my_strat"      # 文件名推导
        assert result["version"] == "2.0.0"            # 文件名推导
        assert captured["body"]["zip_base64"]           # 内容已 base64

    def test_network_error_wrapped(self, monkeypatch):
        """网络异常包装为 WorkshopCloudError"""
        import urllib.error

        from finhack_pro.workshop.cloud import WorkshopCloud, WorkshopCloudError

        cloud = WorkshopCloud(base_url="https://example.test/api")

        def boom(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        # patch 底层 urlopen（保留 _request 的异常包装逻辑）
        monkeypatch.setattr(urllib.request, "urlopen", boom)
        try:
            cloud.list_packages()
            assert False, "应抛出 WorkshopCloudError"
        except WorkshopCloudError as e:
            assert "无法连接" in str(e)
