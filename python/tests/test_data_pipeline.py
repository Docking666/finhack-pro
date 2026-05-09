"""
Phase 2: Data Pipeline 测试

覆盖数据验证、缓存和版本管理模块的全面测试。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from finhack_pro.data.cache import CacheStats, DataCache
from finhack_pro.data.validator import DataAnomaly, DataQualityReport, DataValidator, ValidationResult
from finhack_pro.data.versioning import DataVersion, DataVersionManager, VersionDiff

# ============================================================================
# 测试数据生成辅助函数
# ============================================================================


def make_valid_ohlcv(n: int = 100, start: str = "2024-01-01") -> pd.DataFrame:
    """生成有效的 OHLCV 测试数据"""
    np.random.seed(42)
    dates = pd.date_range(start, periods=n, freq="B")
    close = np.cumsum(np.random.randn(n) * 0.5) + 100
    close = np.maximum(close, 1.0)  # 确保价格为正

    data = {
        "date": dates,
        "open": close + np.random.randn(n) * 0.3,
        "high": close + abs(np.random.randn(n)) * 1.0,
        "low": close - abs(np.random.randn(n)) * 1.0,
        "close": close,
        "volume": np.random.randint(1000, 100000, n).astype(float),
    }

    df = pd.DataFrame(data)
    # 确保 high >= max(open, close), low <= min(open, close)
    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"] = df[["open", "close", "low"]].min(axis=1)
    df["volume"] = df["volume"].clip(lower=0)

    return df


# ============================================================================
# TestDataValidator
# ============================================================================


class TestDataValidator:
    """数据验证器测试"""

    def test_valid_data(self):
        """测试有效数据通过验证"""
        df = make_valid_ohlcv(50)
        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_invalid_columns(self):
        """测试缺少必需列"""
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=10),
            "price": [100.0] * 10,
        })
        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("缺少必需列" in e for e in result.errors)

    def test_wrong_types(self):
        """测试错误的数据类型"""
        df = pd.DataFrame({
            "date": ["2024-01-01"] * 10,  # 字符串而非 datetime
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.0] * 10,
            "volume": [1000] * 10,
        })
        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("datetime" in e for e in result.errors)

    def test_nan_handling(self):
        """测试 NaN 值检测"""
        df = make_valid_ohlcv(20)
        df.loc[5, "close"] = np.nan
        df.loc[8, "volume"] = np.nan

        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("NaN" in e for e in result.errors)

    def test_high_less_than_low(self):
        """测试 high < low 检测"""
        df = make_valid_ohlcv(20)
        df.loc[3, "high"] = 95.0
        df.loc[3, "low"] = 100.0

        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("high < low" in e for e in result.errors)

    def test_negative_prices(self):
        """测试负价格检测"""
        df = make_valid_ohlcv(20)
        df.loc[2, "close"] = -5.0
        df.loc[7, "open"] = 0.0

        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("<= 0" in e for e in result.errors)

    def test_unsorted_dates(self):
        """测试日期排序检查"""
        df = make_valid_ohlcv(10)
        # 打乱日期顺序
        df = df.iloc[::-1].reset_index(drop=True)

        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("升序" in e for e in result.errors)

    def test_duplicate_dates(self):
        """测试重复日期检测"""
        df = make_valid_ohlcv(10)
        # 复制第一行
        df = pd.concat([df, df.iloc[[0]]], ignore_index=True)

        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("重复" in e for e in result.errors)

    def test_empty_dataframe(self):
        """测试空 DataFrame"""
        df = pd.DataFrame()
        validator = DataValidator()
        result = validator.validate_ohlcv(df)

        assert result.is_valid is False
        assert any("空" in e for e in result.errors)

    def test_batch_validation(self):
        """测试批量验证"""
        data = {
            "SYM_A": make_valid_ohlcv(30),
            "SYM_B": make_valid_ohlcv(30),
        }
        # 使 SYM_B 数据无效
        data["SYM_B"].loc[0, "close"] = np.nan

        validator = DataValidator()
        results = validator.validate_ohlcv_batch(data)

        assert len(results) == 2
        assert results["SYM_A"].is_valid is True
        assert results["SYM_B"].is_valid is False

    def test_clean_nan_fill(self):
        """测试清洗: NaN 前向填充"""
        df = make_valid_ohlcv(20)
        df.loc[5, "close"] = np.nan
        df.loc[5, "volume"] = np.nan

        validator = DataValidator()
        cleaned = validator.clean_ohlcv(df)

        assert cleaned["close"].isna().sum() == 0
        assert cleaned["volume"].isna().sum() == 0

    def test_clean_high_low_swap(self):
        """测试清洗: high < low 交换"""
        df = make_valid_ohlcv(20)
        original_high = df.loc[3, "high"]
        original_low = df.loc[3, "low"]
        df.loc[3, "high"] = original_low
        df.loc[3, "low"] = original_high

        validator = DataValidator()
        cleaned = validator.clean_ohlcv(df)

        assert cleaned.loc[cleaned.index[3], "high"] >= cleaned.loc[cleaned.index[3], "low"]

    def test_clean_remove_negative_prices(self):
        """测试清洗: 移除零/负价格"""
        df = make_valid_ohlcv(20)
        df.loc[2, "close"] = -5.0

        validator = DataValidator()
        cleaned = validator.clean_ohlcv(df)

        assert (cleaned["close"] > 0).all()
        assert len(cleaned) < len(df)

    def test_clean_remove_duplicates(self):
        """测试清洗: 去除重复日期"""
        df = make_valid_ohlcv(10)
        df = pd.concat([df, df.iloc[[0]]], ignore_index=True)

        validator = DataValidator()
        cleaned = validator.clean_ohlcv(df)

        assert cleaned["date"].duplicated().sum() == 0

    def test_clean_sort_by_date(self):
        """测试清洗: 按日期排序"""
        df = make_valid_ohlcv(10)
        df = df.sample(frac=1, random_state=42).reset_index(drop=True)

        validator = DataValidator()
        cleaned = validator.clean_ohlcv(df)

        assert cleaned["date"].is_monotonic_increasing

    def test_anomaly_detection_price_gap(self):
        """测试异常检测: 价格缺口"""
        df = make_valid_ohlcv(30)
        # 制造 > 20% 的价格缺口
        df.loc[15, "close"] = df.loc[14, "close"] * 1.5

        validator = DataValidator()
        anomalies = validator.detect_anomalies(df)

        gap_anomalies = [a for a in anomalies if a.type == "price_gap"]
        assert len(gap_anomalies) > 0
        assert gap_anomalies[0].severity == "high"

    def test_anomaly_detection_volume_spike(self):
        """测试异常检测: 成交量突增"""
        df = make_valid_ohlcv(30)
        # 制造成交量突增
        avg_vol = df["volume"].mean()
        df.loc[20, "volume"] = avg_vol * 20

        validator = DataValidator()
        anomalies = validator.detect_anomalies(df)

        spike_anomalies = [a for a in anomalies if a.type == "volume_spike"]
        assert len(spike_anomalies) > 0
        assert spike_anomalies[0].severity == "medium"

    def test_anomaly_detection_zero_volume(self):
        """测试异常检测: 零成交量"""
        df = make_valid_ohlcv(20)
        df.loc[10, "volume"] = 0

        validator = DataValidator()
        anomalies = validator.detect_anomalies(df)

        zero_vol = [a for a in anomalies if a.type == "zero_volume"]
        assert len(zero_vol) > 0

    def test_anomaly_detection_stale_prices(self):
        """测试异常检测: 停滞价格"""
        df = make_valid_ohlcv(20)
        # 设置连续 5 天相同 OHLC
        ohlc_val = (100.0, 101.0, 99.0, 100.0)
        for i in range(5, 10):
            df.loc[i, "open"] = ohlc_val[0]
            df.loc[i, "high"] = ohlc_val[1]
            df.loc[i, "low"] = ohlc_val[2]
            df.loc[i, "close"] = ohlc_val[3]

        validator = DataValidator()
        anomalies = validator.detect_anomalies(df)

        stale = [a for a in anomalies if a.type == "stale_prices"]
        assert len(stale) > 0
        assert stale[0].severity == "low"

    def test_anomaly_detection_empty_df(self):
        """测试异常检测: 空 DataFrame"""
        df = pd.DataFrame()
        validator = DataValidator()
        anomalies = validator.detect_anomalies(df)
        assert len(anomalies) == 0

    def test_quality_report(self):
        """测试数据质量报告生成"""
        df = make_valid_ohlcv(50)
        report = DataQualityReport()
        report_str = report.generate(df, symbol="TEST")

        assert "TEST" in report_str
        assert "50" in report_str
        assert "通过" in report_str


# ============================================================================
# TestDataCache
# ============================================================================


class TestDataCache:
    """数据缓存测试"""

    def test_set_and_get(self, tmp_path):
        """测试缓存写入和读取"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        df = make_valid_ohlcv(50)

        cache.set("TEST_SYM", df)
        result = cache.get("TEST_SYM", "2024-01-01", "2024-12-31")

        assert result is not None
        assert len(result) == 50

    def test_cache_miss(self, tmp_path):
        """测试缓存未命中"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        result = cache.get("NONEXISTENT", "2024-01-01", "2024-12-31")

        assert result is None

    def test_date_range_filter(self, tmp_path):
        """测试日期范围过滤"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        df = make_valid_ohlcv(100, start="2024-01-01")

        cache.set("TEST_SYM", df)
        result = cache.get("TEST_SYM", "2024-02-01", "2024-02-28")

        assert result is not None
        assert len(result) < 100

    def test_empty_data_not_cached(self, tmp_path):
        """测试空数据不缓存"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        df = pd.DataFrame()

        cache.set("EMPTY_SYM", df)
        result = cache.get("EMPTY_SYM", "2024-01-01", "2024-12-31")

        assert result is None

    def test_invalidate(self, tmp_path):
        """测试缓存失效"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        df = make_valid_ohlcv(30)

        cache.set("TEST_SYM", df)
        assert cache.get("TEST_SYM", "2024-01-01", "2024-12-31") is not None

        cache.invalidate("TEST_SYM")
        assert cache.get("TEST_SYM", "2024-01-01", "2024-12-31") is None

    def test_invalidate_all(self, tmp_path):
        """测试清除所有缓存"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))

        for sym in ["A", "B", "C"]:
            cache.set(sym, make_valid_ohlcv(20))

        count = cache.invalidate_all()
        assert count == 3

        for sym in ["A", "B", "C"]:
            assert cache.get(sym, "2024-01-01", "2024-12-31") is None

    def test_get_stats(self, tmp_path):
        """测试缓存统计"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        cache.set("SYM_A", make_valid_ohlcv(30))
        cache.set("SYM_B", make_valid_ohlcv(50))

        stats = cache.get_stats()

        assert isinstance(stats, CacheStats)
        assert stats.entry_count == 2
        assert "SYM_A" in stats.symbols
        assert "SYM_B" in stats.symbols

    def test_cleanup(self, tmp_path):
        """测试清理过期缓存"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        df = make_valid_ohlcv(20)

        cache.set("OLD_SYM", df)

        # 手动修改元数据使缓存看起来很旧
        meta_path = cache._metadata_dir / "OLD_SYM_daily.json"
        if meta_path.exists():
            import json
            meta = json.loads(meta_path.read_text())
            meta["created_at"] = time.time() - 60 * 86400  # 60 天前
            meta_path.write_text(json.dumps(meta))

        removed = cache.cleanup(max_age_days=30)
        assert removed == 1

    def test_hash_integrity(self, tmp_path):
        """测试数据哈希完整性校验"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        df = make_valid_ohlcv(30)

        cache.set("INTEGRITY_SYM", df)

        # 篡改缓存数据
        cache_path = cache._get_cache_path("INTEGRITY_SYM", "daily")
        if cache_path.exists():
            # 写入不同的数据以破坏完整性
            tampered_df = make_valid_ohlcv(25)
            cache._save_data(cache_path, tampered_df)

        result = cache.get("INTEGRITY_SYM", "2024-01-01", "2024-12-31")
        assert result is None  # 完整性校验失败

    def test_thread_safety(self, tmp_path):
        """测试线程安全"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        errors = []

        def write_cache(sym: str):
            try:
                for _ in range(10):
                    df = make_valid_ohlcv(20)
                    cache.set(sym, df)
                    cache.get(sym, "2024-01-01", "2024-12-31")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=write_cache, args=(f"SYM_{i}",))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_different_freq(self, tmp_path):
        """测试不同频率的缓存隔离"""
        cache = DataCache(cache_dir=str(tmp_path / "cache"))
        df_daily = make_valid_ohlcv(30)

        cache.set("TEST_SYM", df_daily, freq="daily")
        result = cache.get("TEST_SYM", "2024-01-01", "2024-12-31", freq="daily")
        result_min = cache.get("TEST_SYM", "2024-01-01", "2024-12-31", freq="minute")

        assert result is not None
        assert result_min is None


# ============================================================================
# TestDataVersioning
# ============================================================================


class TestDataVersioning:
    """数据版本管理测试"""

    def test_register_version(self, tmp_path):
        """测试注册版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))
        df = make_valid_ohlcv(50)

        version = vm.register_version(df, symbol="600519", source="test")

        assert isinstance(version, DataVersion)
        assert version.symbol == "600519"
        assert version.row_count == 50
        assert version.version_id != ""
        assert len(version.version_id) == 12

    def test_get_version(self, tmp_path):
        """测试获取版本数据"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))
        original_df = make_valid_ohlcv(30)
        version = vm.register_version(original_df, symbol="TEST")

        loaded_df = vm.get_version(version.version_id)

        assert loaded_df is not None
        assert len(loaded_df) == 30
        assert list(loaded_df.columns) == list(original_df.columns)

    def test_get_version_not_found(self, tmp_path):
        """测试获取不存在的版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))
        result = vm.get_version("nonexistent_id")

        assert result is None

    def test_get_latest(self, tmp_path):
        """测试获取最新版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        v1 = vm.register_version(make_valid_ohlcv(20), symbol="TEST", notes="v1")
        v2 = vm.register_version(make_valid_ohlcv(30), symbol="TEST", notes="v2")

        latest = vm.get_latest("TEST")

        assert latest is not None
        version, df = latest
        assert version.version_id == v2.version_id
        assert len(df) == 30

    def test_get_latest_empty(self, tmp_path):
        """测试获取最新版本(无版本)"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))
        result = vm.get_latest("NONEXISTENT")

        assert result is None

    def test_list_versions(self, tmp_path):
        """测试列出版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        for i in range(5):
            vm.register_version(make_valid_ohlcv(10 + i), symbol="TEST", notes=f"v{i}")

        versions = vm.list_versions(symbol="TEST")

        assert len(versions) == 5
        # 按时间降序排列
        assert versions[0].notes == "v4"

    def test_list_versions_with_limit(self, tmp_path):
        """测试列出版本(带限制)"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        for i in range(10):
            vm.register_version(make_valid_ohlcv(10), symbol="TEST")

        versions = vm.list_versions(symbol="TEST", limit=3)

        assert len(versions) == 3

    def test_list_versions_all_symbols(self, tmp_path):
        """测试列出所有标的的版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        vm.register_version(make_valid_ohlcv(10), symbol="A")
        vm.register_version(make_valid_ohlcv(10), symbol="B")

        versions = vm.list_versions()

        assert len(versions) == 2

    def test_compare_versions(self, tmp_path):
        """测试版本比较"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        v1 = vm.register_version(make_valid_ohlcv(30), symbol="TEST")
        v2 = vm.register_version(make_valid_ohlcv(50), symbol="TEST")

        diff = vm.compare_versions(v1.version_id, v2.version_id)

        assert isinstance(diff, VersionDiff)
        assert diff.version1 == v1.version_id
        assert diff.version2 == v2.version_id
        assert diff.row_count_diff == 20  # 50 - 30
        assert diff.hash_match is False

    def test_compare_same_version(self, tmp_path):
        """测试比较相同版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        df = make_valid_ohlcv(30)
        v1 = vm.register_version(df, symbol="TEST")
        v2 = vm.register_version(df, symbol="TEST")

        diff = vm.compare_versions(v1.version_id, v2.version_id)

        assert diff.hash_match is True
        assert diff.row_count_diff == 0

    def test_rollback(self, tmp_path):
        """测试版本回滚"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        original = make_valid_ohlcv(30)
        v1 = vm.register_version(original, symbol="TEST", notes="original")
        vm.register_version(make_valid_ohlcv(50), symbol="TEST", notes="modified")

        rolled_back = vm.rollback("TEST", v1.version_id)

        assert len(rolled_back) == 30
        # 回滚会创建新版本
        versions = vm.list_versions(symbol="TEST")
        assert len(versions) == 3  # original + modified + rollback

    def test_rollback_nonexistent(self, tmp_path):
        """测试回滚不存在的版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        with pytest.raises(ValueError, match="版本不存在"):
            vm.rollback("TEST", "nonexistent")

    def test_prune(self, tmp_path):
        """测试清理旧版本"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        for i in range(15):
            vm.register_version(make_valid_ohlcv(10), symbol="TEST", notes=f"v{i}")

        pruned = vm.prune(keep_count=5)

        assert pruned == 10
        versions = vm.list_versions(symbol="TEST")
        assert len(versions) == 5

    def test_prune_no_versions(self, tmp_path):
        """测试清理(无版本)"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))
        pruned = vm.prune(keep_count=5)
        assert pruned == 0

    def test_version_metadata(self, tmp_path):
        """测试版本元数据完整性"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))
        df = make_valid_ohlcv(50, start="2024-01-01")

        version = vm.register_version(
            df, symbol="TEST", source="akshare", notes="test version"
        )

        assert version.source == "akshare"
        assert version.notes == "test version"
        assert version.start_date == "2024-01-01"
        assert version.freq == "daily"
        assert version.hash != ""

    def test_multiple_symbols(self, tmp_path):
        """测试多标的版本管理"""
        vm = DataVersionManager(versions_dir=str(tmp_path / "versions"))

        vm.register_version(make_valid_ohlcv(20), symbol="A")
        vm.register_version(make_valid_ohlcv(30), symbol="B")

        assert vm.get_latest("A") is not None
        assert vm.get_latest("B") is not None
        assert vm.get_latest("C") is None
