"""本地量化仓库 / 采集器的回归测试

覆盖三条核心不变量：
  1. PIT 纪律：默认 first-write-wins，历史 bar 不被后续取数静默覆盖
  2. 失败显式化：取数失败与校验拒收必须分开暴露，不得只进日志
  3. 断点续传：已覆盖区间不重复取数（避免无谓触发反爬）
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from finhack_pro.data.collector import CollectReport, MarketDataCollector
from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.data.warehouse import MarketWarehouse

# ============================================================================
# 测试替身与工具
# ============================================================================


def _make_ohlcv(start: str = "2024-01-02", n: int = 60, seed: int = 7, price: float = 10.0):
    """生成确定性、通过 DataValidator 校验的 OHLCV。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, periods=n)
    close = price + np.cumsum(rng.normal(0, 0.1, n))
    close = np.maximum(close, 0.5)  # 防止穿负
    high = close + np.abs(rng.normal(0, 0.05, n))
    low = close - np.abs(rng.normal(0, 0.05, n))
    open_ = (high + low) / 2
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        }
    )


class _StubFetcher:
    """按 symbol 返回预设 DataFrame；未登记的 symbol 走 fail_with。"""

    def __init__(self, data: dict[str, pd.DataFrame], fail_with: BaseException | None = None):
        self.data = data
        self.fail_with = fail_with
        self.calls: list[tuple] = []

    def get_daily(self, symbol, start_date="", end_date=""):
        self.calls.append((symbol, start_date, end_date))
        if self.fail_with is not None:
            raise self.fail_with
        return self._slice(symbol, start_date, end_date)

    def get_minute(self, symbol, start_date="", end_date="", period="5"):
        self.calls.append((symbol, start_date, end_date, period))
        if self.fail_with is not None:
            raise self.fail_with
        return self._slice(symbol, start_date, end_date)

    def _slice(self, symbol, start_date, end_date):
        df = self.data.get(symbol)
        if df is None:
            return pd.DataFrame()
        out = df.copy()
        if start_date:
            out = out[out["date"] >= pd.to_datetime(start_date)]
        if end_date:
            out = out[out["date"] <= pd.to_datetime(end_date)]
        return out.reset_index(drop=True)


# ============================================================================
# 后端探测
# ============================================================================


def test_detect_backend_returns_known_value():
    from finhack_pro.data import warehouse as wh_mod

    assert wh_mod._detect_backend() in ("parquet", "csv")


def test_explicit_parquet_without_engine_raises(monkeypatch, tmp_path):
    """显式指定 parquet 却无引擎时，宁可报错也不静默降级为 CSV。"""
    from finhack_pro.data import warehouse as wh_mod

    monkeypatch.setattr(wh_mod, "_detect_backend", lambda: "csv")
    with pytest.raises(RuntimeError, match="pyarrow"):
        MarketWarehouse(tmp_path / "wh", backend="parquet")


def test_unknown_backend_raises(tmp_path):
    with pytest.raises(ValueError, match="未知的仓库后端"):
        MarketWarehouse(tmp_path / "wh", backend="duckdb")


def test_csv_backend_writes_gzip_csv(tmp_path, monkeypatch):
    """强制 csv 后端：文件应为 .csv.gz（可脱离 pyarrow 独立验证降级路径）。"""
    from finhack_pro.data import warehouse as wh_mod

    monkeypatch.setattr(wh_mod, "_detect_backend", lambda: "csv")
    wh = MarketWarehouse(tmp_path / "wh")
    assert wh.backend == "csv"

    wh.put("600519", _make_ohlcv())
    assert (tmp_path / "wh" / "daily" / "600519.csv.gz").exists()
    assert len(wh.get("600519")) == 60


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("pyarrow"),
    reason="需要 pyarrow（可选依赖 finhack-pro[data]）",
)
def test_parquet_backend_writes_parquet(tmp_path, monkeypatch):
    """强制 parquet 后端：文件应为 .parquet。缺 pyarrow 时跳过而非伪装通过。"""
    from finhack_pro.data import warehouse as wh_mod

    monkeypatch.setattr(wh_mod, "_detect_backend", lambda: "parquet")
    wh = MarketWarehouse(tmp_path / "wh")
    assert wh.backend == "parquet"

    wh.put("600519", _make_ohlcv())
    assert (tmp_path / "wh" / "daily" / "600519.parquet").exists()
    assert len(wh.get("600519")) == 60


# ============================================================================
# 入库 / 读取
# ============================================================================


