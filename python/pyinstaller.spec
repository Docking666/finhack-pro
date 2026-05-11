# -*- mode: python ; coding: utf-8 -*-
"""
FinHack Pro PyInstaller 打包配置
"""
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 自动收集所有依赖模块的子模块
hiddenimports = []

# 收集 uvicorn 所有子模块
hiddenimports.extend(collect_submodules('uvicorn'))

# 收集 fastapi 所有子模块
hiddenimports.extend(collect_submodules('fastapi'))

# 收集 starlette 所有子模块
hiddenimports.extend(collect_submodules('starlette'))

# 收集 pydantic 所有子模块
hiddenimports.extend(collect_submodules('pydantic'))

# 收集其他关键依赖
for pkg in ['openai', 'anthropic', 'httpx', 'httpcore', 'h11', 'anyio', 'sniffio', 
            'loguru', 'rich', 'click', 'jinja2', 'multipart', 'yaml', 'pandas', 'numpy',
            'akshare', 'tushare', 'ta', 'pydantic_settings', 'typing_extensions']:
    try:
        hiddenimports.extend(collect_submodules(pkg))
    except:
        pass

# 手动添加一些可能遗漏的模块
hiddenimports.extend([
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'fastapi.middleware',
    'fastapi.middleware.cors',
    'fastapi.staticfiles',
    'fastapi.responses',
    'starlette',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.staticfiles',
    'starlette.responses',
    'pydantic',
    'pydantic_settings',
])

# 收集所有数据文件
datas = [
    ('finhack_pro/webui/static', 'finhack_pro/webui/static'),
    ('../config', 'config'),
]

a = Analysis(
    ['finhack_pro/webui/standalone.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='finhack-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 显示控制台窗口以便查看日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
