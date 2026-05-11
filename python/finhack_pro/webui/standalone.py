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


def main():
    """主入口"""
    # 设置工作目录
    base_path = get_base_path()
    os.chdir(base_path)
    
    # 设置环境变量
    os.environ['FINHACK_STANDALONE'] = '1'
    
    # 添加路径到 sys.path 以确保模块可以正确导入
    if base_path not in sys.path:
        sys.path.insert(0, base_path)
    
    # 查找配置文件路径
    config_path = None
    
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后：配置文件在 sys._MEIPASS/config/
        possible_config_paths = [
            os.path.join(sys._MEIPASS, 'config', 'default.yaml'),
            os.path.join(os.path.dirname(sys.executable), 'config', 'default.yaml'),
        ]
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
            # 调试：打印配置文件内容
            try:
                with open(p, 'r') as f:
                    content = f.read()
                    print(f"[Config] 配置文件内容:\n{content[:500]}")
            except Exception as e:
                print(f"[Config] 读取配置文件失败: {e}")
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
