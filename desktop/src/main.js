/**
 * FinHack Pro - Electron 主进程
 * 多智能体量化交易系统桌面版
 * 
 * 功能：
 * 1. 启动时检查并启动 Python 后端
 * 2. 创建主窗口，加载 http://localhost:8000
 * 3. 系统托盘图标支持
 * 4. 窗口关闭时最小化到托盘
 * 5. 启动加载页面
 * 6. 应用退出时清理后端进程
 */

const { app, BrowserWindow, Tray, Menu, dialog, shell, nativeImage } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const http = require('http');
const fs = require('fs');
const Store = require('electron-store');

// 初始化配置存储
const store = new Store();

// 全局变量
let mainWindow = null;
let tray = null;
let pythonProcess = null;
let isQuitting = false;
let loadWindow = null;

// 配置常量
const BACKEND_HOST = 'localhost';
const BACKEND_PORT = 8000;
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;
const WINDOW_TITLE = 'FinHack Pro - 多智能体量化交易系统';
const WINDOW_WIDTH = 1400;
const WINDOW_HEIGHT = 900;
const MIN_WINDOW_WIDTH = 800;
const MIN_WINDOW_HEIGHT = 600;

// 获取应用路径
function getAppPath() {
  if (app.isPackaged) {
    return path.dirname(app.getPath('exe'));
  }
  return app.getAppPath();
}

// 获取后端路径
function getBackendPath() {
  const appPath = getAppPath();
  if (app.isPackaged) {
    // 打包后的路径
    return path.join(appPath, 'resources', 'backend');
  }
  // 开发环境路径
  return path.join(appPath, '..', 'python');
}

// 获取 Python 可执行文件路径
function getPythonExecutable() {
  const backendPath = getBackendPath();
  const platform = process.platform;
  
  if (app.isPackaged) {
    if (platform === 'win32') {
      return path.join(backendPath, 'python', 'python.exe');
    } else {
      return path.join(backendPath, 'python', 'bin', 'python3');
    }
  }
  
  // 开发环境使用系统 Python
  return platform === 'win32' ? 'python' : 'python3';
}

// 获取图标路径
function getIconPath() {
  const appPath = getAppPath();
  const platform = process.platform;
  
  if (app.isPackaged) {
    if (platform === 'win32') {
      return path.join(appPath, 'resources', 'assets', 'icon.ico');
    } else {
      return path.join(appPath, 'resources', 'assets', 'icon.png');
    }
  }
  
  if (platform === 'win32') {
    return path.join(appPath, 'assets', 'icon.ico');
  }
  return path.join(appPath, 'assets', 'icon.png');
}

// 获取托盘图标路径
function getTrayIconPath() {
  const appPath = getAppPath();
  
  if (app.isPackaged) {
    return path.join(appPath, 'resources', 'assets', 'tray.png');
  }
  return path.join(appPath, 'assets', 'tray.png');
}

// 检查端口是否被占用
function checkPortInUse(port) {
  return new Promise((resolve) => {
    const server = http.createServer();
    server.once('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        resolve(true);
      } else {
        resolve(false);
      }
    });
    server.once('listening', () => {
      server.close();
      resolve(false);
    });
    server.listen(port);
  });
}

// 检查后端是否运行
function checkBackendRunning() {
  return new Promise((resolve) => {
    const req = http.request({
      hostname: BACKEND_HOST,
      port: BACKEND_PORT,
      path: '/',
      method: 'GET',
      timeout: 2000
    }, (res) => {
      resolve(res.statusCode === 200 || res.statusCode === 302);
    });
    
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

// 等待后端就绪
function waitForBackend(maxRetries = 60, retryInterval = 1000) {
  return new Promise((resolve, reject) => {
    let retries = 0;
    
    const check = async () => {
      retries++;
      const isRunning = await checkBackendRunning();
      
      if (isRunning) {
        resolve(true);
      } else if (retries >= maxRetries) {
        reject(new Error(`后端启动超时，已等待 ${maxRetries} 秒`));
      } else {
        // 更新加载窗口状态
        if (loadWindow && !loadWindow.isDestroyed()) {
          loadWindow.webContents.send('loading-status', {
            message: `正在启动后端服务... (${retries}/${maxRetries})`,
            progress: (retries / maxRetries) * 100
          });
        }
        setTimeout(check, retryInterval);
      }
    };
    
    check();
  });
}

// 启动 Python 后端
async function startPythonBackend() {
  const isPortInUse = await checkPortInUse(BACKEND_PORT);
  
  if (isPortInUse) {
    const isBackendRunning = await checkBackendRunning();
    if (isBackendRunning) {
      console.log('后端已在运行中');
      return true;
    }
    
    // 端口被占用但不是我们的后端
    dialog.showErrorBox(
      '端口被占用',
      `端口 ${BACKEND_PORT} 已被其他程序占用。\n请关闭占用该端口的程序后重试。`
    );
    app.quit();
    return false;
  }
  
  const pythonExe = getPythonExecutable();
  const backendPath = getBackendPath();
  
  // 检查 Python 是否存在
  if (app.isPackaged && !fs.existsSync(pythonExe)) {
    dialog.showErrorBox(
      'Python 环境缺失',
      '未找到 Python 运行时环境。\n请重新安装应用程序。'
    );
    app.quit();
    return false;
  }
  
  // 检查后端目录是否存在
  if (!fs.existsSync(backendPath)) {
    dialog.showErrorBox(
      '后端文件缺失',
      '未找到后端程序文件。\n请重新安装应用程序。'
    );
    app.quit();
    return false;
  }
  
  return new Promise((resolve, reject) => {
    console.log('正在启动 Python 后端...');
    console.log('Python 路径:', pythonExe);
    console.log('后端路径:', backendPath);
    
    // 启动后端
    const mainPyPath = path.join(backendPath, 'main.py');
    
    // 检查 main.py 是否存在，如果不存在尝试其他入口文件
    let entryFile = mainPyPath;
    if (!fs.existsSync(mainPyPath)) {
      // 尝试查找其他入口文件
      const possibleEntries = ['app.py', 'run.py', 'server.py'];
      for (const entry of possibleEntries) {
        const entryPath = path.join(backendPath, entry);
        if (fs.existsSync(entryPath)) {
          entryFile = entryPath;
          break;
        }
      }
    }
    
    pythonProcess = spawn(pythonExe, ['-u', entryFile], {
      cwd: backendPath,
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
        FLASK_ENV: 'production',
        HOST: BACKEND_HOST,
        PORT: String(BACKEND_PORT)
      },
      stdio: ['ignore', 'pipe', 'pipe']
    });
    
    // 输出后端日志
    pythonProcess.stdout.on('data', (data) => {
      console.log(`[后端] ${data.toString().trim()}`);
    });
    
    pythonProcess.stderr.on('data', (data) => {
      console.error(`[后端错误] ${data.toString().trim()}`);
    });
    
    pythonProcess.on('error', (err) => {
      console.error('启动后端失败:', err);
      dialog.showErrorBox(
        '后端启动失败',
        `无法启动后端服务：${err.message}\n请检查 Python 环境配置。`
      );
      reject(err);
    });
    
    pythonProcess.on('exit', (code, signal) => {
      console.log(`后端进程退出，代码: ${code}, 信号: ${signal}`);
      pythonProcess = null;
    });
    
    // 等待后端启动
    waitForBackend()
      .then(() => {
        console.log('后端启动成功');
        resolve(true);
      })
      .catch((err) => {
        console.error('等待后端超时:', err);
        dialog.showErrorBox(
          '后端启动超时',
          err.message
        );
        reject(err);
      });
  });
}

// 停止 Python 后端
function stopPythonBackend() {
  return new Promise((resolve) => {
    if (pythonProcess) {
      console.log('正在停止后端进程...');
      
      // Windows 使用 taskkill，其他平台使用 SIGTERM
      if (process.platform === 'win32') {
        exec(`taskkill /pid ${pythonProcess.pid} /T /F`, (error) => {
          if (error) {
            console.error('停止后端进程失败:', error);
          }
          pythonProcess = null;
          resolve();
        });
      } else {
        pythonProcess.kill('SIGTERM');
        setTimeout(() => {
          if (pythonProcess) {
            pythonProcess.kill('SIGKILL');
          }
          pythonProcess = null;
          resolve();
        }, 3000);
      }
    } else {
      resolve();
    }
  });
}

// 创建加载窗口
function createLoadWindow() {
  loadWindow = new BrowserWindow({
    width: 500,
    height: 350,
    frame: false,
    resizable: false,
    center: true,
    show: false,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });
  
  loadWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  
  loadWindow.once('ready-to-show', () => {
    loadWindow.show();
  });
  
  loadWindow.on('closed', () => {
    loadWindow = null;
  });
}

// 创建主窗口
function createMainWindow() {
  const iconPath = getIconPath();
  let icon = null;
  
  if (fs.existsSync(iconPath)) {
    icon = nativeImage.createFromPath(iconPath);
  }
  
  mainWindow = new BrowserWindow({
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    minWidth: MIN_WINDOW_WIDTH,
    minHeight: MIN_WINDOW_HEIGHT,
    title: WINDOW_TITLE,
    icon: icon,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
      webSecurity: true
    }
  });
  
  // 加载后端页面
  mainWindow.loadURL(BACKEND_URL);
  
  // 窗口就绪后显示
  mainWindow.once('ready-to-show', () => {
    // 关闭加载窗口
    if (loadWindow && !loadWindow.isDestroyed()) {
      loadWindow.close();
    }
    mainWindow.show();
    
    // 开发环境打开开发者工具
    if (!app.isPackaged) {
      mainWindow.webContents.openDevTools();
    }
  });
  
  // 窗口关闭事件 - 最小化到托盘
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      
      // Windows 显示托盘通知
      if (process.platform === 'win32' && tray) {
        tray.displayBalloon({
          iconType: 'info',
          title: 'FinHack Pro',
          content: '程序已最小化到系统托盘，点击图标可恢复窗口。'
        });
      }
    }
  });
  
  // 窗口关闭完成
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
  
  // 处理外部链接
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  
  // 页面加载错误处理
  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
    console.error('页面加载失败:', errorCode, errorDescription);
    
    if (errorCode === -3) {
      // ERR_ABORTED - 可能是重定向，忽略
      return;
    }
    
    dialog.showErrorBox(
      '页面加载失败',
      `无法加载应用页面：${errorDescription}\n请检查后端服务是否正常运行。`
    );
  });
}

