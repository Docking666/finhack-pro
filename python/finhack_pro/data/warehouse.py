"""
本地量化数据仓库（MarketWarehouse）

与 ``DataCache`` 的职责边界（**勿混用**）：

    ``DataCache``        短期 TTL 缓存（默认 24h / 500MB 上限 / 30 天清理），
                         可随时淘汰，服务于"在线取数加速"，**不保证可复现**。
    ``MarketWarehouse``  永久事实库，只增不改，服务于"回测可复现 + 全市场扫描"。

为什么必须存在（三条，缺一不可）：

1. **可复现**：TTL 缓存过期后回源，若数据源修正了历史或换了复权因子，
   同一段回测两次跑出不同结果 —— 研究结论不可信且无法归因。
2. **可行性**：5400 只 × 日频在线拉取必然触发反爬（见 ``data/sources.py``
   开头注释：akshare 东财端点常被 RemoteDisconnected 断开）。
   全市场扫描必须读本地，不能依赖在线。
3. **静默偏差**：在线批量取数的失败是**非随机**的（停牌 / ST / 次新更容易失败）。
   吞掉失败会让股票池系统性偏离，且无人察觉 —— 违反 SDD 失败显式化。

写入语义（PIT 纪律）：

    **默认 first-write-wins**：已存在的历史 bar 不被新数据覆盖。
    因为重新拉取的历史可能带不同的复权因子，静默覆盖会篡改既有回测结论。
    需要以新数据为准时，必须显式传 ``overwrite=True``。

存储布局::

    warehouse/
      daily/
        600519.parquet        # 单标的全历史，见下方"为何按 symbol 而非按年分区"
        _index.json           # 覆盖度索引（避免遍历 5400 个文件）
      min60/
        ...

为何按 symbol 单文件、而非按年分区：
    A 股单标的 5 年日线仅约 1200 行。按年分区会产生 5400 × 6 = 3.2 万个小文件，
    元数据与文件句柄开销远大于收益；而主查询模式是"取某标的全历史"，
    按 symbol 单文件对该模式最友好。

后端：
    优先 parquet（需 pyarrow，声明为可选 extra ``data``），缺失时降级为 gzip CSV。
    降级是**显式**的：``backend`` 属性可查，日志会打印，不静默。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

# OHLCV 标准列顺序；amount / turnover 等为可选列，原样保留
CORE_COLUMNS: Tuple[str, ...] = ("date", "open", "high", "low", "close", "volume")


def _detect_backend() -> str:
    """探测 parquet 引擎可用性。

    注意：不可用 ``pd.DataFrame().to_parquet("/dev/null")`` 探测 —— 该路径在
    Windows 上不存在，会抛 OSError 造成**假阴性**；而缺引擎时又抛 ImportError。
    两种异常语义不同却走同一分支，无法区分。改为直接探测引擎模块本身。
    """
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        try:
            import fastparquet  # noqa: F401
        except ImportError:
            return "csv"
    return "parquet"


@dataclass
class IngestResult:
    """单次入库结果"""

    symbol: str
    freq: str
    rows_in: int = 0          # 输入行数
    rows_new: int = 0         # 新增行数
    rows_existing: int = 0    # 已存在而被保留（first-write-wins）的行数
    rows_after: int = 0       # 入库后该标的在仓库中的总行数
    rejected: bool = False    # 校验未通过，整批拒收
    reject_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.rejected


@dataclass
class WarehouseStats:
    """仓库统计"""

    freq: str
    backend: str
    symbol_count: int = 0
    total_rows: int = 0
    earliest: Optional[str] = None
    latest: Optional[str] = None
    size_mb: float = 0.0


class MarketWarehouse:
    """本地量化数据仓库

    Usage:
        >>> wh = MarketWarehouse("data/warehouse")
        >>> wh.put("600519", df)                 # 首次入库
        >>> wh.coverage("daily")["600519"]       # {'start':..., 'end':..., 'rows':...}
        >>> wh.missing_range("600519", "2020-01-01", "2024-12-31")   # 需要补的区间
    """

    def __init__(
        self,
        root: str | Path = "data/warehouse",
        backend: str = "auto",
        validate: bool = True,
    ) -> None:
        """
        Args:
            root: 仓库根目录
            backend: "auto" | "parquet" | "csv"
            validate: 入库前是否经 DataValidator 校验。生产建议保持 True；
                      仅在对已校验数据做纯搬运时关闭。
        """
        self.root = Path(root)
        self.backend = _detect_backend() if backend == "auto" else backend
        if self.backend not in ("parquet", "csv"):
            raise ValueError(f"未知的仓库后端: {backend}（可选 auto/parquet/csv）")
        if self.backend == "parquet" and _detect_backend() != "parquet":
            # 显式指定却不可用：宁可报错，也不静默降级为 CSV
            raise RuntimeError(
                "backend='parquet' 但未找到 pyarrow/fastparquet 引擎。"
                "请安装可选依赖 `pip install finhack-pro[data]`，或改用 backend='auto'/'csv'。"
            )
        self.validate = validate
        self.root.mkdir(parents=True, exist_ok=True)
        logger.info(f"数据仓库初始化: root={self.root}, backend={self.backend}")

    # ------------------------------------------------------------------
    # 路径与索引
    # ------------------------------------------------------------------

    def _freq_dir(self, freq: str) -> Path:
        d = self.root / freq
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _data_path(self, symbol: str, freq: str) -> Path:
        ext = "parquet" if self.backend == "parquet" else "csv.gz"
        return self._freq_dir(freq) / f"{symbol}.{ext}"

    def _index_path(self, freq: str) -> Path:
        return self._freq_dir(freq) / "_index.json"

    def _load_index(self, freq: str) -> Dict[str, Dict[str, Any]]:
        p = self._index_path(freq)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            # 索引损坏不致命（可由 reindex 重建），但必须显式告警：
            # 静默当空索引会导致"以为没数据"而全量重采，白白触发反爬。
            logger.warning(f"覆盖度索引损坏，按空索引处理（可用 reindex() 重建）: {p} -> {e}")
            return {}

    def _save_index(self, freq: str, index: Dict[str, Dict[str, Any]]) -> None:
        p = self._index_path(freq)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    # ------------------------------------------------------------------
    # 读写底层
    # ------------------------------------------------------------------

    def _read_df(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame(columns=list(CORE_COLUMNS))
        if self.backend == "parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, compression="gzip")
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df

    def _write_atomic(self, path: Path, df: pd.DataFrame) -> None:
        """原子写入：先写同目录临时文件再 replace，避免中断留下半截文件。

        中断留下的半截 parquet 会被后续读取当成"数据损坏"，
        且难以与真实的数据质量问题区分——原子写消除这一整类问题。
        """
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        import os

        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            if self.backend == "parquet":
                df.to_parquet(tmp_path, index=False)
            else:
                df.to_csv(tmp_path, index=False, compression="gzip")
            tmp_path.replace(path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 校验与规整
    # ------------------------------------------------------------------

    def _standardize(self, df: pd.DataFrame) -> pd.DataFrame:
        """规整为标准列：日期转 datetime、按日期排序去重、列顺序对齐。"""
        out = df.copy()
        if "date" not in out.columns:
            raise ValueError("入库数据缺少 date 列")
        out["date"] = pd.to_datetime(out["date"])
        out = out.drop_duplicates(subset="date", keep="last").sort_values("date")
        # 核心列在前，其余列原样保留在后
        rest = [c for c in out.columns if c not in CORE_COLUMNS]
        return out[list(CORE_COLUMNS) + rest].reset_index(drop=True)

    def _run_validation(
        self, df: pd.DataFrame, result: IngestResult
    ) -> bool:
        """返回 True 表示通过。校验器本身不可用时视为不阻断（仅告警）。"""
        try:
            from finhack_pro.data.validator import DataValidator

            vr = DataValidator().validate_ohlcv(df)
        except ImportError as e:
            result.warnings.append(f"DataValidator 不可用，跳过校验: {e}")
            return True
        result.warnings.extend(vr.warnings)
        if not vr.is_valid:
            result.rejected = True
            result.reject_reasons.extend(vr.errors)
            return False
        return True

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def put(
        self,
        symbol: str,
        df: pd.DataFrame,
        freq: str = "daily",
        overwrite: bool = False,
    ) -> IngestResult:
        """入库（校验 -> 合并 -> 原子写 -> 更新索引）

        Args:
            symbol: 标的代码（调用前应为标准化形式）
            df: 含 date/open/high/low/close/volume 的 DataFrame
            freq: 频率分区，如 daily / min60
            overwrite: True 时以新数据覆盖同日旧数据。默认 False（PIT first-write-wins）。

        Returns:
            IngestResult。``rejected=True`` 表示校验未通过、整批拒收（不落盘）。
        """
        result = IngestResult(symbol=symbol, freq=freq, rows_in=len(df))

        if df is None or df.empty:
            result.rejected = True
            result.reject_reasons.append("输入数据为空")
            return result

        try:
            incoming = self._standardize(df)
        except (ValueError, KeyError, TypeError) as e:
            result.rejected = True
            result.reject_reasons.append(f"数据规整失败: {e}")
            return result

        if self.validate and not self._run_validation(incoming, result):
            return result

        path = self._data_path(symbol, freq)
        existing = self._read_df(path)

        if existing.empty:
            merged = incoming
            result.rows_new = len(incoming)
        elif overwrite:
            merged = self._standardize(pd.concat([existing, incoming], ignore_index=True))
            result.rows_new = len(incoming)
            result.rows_existing = 0
        else:
            # first-write-wins：以已存在的日期为准，仅追加新日期
            known_dates = set(existing["date"])
            fresh = incoming[~incoming["date"].isin(known_dates)]
            result.rows_new = len(fresh)
            result.rows_existing = len(incoming) - len(fresh)
            if fresh.empty:
                merged = existing
            else:
                merged = self._standardize(
                    pd.concat([existing, fresh], ignore_index=True)
                )

        self._write_atomic(path, merged)
        result.rows_after = len(merged)

        index = self._load_index(freq)
        index[symbol] = {
            "start": merged["date"].min().strftime("%Y-%m-%d"),
            "end": merged["date"].max().strftime("%Y-%m-%d"),
            "rows": len(merged),
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_index(freq, index)
        return result

    def get(
        self,
        symbol: str,
        start: str = "",
        end: str = "",
        freq: str = "daily",
    ) -> pd.DataFrame:
        """读取区间数据。标的不存在时返回**空 DataFrame**——调用方须先用
        ``exists()`` / ``coverage()`` 判断，不要靠"空结果"推断数据缺失。"""
        df = self._read_df(self._data_path(symbol, freq))
        if df.empty:
            return df
        if start:
            df = df[df["date"] >= pd.to_datetime(start)]
        if end:
            # 半开区间 [start, end+1day)：end 当天 00:00 时刻的日线 bar 要保留；
            # 而分钟 bar 的 end 当天（如 09:35）若按 `<= end` 过滤会被整段丢弃。
            df = df[df["date"] < pd.to_datetime(end) + pd.Timedelta(days=1)]
        return df.reset_index(drop=True)

    def exists(self, symbol: str, freq: str = "daily") -> bool:
        return self._data_path(symbol, freq).exists()

    def symbols(self, freq: str = "daily") -> List[str]:
        return sorted(self._load_index(freq).keys())

    def coverage(self, freq: str = "daily") -> Dict[str, Dict[str, Any]]:
        """覆盖度索引。索引缺失时回扫目录重建（而非返回空）。"""
        index = self._load_index(freq)
        if index:
            return index
        return self.reindex(freq)

    def missing_range(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "daily",
    ) -> Optional[Tuple[str, str]]:
        """计算相对请求区间仍缺的日期范围，供采集器增量补数。

        Returns:
            None 表示已完整覆盖；(lo, hi) 表示需要补 [lo, hi]。
            注意：区间内部可能有空洞（如长期停牌），本方法只给出外接范围，
            精确空洞需由 :meth:`holes` 给出。
        """
        cov = self.coverage(freq).get(symbol)
        if cov is None:
            return (start, end)
        req_lo, req_hi = pd.to_datetime(start), pd.to_datetime(end)
        have_lo, have_hi = pd.to_datetime(cov["start"]), pd.to_datetime(cov["end"])
        if req_lo >= have_lo and req_hi <= have_hi:
            return None
        if req_hi < have_lo or req_lo > have_hi:
            return (start, end)
        lo = start if req_lo < have_lo else (have_hi + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        hi = end if req_hi > have_hi else (have_lo - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        return (lo, hi)

    def holes(
        self,
        symbol: str,
        start: str,
        end: str,
        calendar: Optional[List[str]] = None,
        freq: str = "daily",
    ) -> List[str]:
        """区间内的缺失交易日。calendar 为交易日历；未提供时用仓库内
        其他标的日期的并集近似（至少能发现"别人有我没有"的空洞）。"""
        df = self.get(symbol, start, end, freq)
        have = set(df["date"].dt.strftime("%Y-%m-%d")) if not df.empty else set()
        if calendar is None:
            calendar = self._approx_calendar(freq)
        return [d for d in calendar if start <= d <= end and d not in have]

    def _approx_calendar(self, freq: str) -> List[str]:
        """用仓库内标的的日期并集近似交易日历（用于发现缺失日）。"""
        union: set[str] = set()
        for sym in self.symbols(freq)[:50]:  # 采样 50 只，避免全量扫描
            df = self.get(sym, freq=freq)
            if not df.empty:
                union |= set(df["date"].dt.strftime("%Y-%m-%d"))
        return sorted(union)

    def reindex(self, freq: str = "daily") -> Dict[str, Dict[str, Any]]:
        """回扫目录重建覆盖度索引（索引丢失/损坏后的修复手段）。"""
        index: Dict[str, Dict[str, Any]] = {}
        ext = "parquet" if self.backend == "parquet" else "csv.gz"
        for p in sorted(self._freq_dir(freq).glob(f"*.{ext}")):
            symbol = p.name[: -len(ext) - 1]
            try:
                df = self._read_df(p)
            except Exception as e:
                logger.warning(f"重建索引时读取失败，跳过 {p}: {e}")
                continue
            if df.empty:
                continue
            index[symbol] = {
                "start": df["date"].min().strftime("%Y-%m-%d"),
                "end": df["date"].max().strftime("%Y-%m-%d"),
                "rows": len(df),
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
        self._save_index(freq, index)
        logger.info(f"覆盖度索引重建完成: freq={freq}, {len(index)} 个标的")
        return index

    def stats(self, freq: str = "daily") -> WarehouseStats:
        index = self.coverage(freq)
        st = WarehouseStats(freq=freq, backend=self.backend, symbol_count=len(index))
        if not index:
            return st
        st.total_rows = sum(v["rows"] for v in index.values())
        st.earliest = min(v["start"] for v in index.values())
        st.latest = max(v["end"] for v in index.values())
        st.size_mb = round(
            sum(p.stat().st_size for p in self._freq_dir(freq).rglob("*") if p.is_file())
            / 1024
            / 1024,
            2,
        )
        return st

    def drop(self, symbol: str, freq: str = "daily") -> bool:
        """删除标的（用于清理退市/错采数据）。返回是否确有删除。"""
        p = self._data_path(symbol, freq)
        removed = False
        if p.exists():
            p.unlink()
            removed = True
        index = self._load_index(freq)
        if symbol in index:
            index.pop(symbol)
            self._save_index(freq, index)
            removed = True
        return removed


__all__ = ["MarketWarehouse", "IngestResult", "WarehouseStats", "CORE_COLUMNS"]
