/**
 * FinHack Pro - Electron 预加载脚本
 * 多智能体量化交易系统桌面版
 * 
 * 功能：
 * 暴露安全的 API 给渲染进程
 */

const { contextBridge, ipcRenderer, shell, app } = require('electron');
const os = require('os');
const path = require('path');

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  /**
   * 获取操作系统类型
   * @returns {string} 'windows' | 'macos' | 'linux'
   */
  platform: (() => {
    const platform = process.platform;
    if (platform === 'win32') return 'windows';
    if (platform === 'darwin') return 'macos';
    return 'linux';
  })(),
  
  /**
   * 获取应用版本
   * @returns {string} 应用版本号
   */
  version: process.env.npm_package_version || '1.0.0',
  
  /**
   * 获取应用详细版本信息
   * @returns {Object} 版本信息对象
   */
  getVersionInfo: () => ({
    version: process.env.npm_package_version || '1.0.0',
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
    v8: process.versions.v8,
    os: os.type(),
    osVersion: os.release(),
    arch: os.arch()
  }),
  
  /**
   * 在默认浏览器中打开链接
   * @param {string} url - 要打开的 URL
   * @returns {Promise<void>}
   */
  openExternal: (url) => {
    // 验证 URL 格式
    try {
      const parsedUrl = new URL(url);
      // 只允许 http 和 https 协议
      if (parsedUrl.protocol !== 'http:' && parsedUrl.protocol !== 'https:') {
        console.error('不支持的协议:', parsedUrl.protocol);
        return Promise.reject(new Error('不支持的协议'));
      }
      return shell.openExternal(url);
    } catch (error) {
      console.error('无效的 URL:', url, error);
      return Promise.reject(error);
    }
  },
  
  /**
   * 获取应用路径
   * @returns {Object} 应用相关路径
   */
  getAppPath: () => ({
    // 用户数据目录
    userData: path.join(os.homedir(), '.finhack-pro'),
    // 应用名称
    name: 'FinHack Pro',
    // 用户主目录
    home: os.homedir(),
    // 临时目录
    temp: os.tmpdir()
  }),
  
  /**
   * 获取系统信息
   * @returns {Object} 系统信息
   */
  getSystemInfo: () => ({
    platform: process.platform,
    arch: os.arch(),
    hostname: os.hostname(),
    username: os.userInfo().username,
    cpus: os.cpus().length,
    totalMemory: Math.round(os.totalmem() / (1024 * 1024 * 1024)), // GB
    freeMemory: Math.round(os.freemem() / (1024 * 1024 * 1024)), // GB
    uptime: Math.round(os.uptime() / 3600) // 小时
  }),
  
  /**
   * 发送消息到主进程
   * @param {string} channel - 消息通道
   * @param {any} data - 消息数据
   */
  send: (channel, data) => {
    const validChannels = ['app:quit', 'window:minimize', 'window:maximize', 'window:close'];
    if (validChannels.includes(channel)) {
      ipcRenderer.send(channel, data);
    }
  },
  
  /**
   * 接收主进程消息
   * @param {string} channel - 消息通道
   * @param {Function} callback - 回调函数
   */
  receive: (channel, callback) => {
    const validChannels = ['loading-status', 'update-available', 'update-downloaded'];
    if (validChannels.includes(channel)) {
      ipcRenderer.on(channel, (event, ...args) => callback(...args));
    }
  },
  
  /**
   * 移除消息监听
   * @param {string} channel - 消息通道
   */
  removeAllListeners: (channel) => {
    ipcRenderer.removeAllListeners(channel);
  },
  
  /**
   * 检查更新
   * @returns {Promise<Object>} 更新信息
   */
  checkForUpdates: async () => {
    try {
      const { autoUpdater } = require('electron-updater');
      const result = await autoUpdater.checkForUpdates();
      return {
        available: result.updateInfo.version !== process.env.npm_package_version,
        version: result.updateInfo.version,
        releaseDate: result.updateInfo.releaseDate
      };
    } catch (error) {
      console.error('检查更新失败:', error);
      return { available: false, error: error.message };
    }
  },
  
  /**
   * 存储数据
   */
  store: {
    get: (key) => ipcRenderer.invoke('store:get', key),
    set: (key, value) => ipcRenderer.invoke('store:set', key, value),
    delete: (key) => ipcRenderer.invoke('store:delete', key),
    clear: () => ipcRenderer.invoke('store:clear')
  }
});

// 打印加载完成信息
console.log('预加载脚本已执行');
console.log('平台:', process.platform);
console.log('架构:', process.arch);
