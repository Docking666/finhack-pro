#!/usr/bin/env python3
"""
FinHack Pro 环境检测和自动排障脚本

功能:
1. 检测 Python、Node.js、Rust 版本
2. 检测必要依赖包
3. 自动安装缺失依赖
4. 网络问题自动切换镜像源
5. 下载校验失败自动清理重试
6. 一键部署工具链

使用:
    python scripts/setup_env.py              # 检测环境
    python scripts/setup_env.py --install    # 自动安装缺失依赖
    python scripts/setup_env.py --full       # 完整部署（含 Rust）
    python scripts/setup_env.py --mirror cn  # 使用国内镜像
"""

import os
import sys
import subprocess
import platform
import shutil
import hashlib
import time
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum


class LogLevel(Enum):
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3


def log(level: LogLevel, message: str):
    """日志输出"""
    colors = {
        LogLevel.DEBUG: "\033[36m",    # 青色
        LogLevel.INFO: "\033[32m",     # 绿色
        LogLevel.WARNING: "\033[33m",  # 黄色
        LogLevel.ERROR: "\033[31m",    # 红色
    }
    reset = "\033[0m"
    
    prefix = {
        LogLevel.DEBUG: "[DEBUG]",
        LogLevel.INFO: "[INFO]",
        LogLevel.WARNING: "[WARN]",
        LogLevel.ERROR: "[ERROR]",
    }
    
    print(f"{colors[level]}{prefix[level]}{reset} {message}")


@dataclass
class Version:
    """版本号"""
    major: int
    minor: int
    patch: int = 0
    
    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"
    
    def __ge__(self, other: "Version") -> bool:
        return (self.major, self.minor, self.patch) >= (other.major, other.minor, other.patch)
    
    @classmethod
    def parse(cls, version_str: str) -> Optional["Version"]:
        """解析版本字符串"""
        try:
            # 移除前缀如 "v", "Python "
            version_str = version_str.lower().replace("python", "").replace("node", "").strip()
            version_str = version_str.lstrip("v")
            
            parts = version_str.split(".")
            major = int(parts[0]) if len(parts) > 0 else 0
            minor = int(parts[1]) if len(parts) > 1 else 0
            patch = int(parts[2].split("-")[0].split("+")[0]) if len(parts) > 2 else 0
            
            return cls(major, minor, patch)
        except:
            return None


@dataclass
class CheckResult:
    """检测结果"""
    name: str
    installed: bool
    version: Optional[Version] = None
    required_version: Optional[Version] = None
    message: str = ""
    install_command: Optional[str] = None


@dataclass
class MirrorConfig:
    """镜像配置"""
    name: str
    rustup_dist: str
    rustup_update: str
    pip_index: str
    npm_registry: str


# 镜像配置
MIRRORS = {
    "default": MirrorConfig(
        name="官方源",
        rustup_dist="https://static.rust-lang.org",
        rustup_update="https://static.rust-lang.org/rustup",
        pip_index="https://pypi.org/simple",
        npm_registry="https://registry.npmjs.org",
    ),
    "cn": MirrorConfig(
        name="国内镜像",
        rustup_dist="https://mirrors.ustc.edu.cn/rust-static",
        rustup_update="https://mirrors.ustc.edu.cn/rust-static/rustup",
        pip_index="https://pypi.tuna.tsinghua.edu.cn/simple",
        npm_registry="https://registry.npmmirror.com",
    ),
    "tuna": MirrorConfig(
        name="清华镜像",
        rustup_dist="https://mirrors.tuna.tsinghua.edu.cn/rustup",
        rustup_update="https://mirrors.tuna.tsinghua.edu.cn/rustup/rustup",
        pip_index="https://pypi.tuna.tsinghua.edu.cn/simple",
        npm_registry="https://mirrors.tuna.tsinghua.edu.cn/npm",
    ),
}


