# FinHack Pro 桌面应用

多智能体量化交易系统桌面版 - Electron 应用壳

## 项目结构

```
desktop/
├── package.json           # Node.js 项目配置
├── electron-builder.yml   # 打包配置
├── src/
│   ├── main.js           # Electron 主进程
│   ├── preload.js        # 预加载脚本
│   └── renderer/         # 渲染进程(启动页)
│       ├── index.html
│       └── styles.css
├── assets/
│   ├── icon.ico          # Windows 图标
│   ├── icon.png          # macOS/Linux 图标
│   ├── tray.png          # 系统托盘图标
│   └── README.md         # 图标说明
├── build/                # 构建脚本
│   ├── build-windows.bat # Windows 构建脚本
│   ├── build-macos.sh    # macOS 构建脚本
│   ├── installer.nsh     # NSIS 安装脚本
│   └── entitlements.mac.plist # macOS 权限配置
└── dist/                 # 构建输出目录
```

## 功能特性

- 自动启动和管理 Python 后端进程
- 系统托盘支持，窗口关闭时最小化到托盘
- 启动加载页面，显示后端启动进度
- 端口占用检测和友好提示
- 支持 Windows 和 macOS 双平台
- 自动更新支持
- 深色主题界面

## 开发指南

### 环境要求

- Node.js 18+
- npm 9+
- Python 3.8+ (开发环境)

### 安装依赖

```bash
cd desktop
npm install
```

### 开发运行

```bash
npm start
```

### 构建应用

#### Windows

```bash
# 方式一：使用构建脚本
build\build-windows.bat

# 方式二：直接使用 npm
npm run build:win
```

#### macOS

```bash
# 方式一：使用构建脚本
chmod +x build/build-macos.sh
./build/build-macos.sh

# 方式二：直接使用 npm
npm run build:mac
```

### 构建输出

构建完成后，安装程序位于 `dist/` 目录：

- Windows: `FinHack Pro-1.0.0-x64-setup.exe`
- macOS: `FinHack Pro-1.0.0-x64.dmg`

## 配置说明

### 主进程配置 (src/main.js)

```javascript
const BACKEND_HOST = 'localhost';  // 后端主机
const BACKEND_PORT = 8000;         // 后端端口
const WINDOW_WIDTH = 1400;         // 窗口宽度
const WINDOW_HEIGHT = 900;         // 窗口高度
```

### 打包配置 (electron-builder.yml)

主要配置项：

- `appId`: 应用唯一标识
- `productName`: 产品名称
- `extraResources`: 额外资源（Python 后端）
- `win`: Windows 打包配置
- `mac`: macOS 打包配置
- `nsis`: NSIS 安装程序配置

## API 接口

预加载脚本暴露了以下 API：

```javascript
// 操作系统类型
window.electronAPI.platform  // 'windows' | 'macos' | 'linux'

// 应用版本
window.electronAPI.version   // '1.0.0'

// 在浏览器打开链接
window.electronAPI.openExternal(url)

// 获取应用路径
window.electronAPI.getAppPath()

// 获取系统信息
window.electronAPI.getSystemInfo()

// 本地存储
window.electronAPI.store.get(key)
window.electronAPI.store.set(key, value)
window.electronAPI.store.delete(key)
```

## 图标准备

请参考 `assets/README.md` 准备应用图标：

- `icon.ico`: Windows 应用图标 (256x256, 多尺寸)
- `icon.png`: macOS/Linux 应用图标 (512x512)
- `tray.png`: 系统托盘图标 (16x16 或 32x32)

## 发布流程

1. 更新 `package.json` 中的版本号
2. 运行构建脚本
3. 测试安装程序
4. 代码签名（可选）
5. 上传到发布服务器

## 常见问题

### Q: 后端启动失败？

检查 Python 环境是否正确安装，端口 8000 是否被占用。

### Q: 图标不显示？

确保图标文件存在于 `assets/` 目录，格式正确。

### Q: macOS 提示无法验证开发者？

需要在「系统偏好设置 > 安全性与隐私」中允许运行，或对应用进行代码签名。

## 许可证

MIT License
