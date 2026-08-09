"""
FinHack Pro 独立运行入口
用于 PyInstaller 打包后的可执行文件
"""
import asyncio
import os
import sys


def get_base_path():
    """获取应用基础路径（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的路径
        return sys._MEIPASS
    # 开发环境路径
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_user_data_dir():
    """获取持久化用户数据目录（配置/日志/数据缓存均存于此）

    PyInstaller onefile 的 sys._MEIPASS 是临时解包目录，每次启动随机生成、
    退出即销毁。若将工作目录设为 _MEIPASS，用户保存的配置、日志、数据缓存
    会在重启后全部丢失。这里统一使用用户数据目录：
      - Windows: %APPDATA%/finhack-pro
      - macOS:   ~/Library/Application Support/finhack-pro
      - Linux:   ~/.config/finhack-pro
    """
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        user_dir = os.path.join(base, 'finhack-pro')
    elif sys.platform == 'darwin':
        user_dir = os.path.expanduser('~/Library/Application Support/finhack-pro')
    else:
        base = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        user_dir = os.path.join(base, 'finhack-pro')
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


def main():
    """主入口"""
    # 设置工作目录：优先用户数据目录，确保配置/日志/数据持久化
    frozen = getattr(sys, 'frozen', False)
    if frozen:
        user_dir = get_user_data_dir()
        os.chdir(user_dir)
        base_path = user_dir
    else:
        base_path = get_base_path()
        os.chdir(base_path)
    
    # 设置环境变量
    os.environ['FINHACK_STANDALONE'] = '1'
    
    # 添加路径到 sys.path 以确保模块可以正确导入
    if frozen and get_base_path() not in sys.path:
        sys.path.insert(0, get_base_path())
    elif not frozen and base_path not in sys.path:
        sys.path.insert(0, base_path)
    
    # 查找配置文件路径
    config_path = None

    if frozen:
        # PyInstaller 打包后：
        # 1. 优先使用用户数据目录中的配置文件（持久化，用户可改）
        # 2. 不存在则从内置默认配置复制一份过去（含打包时的默认值）
        user_config = os.path.join(get_user_data_dir(), 'config', 'default.yaml')
        if os.path.exists(user_config):
            config_path = user_config
            print(f"[Config] 找到用户配置文件: {config_path}")
        else:
            builtin_config = os.path.join(sys._MEIPASS, 'config', 'default.yaml')
            if os.path.exists(builtin_config):
                os.makedirs(os.path.dirname(user_config), exist_ok=True)
                try:
                    import shutil
                    shutil.copy2(builtin_config, user_config)
                    config_path = user_config
                    print(f"[Config] 已从内置配置初始化用户配置: {config_path}")
                except Exception as e:
                    print(f"[Config] 初始化用户配置失败({e})，使用内置配置")
                    config_path = builtin_config
            else:
                # 内置配置也没有，则用默认配置
                config_path = None
                print("[Config] 警告: 未找到配置文件，将使用默认配置")
    else:
        # 开发环境
        possible_config_paths = [
            os.path.join(base_path, 'config', 'default.yaml'),
            os.path.join(base_path, '..', 'config', 'default.yaml'),
        ]
        for p in possible_config_paths:
            if os.path.exists(p):
                config_path = p
                print(f"[Config] 找到配置文件: {config_path}")
                break

    if not config_path:
        print("[Config] 警告: 未找到配置文件，将使用默认配置")
    
    # 导入并运行FastAPI应用
    import uvicorn

    from finhack_pro.webui.app import create_app
    
    # 创建应用实例
    app = create_app(config_path=config_path)
    
    print("=" * 50)
    print("  FinHack Pro 多智能体量化交易系统")
    print("  版本: 1.0.0")
    print("=" * 50)
    print()
    print("  WebUI 地址: http://localhost:8000")
    print("  按 Ctrl+C 退出")
    print()
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    main()
