@echo off
chcp 65001 >nul
REM FinHack Pro 后端构建脚本 (Windows)

echo =========================================
echo   FinHack Pro 后端构建脚本
echo =========================================

cd /d "%~dp0.."

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 Python
    exit /b 1
)

REM 安装依赖
echo [1/3] 安装依赖...
cd python
pip install -r requirements.txt
pip install -r requirements-build.txt

REM 运行测试
echo [2/3] 运行测试...
python -m pytest tests/ -v --tb=short

REM PyInstaller 打包
echo [3/3] PyInstaller 打包...
pyinstaller pyinstaller.spec --clean

echo =========================================
echo   构建完成！
echo   输出目录: dist\finhack-backend.exe
echo =========================================
pause
