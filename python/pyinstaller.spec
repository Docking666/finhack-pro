# -*- mode: python ; coding: utf-8 -*-
"""
FinHack Pro PyInstaller 打包配置
"""
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 收集所有数据文件
datas = [
    ('finhack_pro/webui/static', 'finhack_pro/webui/static'),
    ('../config', 'config'),
]

# 收集所有隐藏导入
hiddenimports = [
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
    'pydantic',
    'pydantic_settings',
    'openai',
    'anthropic',
    'pandas',
    'numpy',
    'yaml',
    'redis',
    'sqlalchemy',
    'jinja2',
    'multipart',
    'httpx',
    'httpcore',
    'h11',
    'anyio',
    'sniffio',
    'loguru',
    'rich',
    'akshare',
    'tushare',
    'ta',
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
