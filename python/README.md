# Python策略层

此目录用于存放Python策略脚本，通过pyo3与Rust核心层交互。

## 使用方式

```python
# 示例策略 (后续实现)
import finhack_pro

class MyStrategy(finhack_pro.Strategy):
    def on_bar(self, bar, portfolio):
        # 策略逻辑
        pass
```
