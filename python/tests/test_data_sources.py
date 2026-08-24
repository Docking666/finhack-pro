"""可插拔数据源架构回归测试（SDD：真实多源回退、失败显式化、无 mock 兜底）

覆盖：
  - build_source_chain legacy 映射与显式列表
  - 自定义源加载（load_custom_source）
  - DataFetcher 依序真实回退 / 全部失败显式抛错
  - 失败绝不返回空表/伪造数据
  - 符号转换工具
"""

import pandas as pd
import pytest

from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.data.sources import (
    BaseDataSource,
    build_source_chain,
    load_custom_source,
    to_baostock_symbol,
    to_tushare_symbol,
    to_tx_symbol,
)


class _FakeOK(BaseDataSource):
    """返回确定真实结构数据的测试源（行为确定，不联网）"""

    name = "fake_ok"

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame({
            "date": pd.to_datetime(["2026-08-20", "2026-08-21"]),
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.9, 1.8],
            "close": [1.2, 2.2],
            "volume": [100, 200],
        })


class _FakeFail(BaseDataSource):
    """模拟远端断开/连接失败的测试源"""

    name = "fake_fail"

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise ConnectionError("模拟远端断开")


class _FakeEmpty(BaseDataSource):
    """模拟返回空表的测试源"""

    name = "fake_empty"

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()


def _make_fetcher(tmp_path, **kwargs) -> DataFetcher:
    return DataFetcher(cache_dir=str(tmp_path), **kwargs)


# ---------- build_source_chain ----------

def test_legacy_akshare_maps_to_tx_first(tmp_path):
    """legacy source=akshare 应映射为腾讯优先链（绕开东财封锁），不含 tushare（无 token）"""
    f = _make_fetcher(tmp_path, source="akshare")
    names = [s.name for s in f._sources]
    assert names[0] == "akshare_tx"
    assert "akshare_em" in names
    assert "tushare" not in names
    assert f._akshare_available is True
    assert f._tushare_available is False


def test_legacy_tushare_with_token_includes_fallback(tmp_path):
    """legacy source=tushare + token：tushare 优先，akshare 作回退"""
    f = _make_fetcher(tmp_path, source="tushare", tushare_token="dummy")
    names = [s.name for s in f._sources]
    assert names[0] == "tushare"
    assert f._tushare_available is True
    assert f._akshare_available is True


def test_explicit_sources_skip_tushare_without_token(tmp_path):
    """显式 sources 列表：tushare 无 token 应被跳过，不伪造可用源"""
    f = _make_fetcher(
        tmp_path,
        sources=["akshare_tx", "baostock", "tushare"],
        tushare_token="",
    )
    names = [s.name for s in f._sources]
    assert names == ["akshare_tx", "baostock"]  # tushare 因无 token 跳过


def test_unknown_source_skipped(tmp_path):
    """未知源名应跳过并告警，不影响已知源"""
    f = _make_fetcher(tmp_path, sources=["nope_unknown", "akshare_em"])
    assert [s.name for s in f._sources] == ["akshare_em"]


def test_empty_chain_raises(tmp_path):
    """显式列表全部无效且无自定义源 → 构造时抛 ValueError（不静默降级）"""
    with pytest.raises(ValueError, match="数据源配置无效"):
        _make_fetcher(tmp_path, sources=["unknown_a", "unknown_b"])


# ---------- load_custom_source ----------

def test_custom_source_loading():
    """自定义源可动态加载且必须是 BaseDataSource 子类"""
    src = load_custom_source("finhack_pro.data.sources.AkshareTXDataSource")
    assert isinstance(src, BaseDataSource)
    assert src.name == "akshare_tx"


def test_custom_source_invalid_raises():
    """无效自定义源（模块不存在/非子类）应显式抛错"""
    with pytest.raises((ImportError, ValueError)):
        load_custom_source("nonexistent.module.Whatever")
    with pytest.raises(ValueError):
        load_custom_source("finhack_pro.data.fetcher.DataFetcher")  # 非 BaseDataSource 子类


# ---------- DataFetcher 真实回退 ----------

def test_fallback_to_next_source_on_failure(tmp_path):
    """主源失败（异常）应真实回退到下一个可用源，返回真实数据"""
    f = _make_fetcher(tmp_path, source="akshare")
    f._sources = [_FakeFail(), _FakeOK()]
    df = f.get_daily("600519", "2026-08-01", "2026-08-24")
    assert len(df) == 2
    assert {"date", "open", "high", "low", "close", "volume"} <= set(df.columns)


def test_fallback_on_empty_result(tmp_path):
    """主源返回空表也应视为失败并回退"""
    f = _make_fetcher(tmp_path, source="akshare")
    f._sources = [_FakeEmpty(), _FakeOK()]
    df = f.get_daily("600519", "2026-08-01", "2026-08-24")
    assert len(df) == 2


def test_all_sources_fail_raises_valueerror(tmp_path):
    """全部源失败（异常+空表）→ 显式抛 ValueError，绝不返回空表伪装成功"""
    f = _make_fetcher(tmp_path, source="akshare")
    f._sources = [_FakeFail(), _FakeEmpty()]
    with pytest.raises(ValueError) as ei:
        f.get_daily("600519", "2026-08-01", "2026-08-24")
    msg = str(ei.value)
    assert "数据源获取失败" in msg
    assert "fake_fail" in msg  # 错误明细含每个源的失败原因


def test_no_fake_data_never_returns_empty_df(tmp_path):
    """失败路径绝不返回空 DataFrame 或伪造行（SDD：禁止 mock 兜底）"""
    f = _make_fetcher(tmp_path, source="akshare")
    f._sources = [_FakeFail()]
    with pytest.raises(ValueError):
        f.get_daily("600519", "2026-08-01", "2026-08-24")
    # 异常优先于任何返回值


# ---------- 符号转换 ----------

def test_symbol_converters():
    assert to_tx_symbol("600519") == "sh600519"
    assert to_tx_symbol("000001") == "sz000001"
    assert to_tx_symbol("430047") == "bj430047"
    assert to_tx_symbol("sh600519") == "sh600519"
    assert to_baostock_symbol("600519") == "sh.600519"
    assert to_baostock_symbol("000001") == "sz.000001"
    assert to_tushare_symbol("600519") == "600519.SH"
    assert to_tushare_symbol("000001") == "000001.SZ"
    assert to_tushare_symbol("430047") == "430047.BJ"