// 创建系统托盘
function createTray() {
  const trayIconPath = getTrayIconPath();
  let trayIcon;
  
  if (fs.existsSync(trayIconPath)) {
    trayIcon = nativeImage.createFromPath(trayIconPath);
    // macOS 调整图标大小
    if (process.platform === 'darwin') {
      trayIcon = trayIcon.resize({ width: 16, height: 16 });
    }
  } else {
    // 使用默认图标
    const iconPath = getIconPath();
    if (fs.existsSync(iconPath)) {
      trayIcon = nativeImage.createFromPath(iconPath);
      if (process.platform === 'darwin') {
        trayIcon = trayIcon.resize({ width: 16, height: 16 });
      }
    }
  }
  
  tray = new Tray(trayIcon || nativeImage.createEmpty());
  
  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示窗口',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        }
      }
    },
    {
      label: '隐藏窗口',
      click: () => {
        if (mainWindow) {
          mainWindow.hide();
        }
      }
    },
    { type: 'separator' },
    {
      label: '打开官网',
      click: () => {
        shell.openExternal('https://finhack.pro');
      }
    },
    { type: 'separator' },
    {
      label: '退出',
      click: async () => {
        isQuitting = true;
        await stopPythonBackend();
        app.quit();
      }
    }
  ]);
  
  tray.setToolTip('FinHack Pro - 多智能体量化交易系统');
  tray.setContextMenu(contextMenu);
  
  // 点击托盘图标显示窗口
  tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) {
        mainWindow.focus();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    }
  });
  
  // 双击托盘图标
  tray.on('double-click', () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// 应用就绪
app.whenReady().then(async () => {
  console.log('FinHack Pro 正在启动...');
  
  // 创建加载窗口
  createLoadWindow();
  
  try {
    // 启动 Python 后端
    await startPythonBackend();
    
    // 创建主窗口
    createMainWindow();
    
    // 创建系统托盘
    createTray();
    
    console.log('FinHack Pro 启动完成');
  } catch (error) {
    console.error('启动失败:', error);
    if (loadWindow && !loadWindow.isDestroyed()) {
      loadWindow.close();
    }
    app.quit();
  }
  
  // macOS 激活应用
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    } else if (mainWindow) {
      mainWindow.show();
    }
  });
});

// 所有窗口关闭事件
app.on('window-all-closed', () => {
  // macOS 不退出应用
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// 应用退出前清理
app.on('before-quit', async (event) => {
  if (!isQuitting) {
    event.preventDefault();
    isQuitting = true;
    await stopPythonBackend();
    app.quit();
  }
});

// 应用退出完成
app.on('will-quit', () => {
  console.log('FinHack Pro 正在退出...');
});

// 处理未捕获的异常
process.on('uncaughtException', (error) => {
  console.error('未捕获的异常:', error);
  dialog.showErrorBox(
    '程序错误',
    `发生未知错误：${error.message}\n程序可能需要重启。`
  );
});

// 处理未处理的 Promise 拒绝
process.on('unhandledRejection', (reason, promise) => {
  console.error('未处理的 Promise 拒绝:', reason);
});