def test_put_get_roundtrip(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    df = _make_ohlcv()
    res = wh.put("600519", df)

    assert res.ok and not res.rejected
    assert res.rows_in == res.rows_new == len(df)

    got = wh.get("600519")
    assert len(got) == len(df)
    assert list(got.columns)[:6] == ["date", "open", "high", "low", "close", "volume"]
    assert got["date"].is_monotonic_increasing


def test_put_is_idempotent(tmp_path):
    """重复入库同一批数据不应产生重复行。"""
    wh = MarketWarehouse(tmp_path / "wh")
    df = _make_ohlcv()
    wh.put("600519", df)
    res2 = wh.put("600519", df)

    assert res2.rows_new == 0
    assert res2.rows_existing == len(df)
    assert len(wh.get("600519")) == len(df)


def test_first_write_wins_preserves_history(tmp_path):
    """PIT 纪律：默认不覆盖既有历史 bar（复权因子变化不得篡改回测）。"""
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv())
    original = wh.get("600519")["close"].iloc[0]

    tampered = _make_ohlcv(price=999.0)
    res = wh.put("600519", tampered)

    assert res.rows_new == 0
    assert res.rows_existing == len(tampered)
    assert wh.get("600519")["close"].iloc[0] == original


def test_overwrite_flag_replaces_history(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv())
    res = wh.put("600519", _make_ohlcv(price=999.0), overwrite=True)

    assert res.rows_new > 0
    assert wh.get("600519")["close"].iloc[0] == pytest.approx(999.0, abs=1.0)


def test_put_appends_new_dates_only(tmp_path):
    """新区间与已有区间相接：全部为新增行，并集为 60 行。"""
    wh = MarketWarehouse(tmp_path / "wh")
    first = _make_ohlcv(start="2024-01-02", n=30)   # 01-02 ~ 02-12
    second = _make_ohlcv(start="2024-02-13", n=30)  # 02-13 ~ 03-25（与 first 相接不重叠）
    wh.put("600519", first)
    res = wh.put("600519", second)

    assert res.rows_new == 30
    assert res.rows_existing == 0
    assert len(wh.get("600519")) == 60


def test_put_partial_overlap_appends_only_gap(tmp_path):
    """新区间与已有区间部分重叠：只追加真正的新日期。"""
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv(start="2024-01-02", n=30))  # 01-02 ~ 02-12
    res = wh.put("600519", _make_ohlcv(start="2024-02-05", n=30))  # 02-05 ~ 03-15，重叠 6 天

    assert res.rows_existing == 6
    assert res.rows_new == 24
    assert len(wh.get("600519")) == 54


# ============================================================================
# 校验拒收（脏数据不得入库）
# ============================================================================


