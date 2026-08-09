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

const { app, BrowserWindow, Tray, Menu, dialog, shell, nativeImage, ipcMain } = require('electron');
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
let rustProcess = null;
let isQuitting = false;
let loadWindow = null;

// 配置常量
const BACKEND_HOST = 'localhost';
const BACKEND_PORT = 8000;
const RUST_BRIDGE_HOST = 'localhost';
const RUST_BRIDGE_PORT = 8080;
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

// ============================================================================
// Python 解释器搜索与检测
// ============================================================================

/**
 * 在常见路径中搜索 Python 解释器
 * @returns {string|null} 找到的 Python 路径，未找到返回 null
 */
function searchPythonOnSystem() {
  const platform = process.platform;
  const candidates = [];

  if (platform === 'win32') {
    // Windows: 搜索 PATH、常见安装目录、py launcher
    // 1. 尝试 py launcher (Python 3.3+ 自带)
    candidates.push('py', 'python', 'python3', 'python3.13', 'python3.12', 'python3.11', 'python3.10');
    // 2. 常见安装路径
    const localAppData = process.env.LOCALAPPDATA || '';
    const programFiles = process.env.ProgramFiles || '';
    const programFilesX86 = process.env.ProgramFiles(x86) || process.env['ProgramFiles(x86)'] || '';
    const userHome = process.env.USERPROFILE || process.env.HOME || '';
    const winCandidates = [
      path.join(localAppData, 'Programs', 'Python', 'Python313', 'python.exe'),
      path.join(localAppData, 'Programs', 'Python', 'Python312', 'python.exe'),
      path.join(localAppData, 'Programs', 'Python', 'Python311', 'python.exe'),
      path.join(localAppData, 'Programs', 'Python', 'Python310', 'python.exe'),
      path.join(programFiles, 'Python313', 'python.exe'),
      path.join(programFiles, 'Python312', 'python.exe'),
      path.join(programFiles, 'Python311', 'python.exe'),
      path.join(programFilesX86, 'Python313', 'python.exe'),
      path.join(programFilesX86, 'Python312', 'python.exe'),
      path.join(userHome, 'AppData', 'Local', 'Programs', 'Python', 'Python313', 'python.exe'),
      path.join(userHome, 'AppData', 'Local', 'Programs', 'Python', 'Python312', 'python.exe'),
      // Anaconda / Miniconda
      path.join(userHome, 'anaconda3', 'python.exe'),
      path.join(userHome, 'miniconda3', 'python.exe'),
      path.join(localAppData, 'anaconda3', 'python.exe'),
      path.join(localAppData, 'miniconda3', 'python.exe'),
      path.join(programFiles, 'Anaconda3', 'python.exe'),
      path.join(programFiles, 'Miniconda3', 'python.exe'),
    ];
    candidates.push(...winCandidates);
  } else {
    // macOS / Linux
    candidates.push('python3', 'python3.13', 'python3.12', 'python3.11', 'python3.10', 'python');
    const userHome = process.env.HOME || '';
    const macCandidates = [
      path.join(userHome, '.local', 'bin', 'python3'),
      path.join('/usr/local/bin', 'python3'),
      path.join('/opt/homebrew/bin', 'python3'),
      // Anaconda / Miniconda
      path.join(userHome, 'anaconda3', 'bin', 'python3'),
      path.join(userHome, 'miniconda3', 'bin', 'python3'),
      path.join(userHome, 'opt', 'anaconda3', 'bin', 'python3'),
      path.join(userHome, 'opt', 'miniconda3', 'bin', 'python3'),
    ];
    candidates.push(...macCandidates);
  }

  for (const candidate of candidates) {
    try {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch (e) { /* ignore */ }
  }

  // 对于 PATH 中的命令（不带路径分隔符的），用 which/where 检测
  const pathCommands = candidates.filter(c => !path.isAbsolute(c));
  for (const cmd of pathCommands) {
    try {
      const whichCmd = platform === 'win32' ? 'where' : 'which';
      const result = require('child_process').execSync(`${whichCmd} ${cmd} 2>nul`, {
        encoding: 'utf8',
        timeout: 3000
      }).trim();
      if (result) {
        const firstLine = result.split('\n')[0].trim();
        if (firstLine) return firstLine;
      }
    } catch (e) { /* not found, ignore */ }
  }

  return null;
}

/**
 * 验证 Python 解释器是否可用且版本 >= 3.10
 * @param {string} pythonPath - Python 可执行文件路径
 * @returns {Promise<{valid: boolean, version: string, error: string|null}>}
 */
function validatePython(pythonPath) {
  return new Promise((resolve) => {
    exec(`"${pythonPath}" --version`, { encoding: 'utf8', timeout: 5000 }, (err, stdout, stderr) => {
      const output = (stdout || stderr || '').trim();
      const match = output.match(/Python (\d+)\.(\d+)/);
      if (!match) {
        resolve({ valid: false, version: '', error: `无法获取 Python 版本: ${output || '无输出'}` });
        return;
      }
      const major = parseInt(match[1]);
      const minor = parseInt(match[2]);
      if (major < 3 || (major === 3 && minor < 10)) {
        resolve({ valid: false, version: output, error: `Python 版本过低 (${output})，需要 >= 3.10` });
        return;
      }
      resolve({ valid: true, version: output, error: null });
    });
  });
}

// 获取 Python 可执行文件路径
function getPythonExecutable() {
  // 1. 优先使用用户自定义路径（开发环境）
  const customPath = store.get('pythonPath');
  if (customPath && fs.existsSync(customPath)) {
    return customPath;
  }

  // 2. 打包环境使用 PyInstaller 打包的后端可执行文件
  if (app.isPackaged) {
    const backendPath = getBackendPath();
    const platform = process.platform;
    const ext = platform === 'win32' ? '.exe' : '';
    // PyInstaller 打包的单文件可执行文件
    return path.join(backendPath, `finhack-backend${ext}`);
  }

  // 3. 开发环境：在系统中搜索
  return 'python'; // fallback，实际搜索在 startPythonBackend 中进行
}

// ============================================================================
// Python 自动下载与安装
// ============================================================================

const PYTHON_VERSION = '3.12.8';
const PYTHON_DOWNLOAD_MIRRORS = {
  win32: [
    `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`,
    `https://mirrors.huaweicloud.com/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`,
  ],
  darwin: [
    // macOS 使用 miniconda（更小，免安装）
    'https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh',
    'https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-MacOSX-x86_64.sh',
  ],
  linux: [
    'https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh',
    'https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh',
  ]
};

/**
 * 获取嵌入式 Python 安装目录
 * @returns {string}
 */
function getEmbeddedPythonDir() {
  const userData = app.getPath('userData');
  return path.join(userData, 'python');
}

/**
 * 获取嵌入式 Python 可执行文件路径
 * @returns {string}
 */
function getEmbeddedPythonExe() {
  const embeddedDir = getEmbeddedPythonDir();
  if (process.platform === 'win32') {
    return path.join(embeddedDir, 'python.exe');
  } else {
    return path.join(embeddedDir, 'bin', 'python3');
  }
}

/**
 * 检查嵌入式 Python 是否已安装
 * @returns {boolean}
 */
function isEmbeddedPythonInstalled() {
  const exePath = getEmbeddedPythonExe();
  return fs.existsSync(exePath);
}

/**
 * 下载文件（带进度回调）
 * @param {string} url - 下载 URL
 * @param {string} destPath - 目标路径
 * @param {Function} onProgress - 进度回调 (percent, downloaded, total)
 * @returns {Promise<void>}
 */
function downloadFile(url, destPath, onProgress) {
  return new Promise((resolve, reject) => {
    const https = require('https');
    const http = require('http');
    const client = url.startsWith('https') ? https : http;

    const file = fs.createWriteStream(destPath);
    let downloaded = 0;
    let total = 0;

    const request = client.get(url, (response) => {
      // 处理重定向
      if (response.statusCode === 301 || response.statusCode === 302) {
        file.close();
        fs.unlinkSync(destPath);
        return downloadFile(response.headers.location, destPath, onProgress).then(resolve).catch(reject);
      }

      if (response.statusCode !== 200) {
        file.close();
        fs.unlinkSync(destPath);
        return reject(new Error(`下载失败: HTTP ${response.statusCode}`));
      }

      total = parseInt(response.headers['content-length'], 10) || 0;
      downloaded = 0;

      response.on('data', (chunk) => {
        downloaded += chunk.length;
        if (onProgress && total > 0) {
          onProgress(downloaded / total * 100, downloaded, total);
        }
      });

      response.pipe(file);

      file.on('finish', () => {
        file.close();
        resolve();
      });
    });

    request.on('error', (err) => {
      file.close();
      try { fs.unlinkSync(destPath); } catch (e) { /* ignore */ }
      reject(err);
    });

    request.setTimeout(60000, () => {
      request.destroy();
      reject(new Error('下载超时'));
    });
  });
}

/**
 * 解压 ZIP 文件（Windows embeddable package）
 * @param {string} zipPath - ZIP 文件路径
 * @param {string} destDir - 目标目录
 * @returns {Promise<void>}
 */
function extractZip(zipPath, destDir) {
  return new Promise((resolve, reject) => {
    // 使用 PowerShell 解压（Windows 自带）
    if (process.platform === 'win32') {
      exec(`powershell -Command "Expand-Archive -Path '${zipPath}' -DestinationPath '${destDir}' -Force"`, (err) => {
        if (err) reject(err);
        else resolve();
      });
    } else {
      // macOS/Linux 使用 unzip
      exec(`unzip -o "${zipPath}" -d "${destDir}"`, (err) => {
        if (err) reject(err);
        else resolve();
      });
    }
  });
}

/**
 * 为嵌入式 Python 安装 pip
 * @param {string} pythonDir - Python 目录
 * @returns {Promise<void>}
 */
async function installPipForEmbeddedPython(pythonDir) {
  const pythonExe = path.join(pythonDir, 'python.exe');
  
  // 1. 下载 get-pip.py
  const getPipUrl = 'https://bootstrap.pypa.io/get-pip.py';
  const getPipPath = path.join(pythonDir, 'get-pip.py');
  
  await downloadFile(getPipUrl, getPipPath, () => {});
  
  // 2. 运行 get-pip.py
  return new Promise((resolve, reject) => {
    exec(`"${pythonExe}" "${getPipPath}" --no-warn-script-location`, {
      cwd: pythonDir,
      timeout: 120000
    }, (err, stdout, stderr) => {
      if (err) {
        console.error('安装 pip 失败:', stderr);
        reject(err);
      } else {
        console.log('pip 安装成功');
        // 清理
        try { fs.unlinkSync(getPipPath); } catch (e) { /* ignore */ }
        resolve();
      }
    });
  });
}

/**
 * 修改 python312._pth 文件以启用 site-packages
 * @param {string} pythonDir - Python 目录
 */
function enableSitePackages(pythonDir) {
  const pthFiles = fs.readdirSync(pythonDir).filter(f => f.endsWith('._pth'));
  for (const pthFile of pthFiles) {
    const pthPath = path.join(pythonDir, pthFile);
    let content = fs.readFileSync(pthPath, 'utf8');
    // 取消 import site 的注释
    content = content.replace(/^#import site$/m, 'import site');
    fs.writeFileSync(pthPath, content);
    console.log(`已启用 site-packages: ${pthFile}`);
  }
}

/**
 * 下载并安装嵌入式 Python
 * @param {Function} onProgress - 进度回调 (stage, percent, message)
 * @returns {Promise<string>} 安装后的 Python 可执行文件路径
 */
async function downloadAndInstallPython(onProgress) {
  const platform = process.platform;
  const embeddedDir = getEmbeddedPythonDir();
  const urls = PYTHON_DOWNLOAD_MIRRORS[platform];

  if (!urls || urls.length === 0) {
    throw new Error(`不支持的平台: ${platform}`);
  }

  // 确保目录存在
  if (!fs.existsSync(embeddedDir)) {
    fs.mkdirSync(embeddedDir, { recursive: true });
  }

  if (platform === 'win32') {
    // Windows: 下载 embeddable package
    const zipPath = path.join(embeddedDir, 'python-embed.zip');
    
    onProgress && onProgress('download', 0, '正在下载 Python...');
    
    let lastError = null;
    for (const url of urls) {
      try {
        await downloadFile(url, zipPath, (percent) => {
          onProgress && onProgress('download', percent, `正在下载 Python... ${percent.toFixed(1)}%`);
        });
        lastError = null;
        break;
      } catch (err) {
        console.warn(`镜像下载失败: ${url}`, err.message);
        lastError = err;
      }
    }

    if (lastError) {
      throw new Error(`所有镜像下载失败: ${lastError.message}`);
    }

    onProgress && onProgress('extract', 0, '正在解压 Python...');
    
    // 解压
    await extractZip(zipPath, embeddedDir);
    
    // 清理 zip
    try { fs.unlinkSync(zipPath); } catch (e) { /* ignore */ }

    onProgress && onProgress('pip', 0, '正在安装 pip...');
    
    // 启用 site-packages
    enableSitePackages(embeddedDir);
    
    // 安装 pip
    await installPipForEmbeddedPython(embeddedDir);

    onProgress && onProgress('done', 100, 'Python 安装完成');

    return getEmbeddedPythonExe();
  } else {
    // macOS/Linux: 使用 Miniconda（静默安装）
    const shPath = path.join(embeddedDir, 'miniconda.sh');
    
    onProgress && onProgress('download', 0, '正在下载 Miniconda...');
    
    let lastError = null;
    for (const url of urls) {
      try {
        await downloadFile(url, shPath, (percent) => {
          onProgress && onProgress('download', percent, `正在下载 Miniconda... ${percent.toFixed(1)}%`);
        });
        lastError = null;
        break;
      } catch (err) {
        console.warn(`镜像下载失败: ${url}`, err.message);
        lastError = err;
      }
    }

    if (lastError) {
      throw new Error(`所有镜像下载失败: ${lastError.message}`);
    }

    onProgress && onProgress('install', 0, '正在安装 Miniconda...');
    
    // 静默安装
    const installDir = path.join(embeddedDir, 'miniconda3');
    return new Promise((resolve, reject) => {
      exec(`bash "${shPath}" -b -p "${installDir}"`, {
        timeout: 300000
      }, (err, stdout, stderr) => {
        if (err) {
          console.error('Miniconda 安装失败:', stderr);
          reject(err);
        } else {
          // 清理
          try { fs.unlinkSync(shPath); } catch (e) { /* ignore */ }
          onProgress && onProgress('done', 100, 'Python 安装完成');
          resolve(path.join(installDir, 'bin', 'python3'));
        }
      });
    });
  }
}

/**
 * 安装 Python 依赖
 * @param {string} pythonExe - Python 可执行文件路径
 * @param {string} requirementsPath - requirements.txt 路径
 * @param {Function} onProgress - 进度回调
 * @returns {Promise<void>}
 */
async function installPythonDependencies(pythonExe, requirementsPath, onProgress) {
  return new Promise((resolve, reject) => {
    onProgress && onProgress('deps', 0, '正在安装依赖...');
    
    const cmd = `"${pythonExe}" -m pip install -r "${requirementsPath}" --no-warn-script-location`;
    exec(cmd, { timeout: 600000 }, (err, stdout, stderr) => {
      if (err) {
        console.error('依赖安装失败:', stderr);
        reject(err);
      } else {
        onProgress && onProgress('deps', 100, '依赖安装完成');
        resolve();
      }
    });
  });
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

// ============================================================================
// 环境检测与自动排障
// ============================================================================

/**
 * 运行 Python 环境检测脚本
 * @param {string} action - 'check' | 'install' | 'full'
 * @param {string} mirror - 'default' | 'cn' | 'tuna'
 * @returns {Promise<{success: boolean, data?: object, error?: string}>}
 */
function runEnvCheck(action = 'check', mirror = 'default') {
  return new Promise((resolve) => {
    const appPath = getAppPath();
    let scriptPath;

    // 打包环境（PyInstaller onefile）下跳过环境检测：
    // getPythonExecutable() 返回的是 finhack-backend.exe（服务程序），
    // 用它执行 setup_env.py 会忽略参数直接启动后端服务，进程永不退出，
    // 导致启动流程卡 120 秒。打包环境依赖已全部内置，无需检测。
    if (app.isPackaged) {
      console.log('打包环境：跳过 setup_env.py 环境检测（依赖已内置）');
      resolve({ success: true, data: null });
      return;
    }

    if (app.isPackaged) {
      // 打包后: setup_env.py 在 resources/scripts/ 下
      scriptPath = path.join(appPath, 'resources', 'scripts', 'setup_env.py');
    } else {
      // 开发环境
      scriptPath = path.join(appPath, 'scripts', 'setup_env.py');
    }

    if (!fs.existsSync(scriptPath)) {
      console.warn('环境检测脚本不存在:', scriptPath);
      resolve({ success: true, data: null }); // 脚本不存在则跳过检测
      return;
    }

    const pythonExe = getPythonExecutable();
    const args = [scriptPath, '--json'];

    if (action === 'install') {
      args.push('--install');
    } else if (action === 'full') {
      args.push('--full');
    }

    if (mirror !== 'default') {
      args.push('--mirror', mirror);
    }

    console.log(`运行环境检测: python ${args.join(' ')}`);

    const proc = spawn(pythonExe, args, {
      cwd: app.isPackaged ? path.dirname(scriptPath) : appPath,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
      timeout: 120000, // 2 分钟超时
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });

    proc.on('error', (err) => {
      console.warn('环境检测执行失败:', err.message);
      resolve({ success: true, data: null }); // 检测失败不阻塞启动
    });

    proc.on('close', (code) => {
      if (code === 0) {
        try {
          const data = JSON.parse(stdout);
          resolve({ success: true, data });
        } catch (e) {
          console.warn('环境检测结果解析失败:', e.message);
          resolve({ success: true, data: null });
        }
      } else {
        console.warn('环境检测返回非零退出码:', code, stderr);
        resolve({ success: true, data: null });
      }
    });
  });
}

/**
 * 分析环境检测结果，返回缺失的关键依赖
 * @param {object} data - setup_env.py 的 JSON 输出
 * @returns {{ critical: string[], optional: string[], hasRust: boolean }}
 */
function analyzeEnvResult(data) {
  if (!data) return { critical: [], optional: [], hasRust: false };

  const critical = [];
  const optional = [];
  let hasRust = false;

  // 检查核心工具
  const coreTools = ['python', 'pip'];
  for (const tool of coreTools) {
    if (data[tool] && !data[tool].installed) {
      critical.push(`${tool} (核心工具)`);
    }
  }

  // 检查 Python 依赖
  if (data.python_packages) {
    const requiredPackages = ['pydantic', 'pandas', 'numpy', 'httpx', 'loguru', 'websockets'];
    const optionalPackages = ['numba', 'reportlab', 'openpyxl'];

    for (const pkg of requiredPackages) {
      if (data.python_packages[pkg] && !data.python_packages[pkg].installed) {
        critical.push(`${pkg} (Python 依赖)`);
      }
    }

    for (const pkg of optionalPackages) {
      if (data.python_packages[pkg] && !data.python_packages[pkg].installed) {
        optional.push(pkg);
      }
    }
  }

  // 检查 Rust（可选）
  if (data.rust && data.rust.installed) {
    hasRust = true;
  }

  return { critical, optional, hasRust };
}

/**
 * 环境检测主流程
 * 在启动后端之前调用，检测环境并提示用户安装缺失依赖
 * @returns {Promise<boolean>} 是否通过检测（true=可以继续启动）
 */
async function checkEnvironment() {
  // 更新加载窗口
  if (loadWindow && !loadWindow.isDestroyed()) {
    loadWindow.webContents.send('loading-status', {
      message: '正在检测运行环境...',
      progress: 15
    });
  }

  // 1. 运行环境检测
  const { success, data } = await runEnvCheck('check');

  if (!data) {
    console.log('环境检测脚本未找到，跳过检测');
    return true;
  }

  // 2. 分析结果
  const { critical, optional, hasRust } = analyzeEnvResult(data);

  // 3. 没有缺失，直接通过
  if (critical.length === 0) {
    console.log('环境检测通过');

    if (optional.length > 0) {
      console.log(`可选依赖未安装: ${optional.join(', ')}`);
    }

    if (!hasRust) {
      console.log('Rust 未安装，高性能计算将使用 Python 回退');
    }

    return true;
  }

  // 4. 有缺失的关键依赖，弹窗提示
  const missingList = critical.map((item, i) => `  ${i + 1}. ${item}`).join('\n');
  const optionalList = optional.length > 0
    ? `\n\n可选依赖（不影响基本功能）:\n${optional.map((p, i) => `  ${i + 1}. ${p}`).join('\n')}`
    : '';

  const response = dialog.showMessageBoxSync({
    type: 'warning',
    title: '环境依赖缺失',
    message: '以下依赖未安装，可能影响系统正常运行：',
    detail: `必需依赖:\n${missingList}${optionalList}`,
    buttons: ['自动安装', '继续启动（可能不稳定）', '退出'],
    defaultId: 0,
    cancelId: 2,
    noLink: true,
  });

  // 用户选择退出
  if (response === 2) {
    app.quit();
    return false;
  }

  // 用户选择自动安装
  if (response === 0) {
    if (loadWindow && !loadWindow.isDestroyed()) {
      loadWindow.webContents.send('loading-status', {
        message: '正在安装缺失依赖...',
        progress: 20
      });
    }

    // 尝试国内镜像安装（更快）
    const installResult = await runEnvCheck('install', 'cn');

    if (installResult.data) {
      const afterAnalysis = analyzeEnvResult(installResult.data);

      if (afterAnalysis.critical.length === 0) {
        dialog.showMessageBoxSync({
          type: 'info',
          title: '安装完成',
          message: '所有必需依赖已安装成功！',
          buttons: ['确定'],
        });
        return true;
      } else {
        dialog.showMessageBoxSync({
          type: 'error',
          title: '安装失败',
          message: `以下依赖安装失败: ${afterAnalysis.critical.join(', ')}`,
          detail: '请手动运行以下命令安装:\npython scripts/setup_env.py --install --mirror cn',
          buttons: ['继续启动', '退出'],
        });
        return true; // 仍然尝试启动
      }
    }
  }

  // 用户选择继续启动
  return true;
}

// ============================================================================
// Python 后端管理
// ============================================================================

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
  
  // 打包环境：直接检查 finhack-backend 可执行文件
  if (app.isPackaged) {
    if (!fs.existsSync(pythonExe)) {
      dialog.showErrorBox(
        '后端程序缺失',
        `未找到后端可执行文件: ${pythonExe}\n请重新安装应用程序。`
      );
      app.quit();
      return false;
    }
    // 打包环境直接启动 PyInstaller 可执行文件
    return startPythonBackendWithExe(pythonExe, backendPath);
  }
  
  // 开发环境：验证 Python 可用性，不可用则搜索或下载
  if (!app.isPackaged) {
    let resolvedPython = pythonExe;
    
    // 1. 检查嵌入式 Python 是否已安装
    if (isEmbeddedPythonInstalled()) {
      const embeddedExe = getEmbeddedPythonExe();
      const embeddedValidation = await validatePython(embeddedExe);
      if (embeddedValidation.valid) {
        console.log(`使用已安装的嵌入式 Python: ${embeddedExe}`);
        resolvedPython = embeddedExe;
        store.set('pythonPath', embeddedExe);
        return startPythonBackendWithExe(resolvedPython, backendPath);
      }
    }
    
    // 2. 验证当前 Python 路径
    const validation = await validatePython(resolvedPython);
    if (!validation.valid) {
      console.warn(`默认 Python 不可用: ${validation.error}，开始搜索...`);
      
      // 自动搜索系统中的 Python
      const found = searchPythonOnSystem();
      if (found) {
        const foundValidation = await validatePython(found);
        if (foundValidation.valid) {
          console.log(`自动找到 Python: ${found} (${foundValidation.version})`);
          resolvedPython = found;
          store.set('pythonPath', found);
        }
      }
    }
    
    // 3. 如果仍然没有可用的 Python，提供下载或手动选择
    if (!resolvedPython || !(await validatePython(resolvedPython)).valid) {
      const choice = dialog.showMessageBoxSync({
        type: 'question',
        title: '未找到 Python 解释器',
        message: '未检测到 Python 环境',
        detail: 'FinHack Pro 需要 Python >= 3.10。\n\n您可以选择：\n• 自动下载：下载嵌入式 Python（约 15MB），无需管理员权限\n• 手动选择：使用已安装的 Python',
        buttons: ['自动下载', '手动选择...', '退出'],
        defaultId: 0,
        cancelId: 2,
        noLink: true
      });
      
      if (choice === 2) {
        app.quit();
        return false;
      }
      
      if (choice === 0) {
        // 自动下载 Python
        try {
          // 发送进度到加载窗口
          const sendProgress = (stage, percent, message) => {
            if (loadWindow && !loadWindow.isDestroyed()) {
              loadWindow.webContents.send('python-download-progress', {
                stage, percent, message
              });
            }
          };
          
          resolvedPython = await downloadAndInstallPython(sendProgress);
          store.set('pythonPath', resolvedPython);
          console.log(`Python 自动安装完成: ${resolvedPython}`);
        } catch (err) {
          dialog.showErrorBox(
            'Python 下载失败',
            `无法自动下载 Python：${err.message}\n\n请手动选择 Python 路径或检查网络连接。`
          );
          // 回退到手动选择
          const fallbackChoice = dialog.showMessageBoxSync({
            type: 'question',
            title: '下载失败',
            message: 'Python 自动下载失败',
            buttons: ['手动选择...', '退出'],
            defaultId: 0,
            cancelId: 1
          });
          if (fallbackChoice === 1) {
            app.quit();
            return false;
          }
          // 继续手动选择流程
          const result = dialog.showOpenDialogSync({
            title: '选择 Python 可执行文件',
            filters: [{ name: 'Python', extensions: ['exe'] }, { name: '所有文件', extensions: ['*'] }],
            properties: ['openFile']
          });
          if (!result || result.length === 0) {
            app.quit();
            return false;
          }
          resolvedPython = result[0];
          const selectedValidation = await validatePython(resolvedPython);
          if (!selectedValidation.valid) {
            dialog.showErrorBox('Python 版本不符合要求', selectedValidation.error);
            app.quit();
            return false;
          }
          store.set('pythonPath', resolvedPython);
        }
      } else {
        // 手动选择
        const result = dialog.showOpenDialogSync({
          title: '选择 Python 可执行文件',
          filters: [{ name: 'Python', extensions: ['exe'] }, { name: '所有文件', extensions: ['*'] }],
          properties: ['openFile']
        });
        
        if (!result || result.length === 0) {
          app.quit();
          return false;
        }
        
        const selectedPath = result[0];
        const selectedValidation = await validatePython(selectedPath);
        
        if (!selectedValidation.valid) {
          dialog.showErrorBox(
            'Python 版本不符合要求',
            `选择的 Python 不符合要求：${selectedValidation.error}\n\n请选择 Python >= 3.10 的可执行文件。`
          );
          app.quit();
          return false;
        }
        
        console.log(`用户选择 Python: ${selectedPath} (${selectedValidation.version})`);
        resolvedPython = selectedPath;
        store.set('pythonPath', selectedPath);
      }
    }
    
    return startPythonBackendWithExe(resolvedPython, backendPath);
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
  
  return startPythonBackendWithExe(pythonExe, backendPath);
}

// 使用指定 Python 路径启动后端
function startPythonBackendWithExe(pythonExe, backendPath) {
  return new Promise((resolve, reject) => {
    console.log('正在启动 Python 后端...');
    console.log('Python 路径:', pythonExe);
    console.log('后端路径:', backendPath);
    
    // 判断是否是 PyInstaller 打包的可执行文件
    const isPyInstallerExe = pythonExe.includes('finhack-backend') || !pythonExe.endsWith('.exe') || fs.existsSync(pythonExe.replace('.exe', ''));
    
    let spawnArgs;
    let spawnCwd;
    
    if (isPyInstallerExe && app.isPackaged) {
      // PyInstaller 打包的可执行文件：直接运行，不需要参数
      console.log('检测到 PyInstaller 可执行文件，直接启动...');
      spawnArgs = [];
      spawnCwd = path.dirname(pythonExe);
    } else {
      // 普通 Python 解释器：需要指定入口文件
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
      
      spawnArgs = ['-u', entryFile];
      spawnCwd = backendPath;
    }
    
    pythonProcess = spawn(pythonExe, spawnArgs, {
      cwd: spawnCwd,
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
        // 热修复：PyInstaller 打包漏掉 akshare 数据文件时自动补丁
        patchAkshareDataFiles();
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

/**
 * 热修复 akshare 数据文件缺失问题
 *
 * PyInstaller onefile 打包时若漏掉 akshare 的 file_fold 数据文件
 * （如 calendar.json 交易日历），回测/分析流水线会报：
 *   No such file or directory: _MEIxxxx/akshare/file_fold/calendar.json
 *
 * 本函数在后端启动后扫描当前 _MEI 临时目录，若 akshare/file_fold
 * 缺失则尝试从以下来源补充：
 *   1. 本机 pip 安装的 akshare 包（site-packages/akshare/file_fold）
 *   2. 用户数据目录中的备份（%APPDATA%/finhack-pro/akshare_data）
 *
 * @returns {boolean} 是否成功打上补丁
 */
function patchAkshareDataFiles() {
  try {
    const fs = require('fs');
    const os = require('os');
    const path = require('path');

    // 1. 定位当前 PyInstaller 解包目录（含 akshare 包结构的 _MEI 目录）
    const tempDir = os.tmpdir();
    let meiDirs = [];
    try {
      meiDirs = fs.readdirSync(tempDir).filter((name) => name.startsWith('_MEI'));
    } catch (e) {
      return false;
    }

    let patched = false;
    for (const dir of meiDirs) {
      const akshareDir = path.join(tempDir, dir, 'akshare');
      const fileFold = path.join(akshareDir, 'file_fold');
      // 无 akshare 目录或已有 file_fold 则跳过
      if (!fs.existsSync(akshareDir)) continue;
      if (fs.existsSync(fileFold)) continue;

      // 2. 查找数据文件来源
      const sources = [];
      // 2a. 本机 site-packages
      try {
        const sitePkgs = [
          path.join(os.homedir(), 'AppData', 'Roaming', 'Python'),
          'C:/Python313/Lib/site-packages',
        ];
        for (const sp of sitePkgs) {
          if (fs.existsSync(sp)) {
            const candidates = [];
            const walk = (d) => {
              try {
                const entries = fs.readdirSync(d, { withFileTypes: true });
                for (const e of entries) {
                  if (!e.isDirectory()) continue;
                  if (e.name === 'akshare') {
                    const ff = path.join(d, e.name, 'file_fold');
                    if (fs.existsSync(ff)) candidates.push(ff);
                  } else {
                    walk(path.join(d, e.name));
                  }
                }
              } catch (err) {}
            };
            walk(sp);
            sources.push(...candidates);
          }
        }
      } catch (e) {}
      // 2b. 用户数据目录备份
      const backupDir = path.join(app.getPath('userData'), 'akshare_data', 'file_fold');
      if (fs.existsSync(backupDir)) sources.push(backupDir);

      // 3. 复制补丁
      for (const src of sources) {
        try {
          fs.mkdirSync(fileFold, { recursive: true });
          const files = fs.readdirSync(src);
          for (const f of files) {
            const srcFile = path.join(src, f);
            if (fs.statSync(srcFile).isFile()) {
              fs.copyFileSync(srcFile, path.join(fileFold, f));
            }
          }
          console.log(`[热修复] akshare 数据已补丁: ${fileFold} (来源: ${src})`);
          patched = true;
          break;
        } catch (e) {
          console.warn(`[热修复] 从 ${src} 补丁失败:`, e.message);
        }
      }
    }
    return patched;
  } catch (e) {
    console.warn('[热修复] akshare 补丁异常:', e.message);
    return false;
  }
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

// ============================================================================
// Rust 加速后端管理
// ============================================================================

/**
 * 获取 Rust bridge 二进制文件路径
 * - 打包环境: resources/backend/finhack-bridge(.exe)
 * - 开发环境: target/release/finhack-bridge(.exe)
 * @returns {string|null}
 */
function getRustBridgePath() {
  const platform = process.platform;
  const ext = platform === 'win32' ? '.exe' : '';

  if (app.isPackaged) {
    // 打包环境：从 extraResources 中查找
    const appPath = app.getAppPath();
    const candidates = [
      path.join(appPath, '..', 'resources', 'backend', `finhack-bridge${ext}`),
      path.join(appPath, 'resources', 'backend', `finhack-bridge${ext}`),
      path.join(process.resourcesPath, 'backend', `finhack-bridge${ext}`),
    ];
    for (const p of candidates) {
      if (fs.existsSync(p)) return p;
    }
    return null;
  } else {
    // 开发环境：从 target/release 查找
    const projectRoot = path.join(__dirname, '..', '..');
    const candidates = [
      path.join(projectRoot, 'target', 'release', `finhack-bridge${ext}`),
      path.join(projectRoot, 'target', 'debug', `finhack-bridge${ext}`),
    ];
    for (const p of candidates) {
      if (fs.existsSync(p)) return p;
    }
    return null;
  }
}

/**
 * 检查 Rust bridge 是否可用
 * @returns {Promise<{available: boolean, path: string|null, version: string|null, error: string|null}>}
 */
function checkRustBridge() {
  return new Promise((resolve) => {
    const bridgePath = getRustBridgePath();
    if (!bridgePath) {
      resolve({ available: false, path: null, version: null, error: '未找到 Rust bridge 二进制文件' });
      return;
    }

    // 检查二进制是否可执行
    if (!fs.existsSync(bridgePath)) {
      resolve({ available: false, path: bridgePath, version: null, error: 'Rust bridge 文件不存在' });
      return;
    }

    // 尝试调用健康检查接口
    const url = `http://${RUST_BRIDGE_HOST}:${RUST_BRIDGE_PORT}/health`;
    const http = require('http');
    const req = http.get(url, { timeout: 2000 }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const info = JSON.parse(data);
          resolve({ available: true, path: bridgePath, version: info.version || 'unknown', error: null });
        } catch (e) {
          resolve({ available: true, path: bridgePath, version: 'unknown', error: null });
        }
      });
    });
    req.on('error', () => {
      // bridge 没在运行，但二进制存在，可以启动
      resolve({ available: false, path: bridgePath, version: null, error: 'Rust bridge 未运行' });
    });
    req.on('timeout', () => {
      req.destroy();
      resolve({ available: false, path: bridgePath, version: null, error: 'Rust bridge 响应超时' });
    });
  });
}

/**
 * 启动 Rust bridge 进程
 * @returns {Promise<boolean>}
 */
function startRustBridge() {
  return new Promise((resolve, reject) => {
    const bridgePath = getRustBridgePath();
    if (!bridgePath) {
      console.log('未找到 Rust bridge，使用纯 Python 模式');
      resolve(false);
      return;
    }

    // 检查用户是否禁用了 Rust 加速
    if (store.get('disableRust') === true) {
      console.log('用户已禁用 Rust 加速');
      resolve(false);
      return;
    }

    console.log('正在启动 Rust bridge...');
    console.log('Rust bridge 路径:', bridgePath);

    const env = {
      ...process.env,
      BRIDGE_HOST: RUST_BRIDGE_HOST,
      BRIDGE_PORT: String(RUST_BRIDGE_PORT),
      RUST_LOG: 'info',
    };

    rustProcess = spawn(bridgePath, [], { env, stdio: ['ignore', 'pipe', 'pipe'] });

    rustProcess.stdout.on('data', (data) => {
      console.log(`[Rust] ${data.toString().trim()}`);
    });

    rustProcess.stderr.on('data', (data) => {
      const msg = data.toString().trim();
      console.warn(`[Rust:warn] ${msg}`);
    });

    rustProcess.on('error', (err) => {
      console.warn('Rust bridge 启动失败:', err.message);
      rustProcess = null;
      resolve(false);
    });

    rustProcess.on('exit', (code) => {
      console.log(`Rust bridge 退出，代码: ${code}`);
      rustProcess = null;
    });

    // 等待 bridge 就绪
    let retries = 15;
    const checkInterval = setInterval(() => {
      const http = require('http');
      http.get(`http://${RUST_BRIDGE_HOST}:${RUST_BRIDGE_PORT}/health`, { timeout: 1000 }, (res) => {
        clearInterval(checkInterval);
        console.log('Rust bridge 启动成功');
        // 通知 Python 后端 bridge 地址
        process.env.FINHACK_BRIDGE_URL = `http://${RUST_BRIDGE_HOST}:${RUST_BRIDGE_PORT}`;
        store.set('rustEnabled', true);
        resolve(true);
      }).on('error', () => {
        retries--;
        if (retries <= 0) {
          clearInterval(checkInterval);
          console.warn('Rust bridge 启动超时，使用纯 Python 模式');
          rustProcess = null;
          resolve(false);
        }
      });
    }, 500);
  });
}

/**
 * 停止 Rust bridge
 */
function stopRustBridge() {
  return new Promise((resolve) => {
    if (rustProcess) {
      console.log('正在停止 Rust bridge...');
      if (process.platform === 'win32') {
        exec(`taskkill /pid ${rustProcess.pid} /T /F`, () => {
          rustProcess = null;
          resolve();
        });
      } else {
        rustProcess.kill('SIGTERM');
        setTimeout(() => {
          if (rustProcess) rustProcess.kill('SIGKILL');
          rustProcess = null;
          resolve();
        }, 3000);
      }
    } else {
      resolve();
    }
  });
}

/**
 * 编译 Rust bridge（开发环境）
 * @param {Function} onProgress - 进度回调
 * @returns {Promise<boolean>}
 */
function buildRustBridge(onProgress) {
  return new Promise((resolve) => {
    const projectRoot = path.join(__dirname, '..', '..');
    onProgress && onProgress('build', 0, '正在编译 Rust 核心（首次可能需要 5-10 分钟）...');

    const cmd = process.platform === 'win32' ? 'cargo build --release' : 'cargo build --release';
    exec(cmd, {
      cwd: projectRoot,
      timeout: 600000, // 10 分钟超时
    }, (err, stdout, stderr) => {
      if (err) {
        console.error('Rust 编译失败:', stderr);
        onProgress && onProgress('error', 0, '编译失败');
        resolve(false);
      } else {
        onProgress && onProgress('done', 100, 'Rust 核心编译完成');
        resolve(true);
      }
    });
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
        await stopRustBridge();
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

// ============================================================================
// IPC 处理程序（配置存储 + Python 路径管理）
// ============================================================================

// 配置存储 IPC
ipcMain.handle('store:get', (event, key) => {
  return store.get(key);
});

ipcMain.handle('store:set', (event, key, value) => {
  store.set(key, value);
  return true;
});

ipcMain.handle('store:delete', (event, key) => {
  store.delete(key);
  return true;
});

ipcMain.handle('store:clear', () => {
  store.clear();
  return true;
});

// Python 路径管理 IPC
ipcMain.handle('python:getPath', () => {
  return store.get('pythonPath') || '';
});

ipcMain.handle('python:setPath', (event, pythonPath) => {
  if (pythonPath && fs.existsSync(pythonPath)) {
    store.set('pythonPath', pythonPath);
    return { success: true, path: pythonPath };
  }
  return { success: false, error: '文件不存在' };
});

ipcMain.handle('python:detect', async () => {
  const found = searchPythonOnSystem();
  if (found) {
    const validation = await validatePython(found);
    return { found: true, path: found, ...validation };
  }
  return { found: false };
});

ipcMain.handle('python:validate', async (event, pythonPath) => {
  if (!pythonPath || !fs.existsSync(pythonPath)) {
    return { valid: false, error: '文件不存在' };
  }
  return validatePython(pythonPath);
});

ipcMain.handle('python:browse', async () => {
  const result = dialog.showOpenDialogSync({
    title: '选择 Python 可执行文件',
    filters: [
      { name: 'Python', extensions: ['exe'] },
      { name: '所有文件', extensions: ['*'] }
    ],
    properties: ['openFile']
  });
  if (!result || result.length === 0) return null;
  const selectedPath = result[0];
  const validation = await validatePython(selectedPath);
  return { path: selectedPath, ...validation };
});

ipcMain.handle('python:download', async (event) => {
  try {
    const pythonExe = await downloadAndInstallPython((stage, percent, message) => {
      event.sender.send('python-download-progress', { stage, percent, message });
    });
    store.set('pythonPath', pythonExe);
    return { success: true, path: pythonExe };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

ipcMain.handle('python:isEmbeddedInstalled', () => {
  return isEmbeddedPythonInstalled();
});

// Rust bridge 管理 IPC
ipcMain.handle('rust:getStatus', async () => {
  return checkRustBridge();
});

ipcMain.handle('rust:setEnabled', (event, enabled) => {
  store.set('disableRust', !enabled);
  return true;
});

ipcMain.handle('rust:isEnabled', () => {
  return store.get('disableRust') !== true;
});

ipcMain.handle('rust:build', async (event) => {
  try {
    const success = await buildRustBridge((stage, percent, message) => {
      event.sender.send('rust-build-progress', { stage, percent, message });
    });
    return { success };
  } catch (err) {
    return { success: false, error: err.message };
  }
});

// 应用就绪
app.whenReady().then(async () => {
  console.log('FinHack Pro 正在启动...');
  
  // 创建加载窗口
  createLoadWindow();
  
  try {
    // 第一步：环境检测与自动排障
    const envOk = await checkEnvironment();
    if (!envOk) return; // 用户选择退出
    
    // 第二步：启动 Python 后端
    await startPythonBackend();
    
    // 第三步：尝试启动 Rust 加速后端（可选，失败不阻塞）
    try {
      const rustStarted = await startRustBridge();
      if (rustStarted) {
        console.log('Rust 加速后端已启动');
        // 通知加载窗口
        if (loadWindow && !loadWindow.isDestroyed()) {
          loadWindow.webContents.send('loading-status', {
            message: 'Rust 加速引擎已就绪 🚀',
            progress: 95
          });
        }
      } else {
        console.log('Rust 加速不可用，使用纯 Python 模式');
      }
    } catch (rustErr) {
      console.warn('Rust 启动异常（不影响使用）:', rustErr.message);
    }
    
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
    await stopRustBridge();
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
