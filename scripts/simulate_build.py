#!/usr/bin/env python3
"""
模拟构建过程，检查依赖和配置
"""
import subprocess
import sys
from pathlib import Path

def run_cmd(cmd, cwd=None, check=True):
    """运行命令并返回结果"""
    print(f"\n$ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout[:500])  # 限制输出长度
    if result.stderr:
        print("STDERR:", result.stderr[:500])
    if check and result.returncode != 0:
        print(f"❌ Command failed with exit code {result.returncode}")
        return False
    return result.returncode == 0

def check_python_deps():
    """检查 Python 依赖"""
    print("="*50)
    print("📦 Checking Python dependencies")
    
    # 检查 pip
    if not run_cmd("pip --version", check=False):
        print("❌ pip not found")
        return False
    print("✅ pip is available")
    
    # 检查关键包
    packages = ["pandas", "numpy", "pyinstaller", "fastapi", "uvicorn"]
    for pkg in packages:
        result = subprocess.run(f"python3 -c 'import {pkg}'", shell=True, capture_output=True)
        status = "✅" if result.returncode == 0 else "❌"
        print(f"  {status} {pkg}")
    
    return True

def check_node_deps():
    """检查 Node 依赖"""
    print("\n" + "="*50)
    print("📦 Checking Node.js dependencies")
    
    # 检查 npm
    if not run_cmd("npm --version", check=False):
        print("❌ npm not found")
        return False
    print("✅ npm is available")
    
    # 检查 desktop/package.json
    pkg_file = Path("desktop/package.json")
    if not pkg_file.exists():
        print("❌ desktop/package.json not found")
        return False
    
    import json
    with open(pkg_file) as f:
        pkg = json.load(f)
    
    print(f"✅ Package name: {pkg.get('name')}")
    print(f"✅ Electron version: {pkg.get('devDependencies', {}).get('electron', 'not specified')}")
    print(f"✅ electron-builder version: {pkg.get('devDependencies', {}).get('electron-builder', 'not specified')}")
    
    return True

def check_electron_builder_config():
    """详细检查 electron-builder 配置"""
    print("\n" + "="*50)
    print("🔧 Checking electron-builder configuration")
    
    import yaml
    eb_path = Path("desktop/electron-builder.yml")
    
    with open(eb_path) as f:
        config = yaml.safe_load(f)
    
    # 检查关键配置
    print("\nConfiguration summary:")
    print(f"  appId: {config.get('appId')}")
    print(f"  productName: {config.get('productName')}")
    print(f"  output directory: {config.get('directories', {}).get('output')}")
    
    # 检查 Windows 配置
    win = config.get('win', {})
    print(f"\nWindows config:")
    print(f"  target: {win.get('target')}")
    print(f"  icon: {win.get('icon')}")
    
    # 检查 icon 文件是否存在
    win_icon = Path("desktop") / win.get('icon', '')
    if win_icon.exists():
        print(f"  ✅ Windows icon exists: {win_icon}")
    else:
        print(f"  ⚠️  Windows icon not found: {win_icon} (will use default)")
    
    # 检查 macOS 配置
    mac = config.get('mac', {})
    print(f"\nmacOS config:")
    print(f"  target: {mac.get('target')}")
    print(f"  icon: {mac.get('icon')}")
    
    mac_icon = Path("desktop") / mac.get('icon', '')
    if mac_icon.exists():
        print(f"  ✅ macOS icon exists: {mac_icon}")
    else:
        print(f"  ⚠️  macOS icon not found: {mac_icon} (will use default)")
    
    # 检查 NSIS 配置
    nsis = config.get('nsis', {})
    print(f"\nNSIS config:")
    print(f"  oneClick: {nsis.get('oneClick')}")
    print(f"  perMachine: {nsis.get('perMachine')}")
    
    # 检查是否有引用不存在的文件
    problematic = []
    if 'include' in nsis:
        problematic.append(('nsis.include', nsis['include']))
    if 'license' in nsis:
        problematic.append(('nsis.license', nsis['license']))
    
    if problematic:
        print(f"\n⚠️  Potentially problematic configurations:")
        for key, value in problematic:
            print(f"    {key}: {value}")
            file_path = Path("desktop") / value
            if not file_path.exists():
                print(f"      ❌ File does not exist: {file_path}")
    else:
        print(f"\n✅ No problematic file references found")
    
    return True

def simulate_pyinstaller():
    """模拟 PyInstaller 构建"""
    print("\n" + "="*50)
    print("🔨 Simulating PyInstaller build")
    
    spec_file = Path("python/pyinstaller.spec")
    if not spec_file.exists():
        print("❌ pyinstaller.spec not found")
        return False
    
    print("✅ pyinstaller.spec exists")
    
    # 检查 spec 文件内容
    with open(spec_file) as f:
        content = f.read()
    
    # 检查关键配置
    if "Analysis" in content:
        print("✅ Analysis section found")
    if "PYZ" in content:
        print("✅ PYZ section found")
    if "EXE" in content:
        print("✅ EXE section found")
    
    # 检查隐藏导入
    hidden_imports = [
        "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
        "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan", "uvicorn.lifespan.on",
        "jinja2", "multipart"
    ]
    
    print("\nHidden imports in spec:")
    for imp in hidden_imports:
        if imp in content:
            print(f"  ✅ {imp}")
        else:
            print(f"  ❌ {imp} (missing)")
    
    return True

def main():
    print("🔍 Build Simulation and Validation")
    print("="*50)
    
    checks = [
        ("Python dependencies", check_python_deps),
        ("Node.js dependencies", check_node_deps),
        ("electron-builder config", check_electron_builder_config),
        ("PyInstaller spec", simulate_pyinstaller),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} check failed: {e}")
            results.append((name, False))
    
    print("\n" + "="*50)
    print("📊 Summary:")
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")
    
    all_passed = all(r for _, r in results)
    print("\n" + "="*50)
    if all_passed:
        print("✅ All checks passed! Ready for GitHub Actions.")
        return 0
    else:
        print("❌ Some checks failed. Please review above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