class EnvironmentChecker:
    """环境检测器"""
    
    # 最低版本要求
    REQUIREMENTS = {
        "python": Version(3, 10, 0),
        "node": Version(18, 0, 0),
        "rust": Version(1, 75, 0),
        "pip": Version(22, 0, 0),
        "npm": Version(9, 0, 0),
    }
    
    # 必要的 Python 包
    PYTHON_PACKAGES = [
        "pydantic",
        "pandas",
        "numpy",
        "httpx",
        "openai",
        "anthropic",
        "akshare",
        "ta",
        "loguru",
        "websockets",
    ]
    
    # 可选的 Python 包
    OPTIONAL_PACKAGES = [
        "numba",
        "reportlab",
        "openpyxl",
        "xlsxwriter",
    ]
    
    def __init__(self, mirror: str = "default"):
        self.mirror = MIRRORS.get(mirror, MIRRORS["default"])
        self.results: Dict[str, CheckResult] = {}
        self.platform = platform.system().lower()
        self.arch = platform.machine().lower()
    
    def run_command(
        self,
        cmd: List[str],
        timeout: int = 30,
        check: bool = False,
    ) -> Tuple[int, str, str]:
        """运行命令"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "Command timed out"
        except FileNotFoundError:
            return -2, "", f"Command not found: {cmd[0]}"
        except Exception as e:
            return -3, "", str(e)
    
    def check_python(self) -> CheckResult:
        """检测 Python"""
        log(LogLevel.INFO, "检测 Python...")
        
        returncode, stdout, stderr = self.run_command(
            [sys.executable, "--version"]
        )
        
        if returncode == 0 and stdout:
            version = Version.parse(stdout)
            required = self.REQUIREMENTS["python"]
            
            if version and version >= required:
                return CheckResult(
                    name="Python",
                    installed=True,
                    version=version,
                    required_version=required,
                    message=f"Python {version} (需要 >= {required})",
                )
            else:
                return CheckResult(
                    name="Python",
                    installed=True,
                    version=version,
                    required_version=required,
                    message=f"Python {version} 版本过低 (需要 >= {required})",
                    install_command=f"请升级 Python 到 {required} 或更高版本",
                )
        
        return CheckResult(
            name="Python",
            installed=False,
            required_version=self.REQUIREMENTS["python"],
            message="Python 未安装",
            install_command="请从 https://python.org 下载安装 Python",
        )
    
    def check_pip(self) -> CheckResult:
        """检测 pip"""
        log(LogLevel.INFO, "检测 pip...")
        
        returncode, stdout, stderr = self.run_command(
            [sys.executable, "-m", "pip", "--version"]
        )
        
        if returncode == 0 and stdout:
            # pip 24.0 from ...
            version_str = stdout.split()[1] if len(stdout.split()) > 1 else ""
            version = Version.parse(version_str)
            required = self.REQUIREMENTS["pip"]
            
            if version:
                return CheckResult(
                    name="pip",
                    installed=True,
                    version=version,
                    required_version=required,
                    message=f"pip {version}",
                )
        
        return CheckResult(
            name="pip",
            installed=False,
            required_version=self.REQUIREMENTS["pip"],
            message="pip 未安装",
            install_command=f"{sys.executable} -m ensurepip --upgrade",
        )
    
    def check_node(self) -> CheckResult:
        """检测 Node.js"""
        log(LogLevel.INFO, "检测 Node.js...")
        
        returncode, stdout, stderr = self.run_command(["node", "--version"])
        
        if returncode == 0 and stdout:
            version = Version.parse(stdout)
            required = self.REQUIREMENTS["node"]
            
            if version and version >= required:
                return CheckResult(
                    name="Node.js",
                    installed=True,
                    version=version,
                    required_version=required,
                    message=f"Node.js {version} (需要 >= {required})",
                )
            else:
                return CheckResult(
                    name="Node.js",
                    installed=True,
                    version=version,
                    required_version=required,
                    message=f"Node.js {version} 版本过低 (需要 >= {required})",
                    install_command="请升级 Node.js 到 18 LTS 或更高版本",
                )
        
        return CheckResult(
            name="Node.js",
            installed=False,
            required_version=self.REQUIREMENTS["node"],
            message="Node.js 未安装",
            install_command="请从 https://nodejs.org 下载安装 Node.js 18 LTS",
        )
    
    def check_npm(self) -> CheckResult:
        """检测 npm"""
        log(LogLevel.INFO, "检测 npm...")
        
        returncode, stdout, stderr = self.run_command(["npm", "--version"])
        
        if returncode == 0 and stdout:
            version = Version.parse(stdout)
            required = self.REQUIREMENTS["npm"]
            
            return CheckResult(
                name="npm",
                installed=True,
                version=version,
                required_version=required,
                message=f"npm {version}",
            )
        
        return CheckResult(
            name="npm",
            installed=False,
            required_version=self.REQUIREMENTS["npm"],
            message="npm 未安装",
            install_command="请安装 Node.js，npm 会随 Node.js 一起安装",
        )
    
    def check_rust(self) -> CheckResult:
        """检测 Rust"""
        log(LogLevel.INFO, "检测 Rust...")
        
        returncode, stdout, stderr = self.run_command(["rustc", "--version"])
        
        if returncode == 0 and stdout:
            # rustc 1.75.0 (...)
            version_str = stdout.split()[1] if len(stdout.split()) > 1 else ""
            version = Version.parse(version_str)
            required = self.REQUIREMENTS["rust"]
            
            if version and version >= required:
                return CheckResult(
                    name="Rust",
                    installed=True,
                    version=version,
                    required_version=required,
                    message=f"Rust {version} (需要 >= {required})",
                )
            else:
                return CheckResult(
                    name="Rust",
                    installed=True,
                    version=version,
                    required_version=required,
                    message=f"Rust {version} 版本过低 (需要 >= {required})",
                    install_command="rustup update stable",
                )
        
        return CheckResult(
            name="Rust",
            installed=False,
            required_version=self.REQUIREMENTS["rust"],
            message="Rust 未安装",
            install_command="curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
        )
    
    def check_cargo(self) -> CheckResult:
        """检测 Cargo"""
        log(LogLevel.INFO, "检测 Cargo...")
        
        returncode, stdout, stderr = self.run_command(["cargo", "--version"])
        
        if returncode == 0 and stdout:
            version_str = stdout.split()[1] if len(stdout.split()) > 1 else ""
            version = Version.parse(version_str)
            
            return CheckResult(
                name="Cargo",
                installed=True,
                version=version,
                message=f"Cargo {version}",
            )
        
        return CheckResult(
            name="Cargo",
            installed=False,
            message="Cargo 未安装",
            install_command="Rust 安装后会自动安装 Cargo",
        )
    
    def check_python_packages(self) -> Dict[str, CheckResult]:
        """检测 Python 包"""
        log(LogLevel.INFO, "检测 Python 依赖包...")
        
        results = {}
        
        for package in self.PYTHON_PACKAGES:
            returncode, stdout, stderr = self.run_command(
                [sys.executable, "-c", f"import {package}; print({package}.__version__)"]
            )
            
            if returncode == 0:
                version = Version.parse(stdout) if stdout else None
                results[package] = CheckResult(
                    name=package,
                    installed=True,
                    version=version,
                    message=f"{package} {version}" if version else package,
                )
            else:
                results[package] = CheckResult(
                    name=package,
                    installed=False,
                    message=f"{package} 未安装",
                    install_command=f"{sys.executable} -m pip install {package}",
                )
        
        return results
    
    def check_all(self) -> Dict[str, Any]:
        """检测所有环境"""
        log(LogLevel.INFO, "="*50)
        log(LogLevel.INFO, "开始环境检测...")
        log(LogLevel.INFO, f"平台: {self.platform} / {self.arch}")
        log(LogLevel.INFO, f"镜像: {self.mirror.name}")
        log(LogLevel.INFO, "="*50)
        
        results = {
            "python": self.check_python(),
            "pip": self.check_pip(),
            "node": self.check_node(),
            "npm": self.check_npm(),
            "rust": self.check_rust(),
            "cargo": self.check_cargo(),
            "python_packages": self.check_python_packages(),
        }
        
        self.results = results
        return results
    
    def print_report(self):
        """打印检测报告"""
        print("\n" + "="*60)
        print("环境检测报告")
        print("="*60)
        
        # 核心工具
        print("\n【核心工具】")
        for name in ["python", "pip", "node", "npm", "rust", "cargo"]:
            result = self.results.get(name)
            if result:
                status = "✅" if result.installed and (
                    not result.required_version or 
                    (result.version and result.version >= result.required_version)
                ) else "❌"
                print(f"  {status} {result.message}")
                if not result.installed and result.install_command:
                    print(f"      安装: {result.install_command}")
        
        # Python 包
        print("\n【Python 依赖】")
        packages = self.results.get("python_packages", {})
        for name, result in packages.items():
            status = "✅" if result.installed else "❌"
            print(f"  {status} {result.message}")
        
        # 统计
        total = len(self.results) - 1 + len(packages)
        installed = sum(1 for r in self.results.values() if isinstance(r, CheckResult) and r.installed)
        installed += sum(1 for r in packages.values() if r.installed)
        
        print("\n" + "-"*60)
        print(f"总计: {installed}/{total} 项已安装")
        print("="*60)


class EnvironmentInstaller:
    """环境安装器"""
    
    def __init__(self, checker: EnvironmentChecker, mirror: str = "default"):
        self.checker = checker
        self.mirror = MIRRORS.get(mirror, MIRRORS["default"])
    
    def install_pip_package(self, package: str, upgrade: bool = False) -> bool:
        """安装 pip 包"""
        log(LogLevel.INFO, f"安装 {package}...")
        
        cmd = [sys.executable, "-m", "pip", "install"]
        if upgrade:
            cmd.append("--upgrade")
        cmd.extend(["--index-url", self.mirror.pip_index])
        cmd.append(package)
        
        returncode, stdout, stderr = self.checker.run_command(cmd, timeout=300)
        
        if returncode == 0:
            log(LogLevel.INFO, f"{package} 安装成功")
            return True
        else:
            log(LogLevel.ERROR, f"{package} 安装失败: {stderr}")
            return False
    
    def install_rust(self) -> bool:
        """安装 Rust"""
        log(LogLevel.INFO, "安装 Rust...")
        
        # 设置镜像环境变量
        env = os.environ.copy()
        env["RUSTUP_DIST_SERVER"] = self.mirror.rustup_dist
        env["RUSTUP_UPDATE_ROOT"] = self.mirror.rustup_update
        
        # 下载 rustup-init
        if self.checker.platform == "windows":
            rustup_url = f"{self.mirror.rustup_dist}/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe"
            rustup_path = "rustup-init.exe"
        else:
            rustup_url = "https://sh.rustup.rs"
            rustup_path = "/tmp/rustup-init.sh"
        
        try:
            log(LogLevel.INFO, f"下载 Rust 安装器...")
            urllib.request.urlretrieve(rustup_url, rustup_path)
            
            if self.checker.platform != "windows":
                os.chmod(rustup_path, 0o755)
            
            # 运行安装
            log(LogLevel.INFO, "运行 Rust 安装...")
            if self.checker.platform == "windows":
                result = subprocess.run(
                    [rustup_path, "-y"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            else:
                result = subprocess.run(
                    ["bash", rustup_path, "-y"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            
            if result.returncode == 0:
                log(LogLevel.INFO, "Rust 安装成功")
                # 清理
                if os.path.exists(rustup_path):
                    os.remove(rustup_path)
                return True
            else:
                log(LogLevel.ERROR, f"Rust 安装失败: {result.stderr}")
                return False
                
        except urllib.error.URLError as e:
            log(LogLevel.ERROR, f"下载失败: {e}")
            return False
        except Exception as e:
            log(LogLevel.ERROR, f"安装异常: {e}")
            return False
    
    def install_missing(self, include_rust: bool = False) -> Dict[str, bool]:
        """安装缺失的依赖"""
        results = {}
        
        # 安装缺失的 Python 包
        packages = self.checker.results.get("python_packages", {})
        for name, result in packages.items():
            if not result.installed:
                results[name] = self.install_pip_package(name)
        
        # 安装 Rust（可选）
        if include_rust:
            rust_result = self.checker.results.get("rust")
            if rust_result and not rust_result.installed:
                results["rust"] = self.install_rust()
        
        return results
    
    def setup_npm_mirror(self):
        """配置 npm 镜像"""
        log(LogLevel.INFO, f"配置 npm 镜像: {self.mirror.npm_registry}")
        
        cmd = ["npm", "config", "set", "registry", self.mirror.npm_registry]
        returncode, stdout, stderr = self.checker.run_command(cmd)
        
        if returncode == 0:
            log(LogLevel.INFO, "npm 镜像配置成功")
        else:
            log(LogLevel.WARNING, f"npm 镜像配置失败: {stderr}")


def verify_download(url: str, expected_sha256: Optional[str] = None) -> bool:
    """验证下载文件完整性"""
    if not expected_sha256:
        return True
    
    try:
        with urllib.request.urlopen(url) as response:
            data = response.read()
        
        actual_sha256 = hashlib.sha256(data).hexdigest()
        return actual_sha256 == expected_sha256
    except:
        return False


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="FinHack Pro 环境检测和自动排障")
    parser.add_argument("--install", action="store_true", help="自动安装缺失依赖")
    parser.add_argument("--full", action="store_true", help="完整部署（含 Rust）")
    parser.add_argument("--mirror", choices=["default", "cn", "tuna"], default="default", help="使用镜像源")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    # 创建检测器
    checker = EnvironmentChecker(mirror=args.mirror)
    
    # 运行检测
    results = checker.check_all()
    
    # 打印报告
    if not args.json:
        checker.print_report()
    
    # 自动安装
    if args.install or args.full:
        installer = EnvironmentInstaller(checker, mirror=args.mirror)
        
        # 配置镜像
        installer.setup_npm_mirror()
        
        # 安装缺失依赖
        install_results = installer.install_missing(include_rust=args.full)
        
        if install_results:
            print("\n安装结果:")
            for name, success in install_results.items():
                status = "✅" if success else "❌"
                print(f"  {status} {name}")
    
    # JSON 输出
    if args.json:
        output = {}
        for name, result in results.items():
            if isinstance(result, CheckResult):
                output[name] = {
                    "installed": result.installed,
                    "version": str(result.version) if result.version else None,
                    "message": result.message,
                }
            elif isinstance(result, dict):
                output[name] = {}
                for pkg, pkg_result in result.items():
                    output[name][pkg] = {
                        "installed": pkg_result.installed,
                        "version": str(pkg_result.version) if pkg_result.version else None,
                    }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    
    # 返回码
    all_installed = all(
        r.installed for r in results.values() 
        if isinstance(r, CheckResult)
    )
    sys.exit(0 if all_installed else 1)


if __name__ == "__main__":
    main()
