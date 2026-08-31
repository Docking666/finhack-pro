"""free-stockdb 适配器的回归测试

核心不变量：
1. **默认只连本机**——公共服务器批量拉取会触发风控返回随机 mock 数据
2. **mock 数据必须被拦下**——诱饵检测命中即抛错，绝不静默入库
3. **复权口径一致**——上游 日k 表是不复权原始价，qfq 由本适配器现算，
   公式必须与上游 SDK 一致，否则仓库混入两套价格口径
4. **响应解析不容静默空**——无法解析的响应显式报错，空表会被上层
   当成"区间无数据"造成股票池静默缺失
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from finhack_pro.data.collector import MarketDataCollector
from finhack_pro.data.free_stockdb import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    FreeStockDBClient,
    FreeStockDBDecoyError,
    FreeStockDBError,
    FreeStockDBSource,
)
from finhack_pro.data.registry import default_registry
from finhack_pro.data.sources import RetryDataSource, build_source_chain
from finhack_pro.data.warehouse import MarketWarehouse

# ============================================================================
# 测试替身：模拟引擎 HTTP 响应
# ============================================================================


def _daily_record(date: str, close: float, pre: float | None = None, pct: float | None = None) -> dict:
    """构造一条与上游字段布局一致的日K记录。"""
    r = {
        "code": "600519",
        "date": int(date.replace("-", "")),
        "open": round(close * 0.99, 2),
        "high": round(close * 1.01, 2),
        "low": round(close * 0.98, 2),
        "close": close,
        "volume": 1_800_000,
        "amount": 18_901_000,
        "turnover": 1.42,
        "is_st": False,
        "name": "贵州茅台",
    }
    if pre is not None:
        r["pre_close"] = pre
    if pct is not None:
        r["pct_chg"] = pct
    return r


def _factor_record(date: str, cum: float) -> dict:
    return {"code": "600519", "date": int(date.replace("-", "")), "cum": cum}


class _StubEngine:
    """按 table 表达式返回预设响应，可记录请求 URL 供断言。"""

    def __init__(self, tables: dict[str, object]):
        self.tables = tables
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        expr = url.split("t=", 1)[1]
        table = expr.split(":", 1)[0]
        if table not in self.tables:
            return []
        return self.tables[table]


def _ok_engine() -> _StubEngine:
    """一只复权过的股票：2024-06-10 发生 10送10（cum 翻倍）。"""
    daily = [
        _daily_record("2024-06-05", 100.0, pre=99.0, pct=1.01),
        _daily_record("2024-06-06", 101.0, pre=100.0, pct=1.0),
        _daily_record("2024-06-09", 102.0, pre=101.0, pct=0.99),
        # 除权日：原始价从 102 跳到 51（1:2 拆分），cum 从 1.0 变 2.0
        _daily_record("2024-06-10", 51.0, pre=102.0, pct=-50.0),
        _daily_record("2024-06-11", 51.5, pre=51.0, pct=0.98),
    ]
    return _StubEngine(
        {
            "股票代码": {"6": ["600519", "600633"], "0": ["000001"]},
            "日k": daily,
            "复权": [
                _factor_record("2024-01-01", 1.0),
                _factor_record("2024-06-10", 2.0),
            ],
        }
    )


# ============================================================================
# 客户端基础
# ============================================================================


def test_default_is_localhost():
    assert DEFAULT_HOST == "127.0.0.1"
    assert DEFAULT_PORT == 7899


def test_ping_unreachable_raises_with_guidance():
    client = FreeStockDBClient(transport=lambda url: (_ for _ in ()).throw(OSError("refused")))
    with pytest.raises(FreeStockDBError, match="stockdb.exe"):
        client.ping()


def test_list_symbols_flattens_groups():
    client = FreeStockDBClient(transport=_ok_engine())
    assert client.list_symbols() == ["000001", "600519", "600633"]


def test_range_query_url_format():
    """范围语法必须是上游约定的 lo<hi 裸数字。"""
    eng = _ok_engine()
    client = FreeStockDBClient(transport=eng)
    client.get_daily_raw("600519", "2024-06-01", "2024-06-30")
    assert "t=日k:600519:20240601<20240630" in eng.urls[0]


def test_unparseable_response_raises_not_silent_empty():
    """响应不可解析必须报错——静默空表会被当成"区间无数据"。"""
    client = FreeStockDBClient(transport=lambda url: "不是JSON")
    with pytest.raises(FreeStockDBError, match="无法解析"):
        client.get_daily_raw("600519", "2024-01-01", "2024-12-31")


def test_null_response_yields_empty():
    client = FreeStockDBClient(transport=lambda url: None)
    assert client.get_daily_raw("600519", "2024-01-01", "2024-12-31") == []


# ============================================================================
# 诱饵检测（风控 mock 数据）
# ============================================================================


def test_decoy_detected_when_pct_chg_inconsistent():
    """随机 mock 的字段彼此独立生成，交叉校验必然露馅。"""
    records = []
    for i in range(20):
        close = 100.0 + i
        records.append(
            _daily_record(f"2024-06-{i + 1:02d}", close, pre=close, pct=float(i % 7) * 3.7)
        )  # pct 与 close/pre_close 明显矛盾
    client = FreeStockDBClient(transport=lambda url: records)
    with pytest.raises(FreeStockDBDecoyError, match="mock"):
        client.get_daily_frame("600519", "2024-01-01", "2024-12-31", adjust="")


def test_decoy_detected_when_all_records_identical():
    base = _daily_record("2024-06-05", 100.0)
    records = []
    for i in range(20):
        r = dict(base)
        r["date"] = int(f"202406{i + 10:02d}")
        records.append(r)
    client = FreeStockDBClient(transport=lambda url: records)
    with pytest.raises(FreeStockDBDecoyError, match="完全相同"):
        client.get_daily_frame("600519", "2024-01-01", "2024-12-31", adjust="")


def test_real_data_passes_decoy_check():
    """正常行情（pct 与 close/pre_close 一致）不得误报。"""
    records = []
    close = 100.0
    for i in range(20):
        pre = close
        close = round(close * (1 + 0.01 * ((i % 3) - 1)), 2)
        pct = round((close / pre - 1) * 100, 2)
        records.append(_daily_record(f"2024-06-{i + 1:02d}", close, pre=pre, pct=pct))
    client = FreeStockDBClient(transport=lambda url: records)
    client.check_decoy(records)  # 不抛即通过


def test_short_series_skips_decoy_check():
    """样本 <5 条不做统计判定（避免小样本误报）。"""
    records = [_daily_record("2024-06-05", 100.0, pre=1.0, pct=999.0)]
    client = FreeStockDBClient(transport=lambda url: records)
    client.check_decoy(records)


# ============================================================================
# 复权（口径一致性）
# ============================================================================


def test_qfq_matches_upstream_formula():
    """除权日前的价格应按 f_current/f_latest 折算：102 -> 51。"""
    eng = _ok_engine()
    client = FreeStockDBClient(transport=eng)
    df = client.get_daily_frame("600519", "2024-06-01", "2024-06-30", adjust="qfq")

    assert len(df) == 5
    # 6-09 原始 102，cum=1.0，f_latest=2.0 -> 102 * 1.0 / 2.0 = 51
    assert df.loc[df["date"] == "2024-06-09", "close"].iloc[0] == pytest.approx(51.0)
    # 除权日后原始价已是拆分后口径，cum=2.0 -> 51 * 2.0/2.0 = 51（不变）
    assert df.loc[df["date"] == "2024-06-10", "close"].iloc[0] == pytest.approx(51.0)
    # 复权后序列连续，无除权跳空
    closes = df["close"].tolist()
    assert max(closes) / min(closes) < 1.2


def test_adjust_empty_returns_raw_prices():
    """adjust='' 必须返回原始价——两套口径不能混。"""
    eng = _ok_engine()
    client = FreeStockDBClient(transport=eng)
    df = client.get_daily_frame("600519", "2024-06-01", "2024-06-30", adjust="")
    assert df.loc[df["date"] == "2024-06-09", "close"].iloc[0] == pytest.approx(102.0)


def test_no_factor_series_returns_raw():
    """复权表缺失时原样返回（新股/无除权），不得抛错。"""
    eng = _StubEngine({"日k": [_daily_record(f"2024-06-{d:02d}", 100.0 + d) for d in range(1, 4)]})
    client = FreeStockDBClient(transport=eng)
    df = client.get_daily_frame("600519", "2024-06-01", "2024-06-30", adjust="qfq")
    assert len(df) == 3
    assert df["close"].iloc[0] == pytest.approx(101.0)


# ============================================================================
# 标准化
# ============================================================================


def test_daily_frame_column_layout():
    eng = _ok_engine()
    client = FreeStockDBClient(transport=eng)
    df = client.get_daily_frame("600519", "2024-06-01", "2024-06-30", adjust="")

    for col in ("date", "open", "high", "low", "close", "volume"):
        assert col in df.columns
    assert "amount" in df.columns and "is_st" in df.columns
    assert df["date"].is_monotonic_increasing


def test_daily_frame_dedupes_dates():
    """上游偶发重复键：保留最后一条并告警，不产生重复日期行。"""
    eng = _StubEngine(
        {"日k": [_daily_record("2024-06-05", 100.0), _daily_record("2024-06-05", 101.0)]}
    )
    client = FreeStockDBClient(transport=eng)
    df = client.get_daily_frame("600519", "2024-06-01", "2024-06-30", adjust="")
    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(101.0)


# ============================================================================
# 数据源契约与注册中心
# ============================================================================


def test_source_is_base_datasource_subclass():
    from finhack_pro.data.sources import BaseDataSource

    assert issubclass(FreeStockDBSource, BaseDataSource)
    src = FreeStockDBSource()
    assert src.name == "free_stockdb"


def test_source_registered_in_registry():
    from finhack_pro.data.registry import _register_builtins

    _register_builtins()
    assert "free_stockdb" in default_registry
    spec = default_registry.spec("free_stockdb")
    assert spec is not None
    assert spec.required_config == ()  # 本机默认即可用，无需必需配置


def test_source_chained_via_build_source_chain():
    chain = build_source_chain(sources=["free_stockdb"])
    assert [s.name for s in chain] == ["free_stockdb"]
    assert isinstance(chain[0], RetryDataSource)


def test_source_default_client_is_localhost():
    src = FreeStockDBSource()
    assert src.client.host == "127.0.0.1"
    assert src.client.port == 7899


def test_source_explicit_host_is_honored():
    src = FreeStockDBSource(free_stockdb_host="192.168.1.10", free_stockdb_port=7900)
    assert src.client.host == "192.168.1.10"
    assert src.client.port == 7900


# ============================================================================
# 端到端：source -> collector -> warehouse
# ============================================================================


def test_end_to_end_import_into_warehouse(tmp_path):
    """插件化收益：导入器只是一行 MarketDataCollector。"""
    wh = MarketWarehouse(tmp_path / "wh")
    source = FreeStockDBSource()
    source.client._transport = _ok_engine()

    collector = MarketDataCollector(wh, source, max_workers=1, jitter=(0, 0))
    report = collector.run(["600519"], start="2024-06-01", end="2024-06-30")

    assert report.ok
    assert report.ingested == 1
    df = wh.get("600519", "2024-06-01", "2024-06-30")
    assert len(df) == 5
    # 仓库里存的是 qfq 口径
    assert df["close"].iloc[2] == pytest.approx(51.0)


def test_collector_rejects_decoy_data(tmp_path):
    """mock 数据必须被拒收并计入 rejected，绝不入库。"""
    records = []
    for i in range(20):
        close = 100.0 + i
        records.append(
            _daily_record(f"2024-06-{i + 1:02d}", close, pre=close, pct=float(i % 5) * 9.1)
        )
    wh = MarketWarehouse(tmp_path / "wh")
    source = FreeStockDBSource()
    source.client._transport = _StubEngine({"日k": records, "复权": []})

    collector = MarketDataCollector(wh, source, max_workers=1, jitter=(0, 0))
    report = collector.run(["600519"], start="2024-01-01", end="2024-12-31")

    assert not report.ok
    assert "600519" in report.failed
    assert "mock" in report.failed["600519"]
    assert not wh.exists("600519")  # 拒收即不落盘


# ============================================================================
# 分钟线
# ============================================================================


def _min_record(ymd_hms: str, close: float, volume: int = 100) -> dict:
    o = close
    return {
        "code": "600519",
        "date": int(ymd_hms),
        "open": o,
        "high": round(close * 1.001, 3),
        "low": round(close * 0.999, 3),
        "close": close,
        "volume": volume,
        "amount": volume * close,
    }


def test_minute_frame_url_uses_14_digit_range():
    eng = _StubEngine({"分钟k": [_min_record("20240605093000", 10.0)]})
    client = FreeStockDBClient(transport=eng)
    client.get_minute_frame("600519", "2024-06-05", "2024-06-05", period=1)
    assert "t=分钟k:600519:20240605000000<20240605235959" in eng.urls[0]


def test_minute_frame_datetime_column(tmp_path):
    recs = [_min_record("20240605093000", 10.0), _min_record("20240605093100", 10.1)]
    client = FreeStockDBClient(transport=_StubEngine({"分钟k": recs}))
    df = client.get_minute_frame("600519", "2024-06-05", "2024-06-05", period=1, adjust="")
    assert str(df["date"].dtype).startswith("datetime64")
    assert df["date"].is_monotonic_increasing


def test_minute_qfq_uses_date_part_of_timestamp():
    """分钟 bar 复权按日期查因子（时间戳截前 8 位）。"""
    recs = [_min_record("20240605100000", 100.0)]
    eng = _StubEngine(
        {"分钟k": recs, "复权": [_factor_record("2024-01-01", 1.0), _factor_record("2024-06-10", 2.0)]}
    )
    client = FreeStockDBClient(transport=eng)
    df = client.get_minute_frame("600519", "2024-06-05", "2024-06-05", period=1, adjust="qfq")
    assert df["close"].iloc[0] == pytest.approx(50.0)  # 100 * 1.0/2.0


def test_source_get_minute_contract():
    """MarketDataCollector 的 min* 频率会以 (symbol, start, end, period) 调用。"""
    src = FreeStockDBSource()
    src.client._transport = _StubEngine(
        {"分钟k": [_min_record("20240605093000", 10.0), _min_record("20240605093100", 10.1)]}
    )
    df = src.get_minute("600519", "2024-06-05", "2024-06-05", period="5")
    assert len(df) == 1  # 两根 1 分钟聚成一根 5 分钟
    assert df["date"].iloc[0] == pd.Timestamp("2024-06-05 09:35:00")


def test_collector_imports_min5_into_warehouse(tmp_path):
    """freq=min5 -> 仓库 min5 分区，端到端。"""
    wh = MarketWarehouse(tmp_path / "wh")
    source = FreeStockDBSource()
    source.client._transport = _StubEngine(
        {"分钟k": [_min_record("20240605093000", 10.0), _min_record("20240605093100", 10.1)]}
    )
    collector = MarketDataCollector(wh, source, max_workers=1, jitter=(0, 0))
    report = collector.run(["600519"], start="2024-06-05", end="2024-06-05", freq="min5")

    assert report.ok and report.ingested == 1
    df = wh.get("600519", "2024-06-05", "2024-06-05", freq="min5")
    assert len(df) == 1


# ============================================================================
# 分钟线
# ============================================================================


def _min_record(ymd_hms: str, close: float, volume: int = 100) -> dict:
    o = close
    return {
        "code": "600519",
        "date": int(ymd_hms),
        "open": o,
        "high": round(close * 1.001, 3),
        "low": round(close * 0.999, 3),
        "close": close,
        "volume": volume,
        "amount": volume * close,
    }


def test_aggregate_5min_buckets_morning():
    """09:30-09:34 五根 1 分钟聚成一根标 09:35 的 5 分钟 bar。"""
    recs = [
        _min_record("20240605" + f"{930 + m:04d}" + "00", 10.0 + i * 0.1)
        for i, m in enumerate(range(0, 5))  # 09:30..09:34
    ]
    out = FreeStockDBClient.aggregate_minutes(recs, 5)
    assert len(out) == 1
    bar = out[0]
    assert bar["date"] == 20240605 * 1000000 + 93500  # 09:35:00
    assert bar["open"] == 10.0
    assert bar["close"] == 10.4
    # _min_record 的 high 是 round(close*1.001, 3)，断言须对齐同样的舍入
    assert bar["high"] == round(max(10.0 + i * 0.1 for i in range(5)) * 1.001, 3)
    assert bar["volume"] == 500


def test_aggregate_afternoon_alignment():
    """13:00 与 13:01 同属 elapsed=121 桶（上游约定）。"""
    recs = [
        _min_record("20240605130000", 20.0),
        _min_record("20240605130100", 20.1),
        _min_record("20240605130200", 20.2),
    ]
    out = FreeStockDBClient.aggregate_minutes(recs, 5)
    assert len(out) == 1
    # bucket_end = ceil(121/5)*5 = 125 -> label 13:05
    assert out[0]["date"] == 20240605130500


def test_aggregate_skips_lunch_and_offhours():
    """午休与盘外数据必须丢弃，不得混进 bar。"""
    recs = [
        _min_record("20240605113000", 10.0),   # 11:30 上午收盘，elapsed=120
        _min_record("20240605120000", 99.0),   # 午休，丢弃
        _min_record("20240605150000", 20.0),   # 15:00 收盘，elapsed=240
        _min_record("20240605091500", 88.0),   # 盘前，丢弃
    ]
    out = FreeStockDBClient.aggregate_minutes(recs, 30)
    dates = [b["date"] for b in out]
    assert all(99.0 not in (b["open"], b["close"]) for b in out)
    assert 20240605120000 not in dates
    assert 20240605091500 not in dates
    assert len(out) == 2  # 11:30 -> 上午末桶；15:00 -> 下午末桶


def test_aggregate_1min_returns_as_is():
    recs = [_min_record("20240605093000", 10.0)]
    out = FreeStockDBClient.aggregate_minutes(recs, 1)
    assert out is not recs and len(out) == 1


def test_aggregate_invalid_period_raises():
    with pytest.raises(ValueError):
        FreeStockDBClient.aggregate_minutes([_min_record("20240605093000", 1.0)], 0)

def test_datafetcher_passthrough_free_stockdb_config():
    """coordinator 侧透传的 host/port 必须到达链上的 free_stockdb 源。

    回归：曾因 DataFetcher 未声明 free_stockdb_host/port 参数，coordinator
    调用直接 TypeError，被 _build_data_fetcher 的兜底 except 吞掉，
    所有行情工具静默降级为"数据源未配置"——失败被隐藏，比报错更危险。
    """
    from finhack_pro.data.fetcher import DataFetcher

    fetcher = DataFetcher(
        sources=["free_stockdb"],
        free_stockdb_host="10.0.0.8",
        free_stockdb_port=9999,
    )
    # free_stockdb 是 retryable 源，链上被 RetryDataSource 包装，取 inner
    src = fetcher._sources[0].inner
    assert src.name == "free_stockdb"
    assert src.client.host == "10.0.0.8"
    assert src.client.port == 9999
