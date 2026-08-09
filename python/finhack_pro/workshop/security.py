"""
策略包安全扫描 - Security Scanner

安装创意工坊策略包前的静态安全检查（AST 级别）：

- 禁止危险模块导入：os.system / subprocess / socket / shutil.rmtree 等
- 禁止危险内建调用：eval / exec / compile / __import__ / open(写模式)
- 禁止访问环境变量与文件系统写操作
- 可配置白名单策略（内置策略包跳过扫描）

注意：AST 静态扫描是"减轻风险"而非"绝对防护"。
任意 Python 代码理论上可绕过静态扫描，因此高安全场景应配合子进程隔离执行。

Usage:
    scanner = PackageScanner()
    issues = scanner.scan_code("...")
    if not issues:
        # 安全，可以安装
        pass
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import List, Optional, Set

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SecurityIssue:
    """安全问题"""
    severity: str      # high / medium / low
    line: int          # 代码行号
    message: str       # 描述

    def to_dict(self) -> dict:
        return {"severity": self.severity, "line": self.line, "message": self.message}


class PackageScanner:
    """策略包静态安全检查器"""

    # 高危模块：直接拒绝
    DANGEROUS_MODULES: Set[str] = {
        "os", "subprocess", "socket", "shutil", "sys",
        "ctypes", "multiprocessing", "pickle", "marshal",
        "importlib", "pty", "signal", "fcntl", "winreg",
        "tkinter", "webbrowser", "http.server", "smtplib",
    }

    # 高危内建调用：直接拒绝
    DANGEROUS_CALLS: Set[str] = {
        "eval", "exec", "compile", "__import__", "open",
        "input", "breakpoint", "globals", "locals",
        "getattr", "setattr", "delattr", "vars",
        "memoryview", "bytearray",
    }

    # 高危属性访问（通过 getattr 可达的危险方法）
    DANGEROUS_ATTRS: Set[str] = {
        "system", "popen", "run", "call", "check_output",
        "rmtree", "remove", "unlink", "chmod", "chown",
        "mkdir", "makedirs", "walk", "listdir", "scandir",
        "connect", "sendall", "listen", "bind",
    }

    # 中危：警告（可能误伤，仅记录）
    MEDIUM_PATTERNS: List[str] = [
        "requests", "urllib", "http", "ftp", "network",
    ]

    def __init__(self, allowlist_scope: Optional[str] = None):
        """
        Args:
            allowlist_scope: 白名单作用域（如 "finhack" 表示内置包）。
                非 None 时跳过扫描（内置包信任）。
        """
        self.allowlist_scope = allowlist_scope

    def scan_code(self, code: str) -> List[SecurityIssue]:
        """扫描策略代码，返回问题列表（空 = 安全）"""
        if self.allowlist_scope:
            return []  # 内置包信任

        issues: List[SecurityIssue] = []
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            issues.append(SecurityIssue(
                severity="high", line=getattr(e, "lineno", 0),
                message=f"代码语法错误: {e}",
            ))
            return issues

        for node in ast.walk(tree):
            # import os / import os as o
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in self.DANGEROUS_MODULES:
                        issues.append(SecurityIssue(
                            severity="high", line=node.lineno,
                            message=f"禁止导入危险模块: {alias.name}",
                        ))
                    elif root in self.MEDIUM_PATTERNS:
                        issues.append(SecurityIssue(
                            severity="medium", line=node.lineno,
                            message=f"模块涉及网络/外部交互: {alias.name}",
                        ))

            # from os import system
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    if root in self.DANGEROUS_MODULES:
                        for alias in node.names:
                            if alias.name == "*" or alias.name in self.DANGEROUS_ATTRS:
                                issues.append(SecurityIssue(
                                    severity="high", line=node.lineno,
                                    message=f"禁止从危险模块导入: {node.module}.{alias.name}",
                                ))

            # 危险内建调用
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in self.DANGEROUS_CALLS:
                    issues.append(SecurityIssue(
                        severity="high", line=node.lineno,
                        message=f"禁止调用危险内建: {func.id}()",
                    ))
                # 危险方法调用：os.system() / x.rmtree()
                if isinstance(func, ast.Attribute):
                    if func.attr in self.DANGEROUS_ATTRS:
                        issues.append(SecurityIssue(
                            severity="high", line=node.lineno,
                            message=f"禁止调用危险方法: .{func.attr}()",
                        ))

        return issues

    def scan_package(self, package_dir: str, entry_file: str = "strategy.py") -> List[SecurityIssue]:
        """扫描整个策略包目录"""
        from pathlib import Path
        pkg = Path(package_dir)
        issues: List[SecurityIssue] = []
        for py_file in pkg.rglob("*.py"):
            try:
                code = py_file.read_text(encoding="utf-8")
            except Exception as e:
                issues.append(SecurityIssue(
                    severity="medium", line=0,
                    message=f"无法读取 {py_file.name}: {e}",
                ))
                continue
            file_issues = self.scan_code(code)
            for iss in file_issues:
                iss.message = f"{py_file.name}:{iss.line}: {iss.message}"
                iss.line = 0
            issues.extend(file_issues)
        return issues
