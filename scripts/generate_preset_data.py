"""
生成预置示例数据
用于桌面版开箱即用体验
"""
import sys
import io
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os

# 修复Windows控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def generate_stock_data(symbol, name, start_price, volatility, trend, days=500):
    """
    生成模拟股票数据
    
    Args:
        symbol: 股票代码
        name: 股票名称
        start_price: 起始价格
        volatility: 波动率
        trend: 趋势 (正数上涨, 负数下跌)
        days: 天数
    """
    np.random.seed(hash(symbol) % 2**32)
    
    dates = pd.date_range(start='2023-01-01', periods=days, freq='B')  # 工作日
    
    # 生成价格序列
    returns = np.random.normal(trend/252, volatility/np.sqrt(252), days)
    prices = start_price * np.exp(np.cumsum(returns))
    
    # 生成OHLCV数据
    data = pd.DataFrame({
        'date': dates.strftime('%Y-%m-%d'),
        'open': prices * (1 + np.random.uniform(-0.01, 0.01, days)),
        'high': prices * (1 + np.random.uniform(0.005, 0.02, days)),
        'low': prices * (1 - np.random.uniform(0.005, 0.02, days)),
        'close': prices,
        'volume': np.random.randint(1000000, 10000000, days),
        'amount': prices * np.random.randint(1000000, 10000000, days),
    })
    
    return data

def main():
    # 获取脚本所在目录的上级目录作为项目根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    output_dir = os.path.join(project_root, 'data', 'preset')
    os.makedirs(output_dir, exist_ok=True)
    
    stocks = [
        ('600519.SH', '贵州茅台', 1800, 0.25, 0.15),   # 上涨趋势
        ('000001.SZ', '平安银行', 12, 0.30, 0.05),     # 小幅上涨
        ('300750.SZ', '宁德时代', 200, 0.35, 0.10),    # 波动较大
        ('00700.HK', '腾讯控股', 300, 0.30, 0.08),     # 中概股
    ]
    
    for symbol, name, price, vol, trend in stocks:
        df = generate_stock_data(symbol, name, price, vol, trend)
        output_file = os.path.join(output_dir, f'{symbol}.csv')
        df.to_csv(output_file, index=False, encoding='utf-8')
        print(f'生成 {name} ({symbol}) 数据: {len(df)} 条记录')
    
    print(f'\n数据已保存到 {output_dir}/')

if __name__ == '__main__':
    main()
