# finhack-pyo3

FinHack Pro 的高性能 Python-Rust 绑定（PyO3）。

## 功能

- 技术指标计算：RSI / EMA / MACD / 布林带 / ATR（rayon 并行）
- 批量回测：双均线策略批量参数扫描（rayon 并行策略）
- 并行信号计算：多标的信号计算（rayon 并行标的）
- 绩效统计：最大回撤、夏普比率
- 全部函数 `catch_unwind` 保护，Rust panic 不会崩溃 Python 进程

## 构建

```bash
pip install maturin
cd crates/finhack-pyo3
maturin develop --release   # 开发模式（安装进当前 venv）
# 或打包
maturin build --release
```

## 使用

Python 侧通过 `finhack_pro.backtest.pyo3_isolated.PyO3Isolated`
在独立子进程中加载本模块，数据经共享内存传输，进程级容灾。

```python
from finhack_pro.backtest import get_pyo3_isolated

rust = get_pyo3_isolated()
if rust.is_available:
    status, result = rust.calculate_indicators(closes, highs, lows, ["rsi", "macd"])
```
