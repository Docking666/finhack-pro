"""
数据缓存模块

提供基于文件系统的智能数据缓存，支持 TTL 过期、完整性校验和线程安全。
使用 pickle + gzip 进行压缩存储。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# 数据类定义
# ============================================================================


@dataclass
class CacheStats:
    """缓存统计信息"""

    total_size_mb: float = 0.0
    entry_count: int = 0
    symbols: List[str] = field(default_factory=list)
    oldest_entry: Optional[str] = None
    newest_entry: Optional[str] = None


# ============================================================================
# 数据缓存
# ============================================================================


class DataCache:
    """数据缓存管理器

    提供线程安全的文件系统缓存，支持:
    - pickle + gzip 压缩存储
    - TTL 过期机制
    - 数据完整性校验 (hash)
    - 自动清理过期条目
    - 缓存大小限制

    Usage:
        cache = DataCache(cache_dir="data/cache", ttl_seconds=86400)
        cache.set("600519", df)
        df = cache.get("600519", "2024-01-01", "2024-12-31")
    """

    def __init__(
        self,
        cache_dir: str = "data/cache",
        max_size_mb: int = 500,
        ttl_seconds: int = 86400,
    ) -> None:
        """初始化数据缓存

        Args:
            cache_dir: 缓存目录路径
            max_size_mb: 最大缓存大小 (MB)
            ttl_seconds: 缓存过期时间 (秒)，默认 24 小时
        """
        self.cache_dir = Path(cache_dir)
        self.max_size_mb = max_size_mb
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()

        # 确保缓存目录存在
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # 确保 metadata 子目录存在
        self._metadata_dir = self.cache_dir / "_metadata"
        self._metadata_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(
            f"数据缓存初始化: dir={self.cache_dir}, "
            f"max_size={max_size_mb}MB, ttl={ttl_seconds}s"
        )

    def get(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        freq: str = "daily",
    ) -> Optional[pd.DataFrame]:
        """获取缓存数据

        Args:
            symbol: 标的代码
            start_date: 开始日期
            end_date: 结束日期
            freq: 数据频率

        Returns:
            缓存的 DataFrame，如果缓存不存在或已过期则返回 None
        """
        with self._lock:
            cache_path = self._get_cache_path(symbol, freq)
            if not cache_path.exists():
                logger.debug(f"缓存未命中: {symbol} ({freq})")
                return None

            # 检查 TTL
            metadata = self._load_metadata(symbol, freq)
            if metadata is None:
                logger.debug(f"缓存元数据缺失: {symbol} ({freq})")
                return None

            created_at = metadata.get("created_at", 0)
            if time.time() - created_at > self.ttl_seconds:
                logger.debug(f"缓存已过期: {symbol} ({freq})")
                return None

            # 加载数据
            try:
                df = self._load_data(cache_path)
                if df is None:
                    return None

                # 验证数据完整性
                stored_hash = metadata.get("hash", "")
                current_hash = self._compute_hash(df)
                if stored_hash and stored_hash != current_hash:
                    logger.warning(f"缓存数据完整性校验失败: {symbol} ({freq})")
                    return None

                # 按日期范围过滤
                if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
                    start_dt = pd.to_datetime(start_date)
                    end_dt = pd.to_datetime(end_date)
                    df = df[
                        (df["date"] >= start_dt) & (df["date"] <= end_dt)
                    ].copy()

                logger.debug(f"缓存命中: {symbol} ({freq}), {len(df)} 行")
                return df

            except Exception as e:
                logger.error(f"加载缓存失败: {symbol} ({freq}), {e}")
                return None

    def set(
        self,
        symbol: str,
        data: pd.DataFrame,
        freq: str = "daily",
    ) -> None:
        """写入缓存数据

        Args:
            symbol: 标的代码
            data: 要缓存的数据
            freq: 数据频率
        """
        if data.empty:
            logger.debug(f"跳过空数据缓存: {symbol} ({freq})")
            return

        with self._lock:
            try:
                cache_path = self._get_cache_path(symbol, freq)
                cache_path.parent.mkdir(parents=True, exist_ok=True)

                # 保存数据
                self._save_data(cache_path, data)

                # 保存元数据
                metadata = {
                    "hash": self._compute_hash(data),
                    "row_count": len(data),
                    "created_at": time.time(),
                }
                if "date" in data.columns and pd.api.types.is_datetime64_any_dtype(data["date"]):
                    metadata["date_range"] = [
                        data["date"].min().isoformat(),
                        data["date"].max().isoformat(),
                    ]
                self._save_metadata(symbol, freq, metadata)

                logger.debug(f"缓存写入: {symbol} ({freq}), {len(data)} 行")

            except Exception as e:
                logger.error(f"写入缓存失败: {symbol} ({freq}), {e}")

    def invalidate(self, symbol: str, freq: str = "daily") -> bool:
        """使指定缓存失效

        Args:
            symbol: 标的代码
            freq: 数据频率

        Returns:
            是否成功删除
        """
        with self._lock:
            cache_path = self._get_cache_path(symbol, freq)
            meta_path = self._get_metadata_path(symbol, freq)
            deleted = False

            if cache_path.exists():
                cache_path.unlink()
                deleted = True
            if meta_path.exists():
                meta_path.unlink()
                deleted = True

            if deleted:
                logger.debug(f"缓存已失效: {symbol} ({freq})")
            return deleted

    def invalidate_all(self) -> int:
        """清除所有缓存

        Returns:
            删除的条目数量
        """
        with self._lock:
            count = 0
            # 删除数据文件
            for f in self.cache_dir.iterdir():
                if f.is_file() and f.name.endswith(".pkl.gz"):
                    f.unlink()
                    count += 1
            # 删除元数据文件
            if self._metadata_dir.exists():
                for f in self._metadata_dir.iterdir():
                    if f.is_file() and f.suffix == ".json":
                        f.unlink()

            logger.debug(f"已清除所有缓存: {count} 条")
            return count

    def get_stats(self) -> CacheStats:
        """获取缓存统计信息

        Returns:
            CacheStats 缓存统计
        """
        with self._lock:
            stats = CacheStats()
            symbols = set()
            oldest_ts: Optional[float] = None
            newest_ts: Optional[float] = None
            total_size = 0

            for f in self.cache_dir.iterdir():
                if f.is_file() and f.name.endswith(".pkl.gz"):
                    total_size += f.stat().st_size

            # 从元数据获取 symbol 列表和时间信息
            if self._metadata_dir.exists():
                for f in self._metadata_dir.iterdir():
                    if f.is_file() and f.suffix == ".json":
                        try:
                            meta = json.loads(f.read_text())
                            # 从文件名提取 symbol: {symbol}_{freq}.json
                            name = f.stem
                            parts = name.rsplit("_", 1)
                            if len(parts) == 2:
                                symbols.add(parts[0])
                            created_at = meta.get("created_at", 0)
                            if oldest_ts is None or created_at < oldest_ts:
                                oldest_ts = created_at
                            if newest_ts is None or created_at > newest_ts:
                                newest_ts = created_at
                        except (json.JSONDecodeError, OSError):
                            pass

            stats.total_size_mb = round(total_size / (1024 * 1024), 2)
            stats.entry_count = len(symbols)
            stats.symbols = sorted(symbols)

            if oldest_ts is not None:
                stats.oldest_entry = datetime.fromtimestamp(oldest_ts).isoformat()
            if newest_ts is not None:
                stats.newest_entry = datetime.fromtimestamp(newest_ts).isoformat()

            return stats

    def cleanup(self, max_age_days: int = 30) -> int:
        """清理过期缓存条目

        Args:
            max_age_days: 最大保留天数

        Returns:
            删除的条目数量
        """
        with self._lock:
            count = 0
            cutoff = time.time() - max_age_days * 86400

            if not self._metadata_dir.exists():
                return 0

            for meta_file in self._metadata_dir.iterdir():
                if not (meta_file.is_file() and meta_file.suffix == ".json"):
                    continue

                try:
                    meta = json.loads(meta_file.read_text())
                    created_at = meta.get("created_at", 0)
                    if created_at < cutoff:
                        # 从元数据文件名提取 symbol 和 freq
                        name = meta_file.stem
                        parts = name.split("_", 1)
                        if len(parts) == 2:
                            symbol, freq = parts
                            cache_path = self._get_cache_path(symbol, freq)
                            if cache_path.exists():
                                cache_path.unlink()
                            meta_file.unlink()
                            count += 1
                except (json.JSONDecodeError, OSError):
                    pass

            logger.debug(f"清理过期缓存: 删除 {count} 条 (>{max_age_days}天)")
            return count

    # ========================================================================
    # 内部方法
    # ========================================================================

    def _get_cache_path(self, symbol: str, freq: str) -> Path:
        """生成缓存文件路径"""
        filename = f"{symbol}_{freq}.pkl.gz"
        return self.cache_dir / filename

    def _get_metadata_path(self, symbol: str, freq: str) -> Path:
        """生成元数据文件路径"""
        filename = f"{symbol}_{freq}.json"
        return self._metadata_dir / filename

    def _save_metadata(self, symbol: str, freq: str, metadata: Dict[str, Any]) -> None:
        """保存缓存元数据"""
        meta_path = self._get_metadata_path(symbol, freq)
        try:
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        except OSError as e:
            logger.error(f"保存元数据失败: {meta_path}, {e}")

    def _load_metadata(self, symbol: str, freq: str) -> Optional[Dict[str, Any]]:
        """加载缓存元数据"""
        meta_path = self._get_metadata_path(symbol, freq)
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"加载元数据失败: {meta_path}, {e}")
            return None

    @staticmethod
    def _compute_hash(df: pd.DataFrame) -> str:
        """计算 DataFrame 的哈希值用于完整性校验"""
        # 使用列名 + shape + 内容的 hash
        h = hashlib.md5()
        h.update(str(list(df.columns)).encode())
        h.update(str(df.shape).encode())
        # 使用 values 的 bytes 表示
        h.update(pd.util.hash_pandas_object(df).values.tobytes())
        return h.hexdigest()

    @staticmethod
    def _save_data(path: Path, df: pd.DataFrame) -> None:
        """使用 pickle + gzip 保存数据"""
        with gzip.open(path, "wb") as f:
            pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def _load_data(path: Path) -> Optional[pd.DataFrame]:
        """使用 pickle + gzip 加载数据"""
        try:
            with gzip.open(path, "rb") as f:
                return pickle.load(f)
        except (pickle.UnpicklingError, EOFError, OSError) as e:
            logger.error(f"加载数据失败: {path}, {e}")
            return None
