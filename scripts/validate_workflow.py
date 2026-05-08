#!/usr/bin/env python3
"""
验证 GitHub Actions 工作流配置
检查常见问题而不需要 Docker
"""
import yaml
import os
import sys
from pathlib import Path

def validate_workflow():
    workflow_path = Path(".github/workflows/release.yml")
    
    if not workflow_path.exists():
        print("❌ Workflow file not found")
        return False
    
    print("✅ Workflow file exists")
    
    # 解析 YAML
    try:
        with open(workflow_path) as f:
            workflow = yaml.safe_load(f)
        print("✅ YAML syntax is valid")
    except yaml.YAMLError as e:
        print(f"❌ YAML syntax error: {e}")
        return False
    
    # 检查必需的 jobs
    jobs = workflow.get("jobs", {})
    if "build" not in jobs:
        print("❌ Missing 'build' job")
        return False
    print("✅ 'build' job exists")
    
    # 检查 build job 的 steps
    build_job = jobs["build"]
    steps = build_job.get("steps", [])
    step_names = [s.get("name", "") for s in steps]
    
    print(f"\n📋 Found {len(steps)} steps in build job:")
    for name in step_names:
        print(f"  - {name}")
    
    # 检查关键步骤
    required_steps = [
        "Checkout repository",
        "Set up Python",
        "Install Python dependencies",
        "Set up Node.js",
        "Install Electron dependencies",
        "Generate preset data",
        "Build Python backend",
        "Copy backend artifacts",
        "Build Electron app"
    ]
    
    print("\n🔍 Checking required steps:")
    for required in required_steps:
        found = any(required in name for name in step_names)
        status = "✅" if found else "❌"
        print(f"  {status} {required}")
    
    # 检查文件引用
    print("\n📁 Checking file references:")
    files_to_check = [
        "python/requirements.txt",
        "python/requirements-build.txt",
        "python/pyinstaller.spec",
        "scripts/generate_preset_data.py",
        "desktop/package.json",
        "desktop/electron-builder.yml"
    ]
    
    all_exist = True
    for file_path in files_to_check:
        exists = Path(file_path).exists()
        status = "✅" if exists else "❌"
        print(f"  {status} {file_path}")
        if not exists:
            all_exist = False
    
    # 检查 electron-builder.yml 配置
    print("\n🔧 Checking electron-builder.yml:")
    eb_path = Path("desktop/electron-builder.yml")
    if eb_path.exists():
        try:
            with open(eb_path) as f:
                eb_config = yaml.safe_load(f)
            
            # 检查关键配置
            checks = [
                ("appId", eb_config.get("appId")),
                ("productName", eb_config.get("productName")),
                ("directories.output", eb_config.get("directories", {}).get("output")),
                ("win.target", eb_config.get("win", {}).get("target")),
                ("mac.target", eb_config.get("mac", {}).get("target"))
            ]
            
            for name, value in checks:
                status = "✅" if value else "❌"
                print(f"  {status} {name}: {value}")
            
            # 检查可能的问题配置
            nsis = eb_config.get("nsis", {})
            if "include" in nsis:
                print(f"  ⚠️  nsis.include: {nsis['include']} (may reference non-existent file)")
            if "license" in nsis:
                print(f"  ⚠️  nsis.license: {nsis['license']} (may reference non-existent file)")
                
        except yaml.YAMLError as e:
            print(f"  ❌ electron-builder.yml YAML error: {e}")
            all_exist = False
    
    # 检查工作流语法问题
    print("\n🔍 Checking workflow syntax:")
    
    # 检查 shell 配置
    for step in steps:
        if "shell" in step:
            shell = step["shell"]
            if shell == "cmd" and "2>nul" in str(step.get("run", "")):
                print(f"  ⚠️  Step '{step.get('name')}' uses '2>nul' with cmd shell")
                print(f"      This may fail in PowerShell on Windows runners")
    
    # 检查 environment variables
    env = build_job.get("env", {})
    if "PYTHONUTF8" in env:
        print(f"  ⚠️  PYTHONUTF8 environment variable may not work on macOS")
    
    print("\n" + "="*50)
    if all_exist:
        print("✅ All checks passed!")
        return True
    else:
        print("❌ Some checks failed")
        return False

if __name__ == "__main__":
    os.chdir("/workspace/finhack-pro")
    success = validate_workflow()
    sys.exit(0 if success else 1)
