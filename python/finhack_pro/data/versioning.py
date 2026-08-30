"""
数据版本管理模块

提供数据版本注册、加载、比较、回滚和清理功能。
使用 parquet 格式存储数据（自动降级为 CSV），JSON 侧车文件存储元数据。
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# 数据类定义
# ============================================================================


@dataclass
class DataVersion:
    """数据版本信息"""

    version_id: str
    symbol: str
    freq: str
    start_date: str
    end_date: str
    row_count: int
    hash: str
    created_at: str
    source: str
    notes: str


@dataclass
class VersionDiff:
    """版本差异比较结果"""

    version1: str
    version2: str
    row_count_diff: int
    date_range_diff: str
    hash_match: bool
    stats_diff: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 数据版本管理器
# ============================================================================


class DataVersionManager:
    """数据版本管理器

    提供数据版本的全生命周期管理:
    - 注册新版本 (自动生成版本 ID)
    - 加载指定版本数据
    - 获取最新版本
    - 列出版本历史
    - 比较两个版本的差异
    - 回滚到指定版本
    - 清理旧版本

    Usage:
        vm = DataVersionManager(versions_dir="data/versions")
        version = vm.register_version(df, symbol="600519", source="akshare")
        df = vm.get_version(version.version_id)
    """

    def __init__(self, versions_dir: str = "data/versions") -> None:
        """初始化版本管理器

        Args:
            versions_dir: 版本存储目录
        """
        self.versions_dir = Path(versions_dir)
        self.versions_dir.mkdir(parents=True, exist_ok=True)

        # 检测 parquet 支持
        # 不可用 `pd.DataFrame().to_parquet("/dev/null")` 探测：该路径在 Windows 上
        # 不存在（OSError）与缺引擎（ImportError）都走同一 except 分支，无法区分，
        # 会在装了 pyarrow 的 Windows 上误判为不可用并静默降级到 CSV。
        # 直接探测引擎模块本身（与 data/warehouse.py::_detect_backend 保持一致）。
        self._parquet_available = False
        try:
            import pyarrow  # noqa: F401

            self._parquet_available = True
        except ImportError:
            try:
                import fastparquet  # noqa: F401

                self._parquet_available = True
            except ImportError:
                pass

        logger.debug(
            f"版本管理器初始化: dir={self.versions_dir}, "
            f"parquet={'可用' if self._parquet_available else '不可用(使用CSV)'}"
        )

    def register_version(
        self,
        df: pd.DataFrame,
        symbol: str,
        source: str = "",
        notes: str = "",
        freq: str = "daily",
    ) -> DataVersion:
        """注册新数据版本

        Args:
            df: 要版本化的数据
            symbol: 标的代码
            source: 数据来源
            notes: 备注说明
            freq: 数据频率

        Returns:
            DataVersion 版本信息
        """
        version_id = self._generate_version_id()
        now = datetime.now()

        # 计算日期范围
        start_date = ""
        end_date = ""
        if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
            start_date = df["date"].min().strftime("%Y-%m-%d")
            end_date = df["date"].max().strftime("%Y-%m-%d")

        # 计算哈希
        data_hash = self._compute_hash(df)

        # 创建版本目录
        version_dir = self.versions_dir / symbol / version_id
        version_dir.mkdir(parents=True, exist_ok=True)

        # 保存数据
        if self._parquet_available:
            data_path = version_dir / "data.parquet"
            try:
                df.to_parquet(data_path, index=False, engine="pyarrow")
            except Exception:
                # pyarrow 不可用，尝试其他引擎
                try:
                    df.to_parquet(data_path, index=False)
                except Exception:
                    # 完全不可用，降级为 CSV
                    self._parquet_available = False
                    data_path = version_dir / "data.csv"
                    df.to_csv(data_path, index=False)
        else:
            data_path = version_dir / "data.csv"
            df.to_csv(data_path, index=False)

        # 保存元数据
        version_info = DataVersion(
            version_id=version_id,
            symbol=symbol,
            freq=freq,
            start_date=start_date,
            end_date=end_date,
            row_count=len(df),
            hash=data_hash,
            created_at=now.isoformat(),
            source=source,
            notes=notes,
        )

        meta_path = version_dir / "metadata.json"
        meta_path.write_text(
            json.dumps(
                {
                    "version_id": version_info.version_id,
                    "symbol": version_info.symbol,
                    "freq": version_info.freq,
                    "start_date": version_info.start_date,
                    "end_date": version_info.end_date,
                    "row_count": version_info.row_count,
                    "hash": version_info.hash,
                    "created_at": version_info.created_at,
                    "source": version_info.source,
                    "notes": version_info.notes,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        logger.info(
            f"注册版本: {symbol}/{version_id}, "
            f"{len(df)} 行, {start_date} ~ {end_date}"
        )
        return version_info

    def get_version(self, version_id: str) -> Optional[pd.DataFrame]:
        """加载指定版本的数据

        Args:
            version_id: 版本 ID

        Returns:
            对应版本的 DataFrame，如果不存在则返回 None
        """
        # 在所有 symbol 目录下搜索
        for symbol_dir in self.versions_dir.iterdir():
            if not symbol_dir.is_dir():
                continue
            version_dir = symbol_dir / version_id
            if version_dir.exists():
                return self._load_version_data(version_dir)

        logger.debug(f"版本未找到: {version_id}")
        return None

    def get_latest(
        self, symbol: str, freq: str = "daily"
    ) -> Optional[Tuple[DataVersion, pd.DataFrame]]:
        """获取指定标的的最新版本

        Args:
            symbol: 标的代码
            freq: 数据频率

        Returns:
            (DataVersion, DataFrame) 元组，如果没有版本则返回 None
        """
        versions = self.list_versions(symbol=symbol, limit=1)
        if not versions:
            return None

        latest = versions[0]
        df = self.get_version(latest.version_id)
        if df is None:
            return None

        return latest, df

    def list_versions(
        self,
        symbol: Optional[str] = None,
        limit: int = 20,
    ) -> List[DataVersion]:
        """列出版本历史

        Args:
            symbol: 标的代码，为 None 时列出所有标的
            limit: 返回的最大版本数

        Returns:
            DataVersion 列表，按创建时间降序排列
        """
        all_versions: List[DataVersion] = []

        if symbol:
            search_dirs = [self.versions_dir / symbol]
        else:
            search_dirs = [
                d for d in self.versions_dir.iterdir() if d.is_dir()
            ]

        for symbol_dir in search_dirs:
            if not symbol_dir.exists() or not symbol_dir.is_dir():
                continue

            for version_dir in symbol_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                meta_path = version_dir / "metadata.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                        all_versions.append(DataVersion(**meta))
                    except (json.JSONDecodeError, TypeError, OSError):
                        pass

        # 按创建时间降序排列
        all_versions.sort(key=lambda v: v.created_at, reverse=True)
        return all_versions[:limit]

    def compare_versions(self, v1_id: str, v2_id: str) -> VersionDiff:
        """比较两个版本的差异

        Args:
            v1_id: 版本 1 ID
            v2_id: 版本 2 ID

        Returns:
            VersionDiff 差异比较结果
        """
        df1 = self.get_version(v1_id)
        df2 = self.get_version(v2_id)

        row_count_diff = 0
        date_range_diff = ""
        hash_match = False
        stats_diff: Dict[str, Any] = {}

        if df1 is not None and df2 is not None:
            row_count_diff = len(df2) - len(df1)

            # 日期范围差异
            if "date" in df1.columns and "date" in df2.columns:
                if pd.api.types.is_datetime64_any_dtype(df1["date"]) and pd.api.types.is_datetime64_any_dtype(df2["date"]):
                    range1 = f"{df1['date'].min().strftime('%Y-%m-%d')} ~ {df1['date'].max().strftime('%Y-%m-%d')}"
                    range2 = f"{df2['date'].min().strftime('%Y-%m-%d')} ~ {df2['date'].max().strftime('%Y-%m-%d')}"
                    date_range_diff = f"{range1} -> {range2}"

            # 哈希比较
            hash1 = self._compute_hash(df1)
            hash2 = self._compute_hash(df2)
            hash_match = hash1 == hash2

            # 统计差异
            numeric_cols = [
                c for c in df1.columns
                if c in df2.columns and pd.api.types.is_numeric_dtype(df1[c])
            ]
            for col in numeric_cols[:5]:  # 限制比较列数
                stats_diff[col] = {
                    "v1_mean": round(float(df1[col].mean()), 4),
                    "v2_mean": round(float(df2[col].mean()), 4),
                    "v1_std": round(float(df1[col].std()), 4),
                    "v2_std": round(float(df2[col].std()), 4),
                }
        elif df1 is None and df2 is None:
            date_range_diff = "两个版本均未找到"
        elif df1 is None:
            date_range_diff = "版本 1 未找到"
        else:
            date_range_diff = "版本 2 未找到"

        return VersionDiff(
            version1=v1_id,
            version2=v2_id,
            row_count_diff=row_count_diff,
            date_range_diff=date_range_diff,
            hash_match=hash_match,
            stats_diff=stats_diff,
        )

    def rollback(self, symbol: str, version_id: str) -> pd.DataFrame:
        """回滚到指定版本

        Args:
            symbol: 标的代码
            version_id: 目标版本 ID

        Returns:
            目标版本的 DataFrame

        Raises:
            ValueError: 如果版本不存在
        """
        version_dir = self.versions_dir / symbol / version_id
        if not version_dir.exists():
            raise ValueError(f"版本不存在: {symbol}/{version_id}")

        df = self._load_version_data(version_dir)
        if df is None:
            raise ValueError(f"无法加载版本数据: {symbol}/{version_id}")

        # 注册为新的版本 (回滚版本)
        self.register_version(
            df,
            symbol=symbol,
            source=f"rollback from {version_id}",
            notes=f"回滚到版本 {version_id}",
        )

        logger.info(f"已回滚 {symbol} 到版本 {version_id}")
        return df

    def prune(self, keep_count: int = 10) -> int:
        """清理旧版本，保留每个标的最近的 N 个版本

        Args:
            keep_count: 每个标的保留的版本数

        Returns:
            删除的版本数量
        """
        total_deleted = 0

        for symbol_dir in self.versions_dir.iterdir():
            if not symbol_dir.is_dir():
                continue

            # 获取该标的所有版本，按时间降序
            versions = []
            for version_dir in symbol_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                meta_path = version_dir / "metadata.json"
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                        versions.append((meta.get("created_at", ""), version_dir))
                    except (json.JSONDecodeError, OSError):
                        pass

            # 按时间降序排列
            versions.sort(key=lambda x: x[0], reverse=True)

            # 删除超出保留数量的旧版本
            for _, version_dir in versions[keep_count:]:
                shutil.rmtree(version_dir, ignore_errors=True)
                total_deleted += 1

        if total_deleted > 0:
            logger.info(f"已清理 {total_deleted} 个旧版本 (保留最近 {keep_count} 个)")
        return total_deleted

    # ========================================================================
    # 内部方法
    # ========================================================================

    def _load_version_data(self, version_dir: Path) -> Optional[pd.DataFrame]:
        """从版本目录加载数据"""
        # 优先尝试 parquet
        parquet_path = version_dir / "data.parquet"
        if parquet_path.exists():
            try:
                return pd.read_parquet(parquet_path)
            except Exception:
                pass

        # 降级到 CSV
        csv_path = version_dir / "data.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                return df
            except Exception as e:
                logger.error(f"加载 CSV 失败: {csv_path}, {e}")

        return None

    @staticmethod
    def _generate_version_id() -> str:
        """生成 UUID 格式的版本 ID"""
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _compute_hash(df: pd.DataFrame) -> str:
        """计算 DataFrame 哈希值"""
        import hashlib

        h = hashlib.md5()
        h.update(str(list(df.columns)).encode())
        h.update(str(df.shape).encode())
        h.update(pd.util.hash_pandas_object(df).values.tobytes())
        return h.hexdigest()
