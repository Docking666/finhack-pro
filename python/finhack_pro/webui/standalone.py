"""
FinHack Pro 独立运行入口
用于 PyInstaller 打包后的可执行文件
"""
import os
import sys
import asyncio


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
    
    # 导入并运行FastAPI应用
    from finhack_pro.webui.app import create_app
    import uvicorn
    
    # 创建应用实例
    app = create_app()
    
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
