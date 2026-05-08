# FinHack Pro 桌面版构建指南

本文档详细说明如何从源代码构建 FinHack Pro 桌面版应用。

## 环境要求

### 必需环境

| 环境 | 版本要求 | 说明 |
|------|----------|------|
| **Node.js** | 18.0+ | 桌面应用运行时 |
| **Python** | 3.10+ | 后端引擎 |
| **pip** | 最新版 | Python 包管理器 |

### 可选环境（完整版）

| 环境 | 版本要求 | 说明 |
|------|----------|------|
| **Rust** | 1.75+ | 高性能核心引擎（可选） |
| **Cargo** | 随 Rust 安装 | Rust 包管理器 |

### 推荐工具

- **Git**: 版本控制
- **Visual Studio Code**: 代码编辑器
- **Windows**: Visual Studio Build Tools (用于编译原生模块)
- **macOS**: Xcode Command Line Tools

---

## Windows 构建步骤

### 1. 克隆仓库

```bash
git clone https://github.com/Docking666/finhack-pro.git
cd finhack-pro
```

### 2. 生成预置数据

```bash
# 安装 Python 依赖
pip install pandas numpy

# 生成预置的A股示例数据
python scripts/generate_preset_data.py
```

这将生成以下数据文件：
- `data/preset/600519.SH.csv` - 贵州茅台
- `data/preset/000001.SZ.csv` - 平安银行
- `data/preset/300750.SZ.csv` - 宁德时代
- `data/preset/00700.HK.csv` - 腾讯控股

### 3. 构建Python后端

```bash
cd python

# 安装运行时依赖
pip install -r requirements.txt

# 安装打包依赖
pip install -r requirements-build.txt

# 使用 PyInstaller 打包
pyinstaller pyinstaller.spec --clean
```

打包完成后，Python后端将输出到 `python/dist/` 目录。

### 4. 构建桌面应用

```bash
cd ../desktop

# 安装 Node.js 依赖
npm install

# 构建 Windows 安装包
npm run build:win
```

### 5. 输出文件

构建完成后，安装包位于：

```
desktop/dist/FinHack Pro Setup 1.0.0.exe
```

文件大小约 **200MB**，包含：
- Electron 桌面应用框架
- Python 后端引擎
- 预置示例数据
- 所有依赖库

---

## macOS 构建步骤

### 1. 克隆仓库

```bash
git clone https://github.com/Docking666/finhack-pro.git
cd finhack-pro
```

### 2. 生成预置数据

```bash
pip install pandas numpy
python scripts/generate_preset_data.py
```

### 3. 构建Python后端

```bash
cd python
pip install -r requirements.txt
pip install -r requirements-build.txt
pyinstaller pyinstaller.spec --clean
```

### 4. 构建桌面应用

```bash
cd ../desktop
npm install
npm run build:mac
```

### 5. 输出文件

构建完成后，安装包位于：

```
desktop/dist/FinHack Pro-1.0.0.dmg
```

文件大小约 **250MB**。

---

## 构建产物说明

### Windows

| 文件 | 说明 | 大小 |
|------|------|------|
| `FinHack Pro Setup 1.0.0.exe` | 安装程序 | ~200MB |
| `win-unpacked/` | 免安装版本 | ~180MB |

### macOS

| 文件 | 说明 | 大小 |
|------|------|------|
| `FinHack Pro-1.0.0.dmg` | 磁盘映像 | ~250MB |
| `mac/` | 应用程序包 | ~220MB |

---

## 开发模式

如需在开发模式下运行，无需完整构建：

### 启动Python后端

```bash
cd python
python -m finhack_pro.webui.app
```

后端将在 `http://localhost:8000` 启动。

### 启动桌面应用（开发模式）

```bash
cd desktop
npm run dev
```

这将启动 Electron 开发模式，支持热重载。

---

## 常见问题

### Q1: PyInstaller 打包失败

**问题**: 提示找不到模块或导入错误

