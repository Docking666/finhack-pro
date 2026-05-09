"""
FinHack Pro 测试公共配置和 Fixtures

提供所有测试模块共享的:
- 测试数据生成器
- 通用 Mock 对象
- 环境变量控制
"""

import os
import sys
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# 环境变量控制
# ============================================================================

@pytest.fixture(autouse=True)
def _disable_http_requests(monkeypatch):
    """默认禁用外部 HTTP 请求，防止测试中意外调用 API"""
    # 不实际 patch，由各测试按需控制
    yield


# ============================================================================
# 测试数据生成器
# ============================================================================

@pytest.fixture
def sample_bars_100():
    """生成 100 根标准 K 线数据"""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2024-01-01", periods=n, freq="5min")
    return pd.DataFrame({
        "date": dates,
        "open": np.random.uniform(90, 110, n),
        "high": np.random.uniform(100, 120, n),
        "low": np.random.uniform(80, 100, n),
        "close": np.random.uniform(90, 110, n),
        "volume": np.random.uniform(1000, 10000, n),
        "amount": np.random.uniform(100000, 1000000, n),
    })


@pytest.fixture
def sample_bars_1000():
    """生成 1000 根标准 K 线数据"""
    np.random.seed(42)
    n = 1000
    dates = pd.date_range("2024-01-01", periods=n, freq="5min")
    return pd.DataFrame({
        "date": dates,
        "open": np.random.uniform(90, 110, n),
        "high": np.random.uniform(100, 120, n),
        "low": np.random.uniform(80, 100, n),
        "close": np.random.uniform(90, 110, n),
        "volume": np.random.uniform(1000, 10000, n),
        "amount": np.random.uniform(100000, 1000000, n),
    })


@pytest.fixture
def sample_multi_symbol_data():
    """生成多标的测试数据"""
    data_dict = {}
    for sym in ["SYM_A", "SYM_B", "SYM_C"]:
        np.random.seed(hash(sym) % 2**31)
        n = 500
        data_dict[sym] = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="5min"),
            "open": np.random.uniform(90, 110, n),
            "high": np.random.uniform(100, 120, n),
            "low": np.random.uniform(80, 100, n),
            "close": np.random.uniform(90, 110, n),
            "volume": np.random.uniform(1000, 10000, n),
            "amount": np.random.uniform(100000, 1000000, n),
        })
    return data_dict


@pytest.fixture
def sample_equity_curve():
    """生成示例权益曲线"""
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.02, 252)
    equity = 1_000_000 * np.cumprod(1 + returns)
    return equity


@pytest.fixture
def sample_returns():
    """生成示例日收益率序列"""
    np.random.seed(42)
    return np.random.normal(0.001, 0.02, 252)


# ============================================================================
# 临时目录
# ============================================================================

@pytest.fixture
def tmp_dir(tmp_path):
    """提供临时目录（自动清理）"""
    return tmp_path


@pytest.fixture
def tmp_data_dir(tmp_path):
    """提供临时数据目录"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir
