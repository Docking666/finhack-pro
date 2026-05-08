#!/bin/bash
# FinHack Pro 后端构建脚本

set -e

echo "========================================="
echo "  FinHack Pro 后端构建脚本"
echo "========================================="

cd "$(dirname "$0")/.."

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 Python3"
    exit 1
fi

# 安装依赖
echo "[1/3] 安装依赖..."
cd python
pip install -r requirements.txt
pip install -r requirements-build.txt

# 运行测试
echo "[2/3] 运行测试..."
python -m pytest tests/ -v --tb=short

# PyInstaller 打包
echo "[3/3] PyInstaller 打包..."
pyinstaller pyinstaller.spec --clean

echo "========================================="
echo "  构建完成！"
echo "  输出目录: dist/finhack-backend"
echo "========================================="