**解决方案**:
```bash
# 确保所有依赖已安装
pip install -r requirements.txt
pip install -r requirements-build.txt

# 清理缓存重新打包
pyinstaller pyinstaller.spec --clean --noconfirm
```

**问题**: 提示缺少 Visual C++ 工具

**解决方案**:
安装 Visual Studio Build Tools:
1. 下载 https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. 选择 "Desktop development with C++"
3. 安装完成后重启电脑

### Q2: Electron 签名问题

**问题**: macOS 提示应用已损坏或无法验证

**解决方案**:
```bash
# 方法1: 临时允许任何来源
sudo spctl --master-disable

# 方法2: 移除隔离属性
xattr -cr /Applications/FinHack\ Pro.app
```

**正式签名** (需要 Apple Developer 账号):
```bash
# 签名应用
codesign --deep --force --verify --verbose --sign "Developer ID Application: Your Name" dist/mac/FinHack\ Pro.app

# 公证应用
xcrun notarytool submit dist/FinHack\ Pro-1.0.0.dmg --apple-id your@email.com --password app-specific-password --team-id TEAMID
```

### Q3: 中文路径问题

**问题**: 安装到包含中文的路径后无法启动

**解决方案**:
1. 安装到纯英文路径，如 `C:\FinHack Pro`
2. 确保用户名不包含中文
3. Windows: 修改系统区域设置为 UTF-8
   - 控制面板 -> 区域 -> 管理 -> 更改系统区域设置
   - 勾选 "Beta: 使用 Unicode UTF-8 提供全球语言支持"

### Q4: npm install 失败

**问题**: 依赖安装超时或网络错误

**解决方案**:
```bash
# 使用国内镜像
npm config set registry https://registry.npmmirror.com

# 或使用 cnpm
npm install -g cnpm --registry=https://registry.npmmirror.com
cnpm install
```

### Q5: Python 后端启动失败

**问题**: 提示找不到 Python 或依赖缺失

**解决方案**:
```bash
# 检查 Python 版本
python --version  # 应为 3.10+

# 重新安装依赖
pip install -r requirements.txt --force-reinstall

# 检查环境变量
# Windows: 确保 Python 目录在 PATH 中
# macOS: 可能需要使用 python3 而非 python
```

### Q6: 杀毒软件误报

**问题**: Windows Defender 或其他杀毒软件报告病毒

**解决方案**:
1. 这是误报，因为 PyInstaller 打包的应用未签名
2. 将应用添加到杀毒软件白名单
3. 正式发布时建议购买代码签名证书

---

## 高级配置

### 自定义构建配置

编辑 `desktop/electron-builder.yml`:

```yaml
appId: com.finhack.pro
productName: FinHack Pro

# Windows 配置
win:
  target:
    - nsis
  icon: assets/icon.ico

# macOS 配置
mac:
  target:
    - dmg
  icon: assets/icon.icns
  category: public.app-category.finance

# 包含额外文件
extraResources:
  - from: ../python/dist
    to: python
    filter:
      - "**/*"
  - from: ../data/preset
    to: data/preset
    filter:
      - "**/*.csv"
```

### 减小安装包体积

1. 排除不必要的依赖
2. 使用 UPX 压缩可执行文件
3. 启用 asar 打包

```yaml
# electron-builder.yml
asar: true
compression: maximum
```

---

## 构建脚本参考

项目提供了自动化构建脚本：

### Windows: `scripts/build-backend.bat`

```batch
@echo off
cd python
pip install -r requirements.txt
pip install -r requirements-build.txt
pyinstaller pyinstaller.spec --clean
```

### macOS/Linux: `scripts/build-backend.sh`

```bash
#!/bin/bash
cd python
pip install -r requirements.txt
pip install -r requirements-build.txt
pyinstaller pyinstaller.spec --clean
```

---

## 技术支持

如遇到其他问题，请：

1. 查看 [GitHub Issues](https://github.com/Docking666/finhack-pro/issues)
2. 提交新 Issue 并附上错误日志
3. 加入社区讨论

---

**提示**: 首次构建可能需要较长时间下载依赖，请确保网络畅通。