def test_high_lower_than_low_is_rejected(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    df = _make_ohlcv()
    df.loc[5, "high"] = df.loc[5, "low"] - 1.0  # 制造 high < low

    res = wh.put("600519", df)

    assert res.rejected
    assert res.reject_reasons
    assert not wh.exists("600519")  # 拒收即不落盘


def test_empty_dataframe_is_rejected(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    res = wh.put("600519", pd.DataFrame())
    assert res.rejected
    assert not wh.exists("600519")


def test_missing_date_column_is_rejected(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    df = _make_ohlcv().drop(columns=["date"])
    res = wh.put("600519", df)
    assert res.rejected


def test_get_missing_symbol_returns_empty(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    assert wh.get("nonexistent").empty
    assert not wh.exists("nonexistent")


def test_index_not_written_when_rejected(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    df = _make_ohlcv()
    df.loc[0, "close"] = np.nan
    assert wh.put("600519", df).rejected
    assert wh.symbols() == []


# ============================================================================
# 覆盖度 / 增量范围
# ============================================================================


def test_coverage_and_symbols(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv(start="2024-01-02", n=30))
    wh.put("000001", _make_ohlcv(start="2024-02-01", n=30))

    cov = wh.coverage()
    assert set(cov) == {"600519", "000001"}
    assert cov["600519"]["start"] == "2024-01-02"
    assert wh.symbols() == ["000001", "600519"]


def test_missing_range_none_when_fully_covered(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv(start="2024-01-02", n=60))
    cov = wh.coverage()["600519"]
    assert wh.missing_range("600519", cov["start"], cov["end"]) is None


def test_missing_range_extends_forward(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv(start="2024-01-02", n=30))
    gap = wh.missing_range("600519", "2024-01-02", "2024-06-30")

    assert gap is not None
    lo, hi = gap
    assert lo > wh.coverage()["600519"]["end"]  # 只需向前补
    assert hi == "2024-06-30"


def test_missing_range_full_when_disjoint(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv(start="2024-01-02", n=30))
    assert wh.missing_range("600519", "2020-01-01", "2020-12-31") == (
        "2020-01-01",
        "2020-12-31",
    )


def test_missing_range_whole_range_for_unknown_symbol(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    assert wh.missing_range("999999", "2024-01-01", "2024-12-31") == (
        "2024-01-01",
        "2024-12-31",
    )


def test_reindex_rebuilds_from_disk(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv())
    (tmp_path / "wh" / "daily" / "_index.json").unlink()  # 模拟索引丢失

    rebuilt = wh.reindex()
    assert "600519" in rebuilt
    assert wh.exists("600519")


def test_corrupted_index_warns_and_rebuilds(tmp_path, caplog):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv())
    (tmp_path / "wh" / "daily" / "_index.json").write_text("{ not json", encoding="utf-8")

    cov = wh.coverage()  # 索引损坏应回扫重建，而非返回空导致全量重采
    assert "600519" in cov


def test_holes_detects_missing_days(tmp_path):
    """空洞检测：别人有、我没有的交易日应被报出。"""
    wh = MarketWarehouse(tmp_path / "wh")
    full = _make_ohlcv(start="2024-01-02", n=30)
    wh.put("600519", full)
    wh.put("000001", full)

    partial = full[~full["date"].isin(full["date"].iloc[[3, 4, 5]])]
    wh.put("000002", partial)

    holes = wh.holes("000002", "2024-01-02", "2024-03-01")
    assert len(holes) == 3


def test_drop_removes_data_and_index(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv())
    assert wh.drop("600519")
    assert not wh.exists("600519")
    assert wh.symbols() == []
    assert not wh.drop("600519")  # 重复删除返回 False


def test_stats(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv(start="2024-01-02", n=30))
    st = wh.stats()

    assert st.symbol_count == 1
    assert st.total_rows == 30
    assert st.earliest == "2024-01-02"
    assert st.size_mb >= 0


def test_atomic_write_leaves_no_temp_files(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    wh.put("600519", _make_ohlcv())
    leftovers = [p.name for p in (tmp_path / "wh" / "daily").iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


# ============================================================================
# 采集器：三态结果
# ============================================================================


def test_collector_ingests_all(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    data = {"600519": _make_ohlcv(), "000001": _make_ohlcv(seed=11)}
    col = MarketDataCollector(wh, _StubFetcher(data), max_workers=1, jitter=(0, 0))

    report = col.run(list(data), start="2024-01-01", end="2024-12-31")

    assert report.ok
    assert report.ingested == 2
    assert report.coverage_rate == 1.0
    assert set(wh.symbols()) == set(data)


def test_collector_records_fetch_failure_explicitly(tmp_path):
    """取数失败必须进 report.failed，不得只进日志。"""
    wh = MarketWarehouse(tmp_path / "wh")
    col = MarketDataCollector(
        wh, _StubFetcher({}, fail_with=ConnectionError("远端断开")), max_workers=1, jitter=(0, 0)
    )

    report = col.run(["600519", "000001"], start="2024-01-01", end="2024-12-31")

    assert not report.ok
    assert report.ingested == 0
    assert set(report.failed) == {"600519", "000001"}
    assert "ConnectionError" in report.failed["600519"]


def test_collector_separates_rejected_from_failed(tmp_path):
    """取到但脏：进 rejected，而非 failed —— 两者处置方式不同。"""
    wh = MarketWarehouse(tmp_path / "wh")
    dirty = _make_ohlcv()
    dirty.loc[2, "high"] = dirty.loc[2, "low"] - 1.0
    col = MarketDataCollector(
        wh, _StubFetcher({"600519": dirty}), max_workers=1, jitter=(0, 0)
    )

    report = col.run(["600519"], start="2024-01-01", end="2024-12-31")

    assert not report.ok
    assert report.failed == {}          # 不是网络问题
    assert "600519" in report.rejected  # 是数据质量问题
    assert not wh.exists("600519")


def test_collector_empty_response_counts_as_failure(tmp_path):
    """返回空 DataFrame 与抛异常同为失败（旧实现会静默丢弃）。"""
    wh = MarketWarehouse(tmp_path / "wh")
    col = MarketDataCollector(wh, _StubFetcher({}), max_workers=1, jitter=(0, 0))
    report = col.run(["600519"], start="2024-01-01", end="2024-12-31")

    assert report.failed == {"600519": "数据源返回空数据"}


def test_collector_resume_skips_covered(tmp_path):
    """断点续传：已完整覆盖的标的不应再次取数。"""
    wh = MarketWarehouse(tmp_path / "wh")
    data = {"600519": _make_ohlcv(start="2024-01-02", n=30)}
    fetcher = _StubFetcher(data)
    col = MarketDataCollector(wh, fetcher, max_workers=1, jitter=(0, 0))

    cov = None
    first = col.run(["600519"], start="2024-01-02", end="2024-02-12")
    assert first.ingested == 1
    calls_after_first = len(fetcher.calls)

    second = col.run(["600519"], start="2024-01-02", end="2024-02-12")
    assert second.ingested == 0
    assert second.skipped_covered == 1
    assert len(fetcher.calls) == calls_after_first  # 未重复取数


def test_collector_fetches_only_gap(tmp_path):
    """增量补数：只请求缺失区间，绝不整段重取。"""
    wh = MarketWarehouse(tmp_path / "wh")
    data = {"600519": _make_ohlcv(start="2024-01-02", n=60)}
    fetcher = _StubFetcher(data)
    col = MarketDataCollector(wh, fetcher, max_workers=1, jitter=(0, 0))

    col.run(["600519"], start="2024-01-02", end="2024-02-12")
    fetcher.calls.clear()

    col.run(["600519"], start="2024-01-02", end="2024-04-01")
    assert fetcher.calls
    requested_start = fetcher.calls[0][1]
    assert requested_start > "2024-02-12"  # 起点应在已覆盖区间之后


def test_collector_writes_failure_manifest(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    col = MarketDataCollector(
        wh, _StubFetcher({}, fail_with=ConnectionError("boom")), max_workers=1, jitter=(0, 0)
    )
    report = col.run(["600519"], start="2024-01-01", end="2024-12-31", freq="daily")

    manifest = tmp_path / "wh" / "_failures_daily.json"
    assert manifest.exists()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["failed_fetch"]["600519"]
    assert payload["requested"] == 1


def test_collector_no_manifest_when_clean(tmp_path):
    """没有失败就不留文件，避免"有文件=有问题"的语义被噪声污染。"""
    wh = MarketWarehouse(tmp_path / "wh")
    col = MarketDataCollector(
        wh, _StubFetcher({"600519": _make_ohlcv()}), max_workers=1, jitter=(0, 0)
    )
    col.run(["600519"], start="2024-01-01", end="2024-12-31")
    assert not (tmp_path / "wh" / "_failures_daily.json").exists()


def test_collector_minute_freq_uses_get_minute(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    fetcher = _StubFetcher({"600519": _make_ohlcv()})
    col = MarketDataCollector(wh, fetcher, max_workers=1, jitter=(0, 0))

    report = col.run(["600519"], start="2024-01-01", end="2024-12-31", freq="min60")

    assert report.ok
    assert fetcher.calls[0][3] == "60"  # period 从 freq 解析


def test_collector_limit_for_smoke_runs(tmp_path):
    wh = MarketWarehouse(tmp_path / "wh")
    data = {s: _make_ohlcv(seed=i) for i, s in enumerate(["A", "B", "C"])}
    col = MarketDataCollector(wh, _StubFetcher(data), max_workers=1, jitter=(0, 0))

    report = col.run(["A", "B", "C"], start="2024-01-01", end="2024-12-31", limit=2)
    assert report.requested == 2
    assert report.ingested == 2


def test_report_summary_contains_coverage(tmp_path):
    r = CollectReport(freq="daily", requested=10, ingested=8)
    assert "覆盖率=80.00%" in r.summary()
    assert r.coverage_rate == 0.8


# ============================================================================
# fetcher.batch_download 的失败出参
# ============================================================================


class _FetcherStub(DataFetcher):
    """绕开 __init__ 的网络/缓存初始化，只测 batch_download 的失败收集。"""

    def __init__(self, behaviour: dict[str, str]):  # noqa: D107
        self.behaviour = behaviour

    def get_daily(self, symbol, start_date="", end_date=""):  # noqa: D102
        mode = self.behaviour.get(symbol, "ok")
        if mode == "raise":
            raise ConnectionError("远端断开")
        if mode == "empty":
            return pd.DataFrame()
        return _make_ohlcv()


def test_batch_download_reports_failures_via_outparam():
    f = _FetcherStub({"A": "ok", "B": "raise", "C": "empty"})
    errors: dict[str, str] = {}

    results = f.batch_download(["A", "B", "C"], errors=errors)

    assert set(results) == {"A"}
    assert "ConnectionError" in errors["B"]
    assert errors["C"] == "数据源返回空数据"


def test_batch_download_without_outparam_still_works():
    """向后兼容：不传 errors 时行为与改动前一致。"""
    f = _FetcherStub({"A": "ok", "B": "raise"})
    results = f.batch_download(["A", "B"])
    assert set(results) == {"A"}
