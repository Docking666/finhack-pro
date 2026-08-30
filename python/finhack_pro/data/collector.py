"""
全市场增量补数采集器（MarketDataCollector）

与 ``DataFetcher.batch_download_async`` 的边界：

    ``batch_download_async``  一次性内存并发下载，返回 {symbol: DataFrame}。
                              失败时仅 logger.error 后**吞掉**，调用方无从得知
                              哪几只失败；无断点续传，跑到 80% 中断即全丢。
    ``MarketDataCollector``   面向"建库"：落盘到 MarketWarehouse，
                              **显式区分两类失败**、支持断点续传、限流抖动。

为什么失败必须显式分类：

    在线取数的失败是**非随机**的 —— 停牌、ST、次新、退市标的更容易失败。
    若只记日志然后返回部分结果，得到的股票池会系统性剔除这类标的，
    而回测时"恰好没有 ST 股"会让收益虚高。这是不可复现且不可察觉的偏差。

    因此本采集器把结果分成三态，调用方必须处理：
      - ``ingested``  成功入库
      - ``failed``    取数失败（网络 / 数据源）—— 需重跑或换源
      - ``rejected``  取到但校验拒收（脏数据）—— 需人工看数据质量

Usage:
    >>> wh = MarketWarehouse("data/warehouse")
    >>> col = MarketDataCollector(wh, DataFetcher())
    >>> report = col.run(symbols, start="2020-01-01", end="2024-12-31")
    >>> if not report.ok:
    ...     logger.error(report.summary())
"""

from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CollectReport:
    """采集结果（三态，禁止只留"成功"）"""

    freq: str
    requested: int = 0
    ingested: int = 0
    skipped_covered: int = 0      # 已覆盖，跳过（断点续传生效）
    failed: Dict[str, str] = field(default_factory=dict)      # symbol -> 取数失败原因
    rejected: Dict[str, List[str]] = field(default_factory=dict)  # symbol -> 校验错误列表
    rows_new: int = 0
    elapsed_sec: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed and not self.rejected

    @property
    def coverage_rate(self) -> float:
        """成功入库占请求数的比例。全市场扫描的**核心健康指标**。"""
        if self.requested == 0:
            return 0.0
        return round(self.ingested / self.requested, 4)

    def summary(self) -> str:
        return (
            f"采集完成: freq={self.freq}, 请求={self.requested}, "
            f"入库={self.ingested}, 跳过(已覆盖)={self.skipped_covered}, "
            f"取数失败={len(self.failed)}, 校验拒收={len(self.rejected)}, "
            f"新增行={self.rows_new}, 覆盖率={self.coverage_rate:.2%}, "
            f"耗时={self.elapsed_sec:.1f}s"
        )


class MarketDataCollector:
    """全市场增量补数采集器

    Args:
        warehouse: MarketWarehouse 实例
        fetcher: DataFetcher 实例（提供 get_daily / get_minute）
        max_workers: 并发数。默认 4 —— 再高易触发数据源反爬
                     （见 data/sources.py 关于东财端点 RemoteDisconnected 的说明）
        jitter: 每次请求前的随机抖动区间（秒），打散请求节奏
    """

    def __init__(
        self,
        warehouse: Any,
        fetcher: Any,
        max_workers: int = 4,
        jitter: Tuple[float, float] = (0.05, 0.25),
    ) -> None:
        self.warehouse = warehouse
        self.fetcher = fetcher
        self.max_workers = max(1, int(max_workers))
        self.jitter = jitter

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(
        self,
        symbols: Sequence[str],
        start: str = "2020-01-01",
        end: str = "",
        freq: str = "daily",
        resume: bool = True,
        limit: int = 0,
    ) -> CollectReport:
        """批量采集入库

        Args:
            symbols: 标的代码列表
            start/end: 请求的日期区间
            freq: daily / min60 等（min* 走 get_minute，period 取数字部分）
            resume: True 时跳过仓库中已完整覆盖该区间的标的（断点续传）
            limit: >0 时只处理前 N 个（冒烟测试用）

        Returns:
            CollectReport。调用方应检查 ``report.ok``。
        """
        started = time.time()
        if not end:
            end = datetime.now().strftime("%Y-%m-%d")

        todo: List[str] = list(symbols)
        if limit and limit > 0:
            todo = todo[:limit]

        report = CollectReport(freq=freq, requested=len(todo))

        plan: List[Tuple[str, Optional[Tuple[str, str]]]] = []
        for sym in todo:
            if resume:
                gap = self.warehouse.missing_range(sym, start, end, freq)
                if gap is None:
                    report.skipped_covered += 1
                    continue
                plan.append((sym, gap))
            else:
                plan.append((sym, (start, end)))

        logger.info(
            f"采集计划: 请求={report.requested}, 待取={len(plan)}, "
            f"跳过(已覆盖)={report.skipped_covered}, freq={freq}"
        )

        if plan:
            self._run_pool(plan, freq, report)

        report.elapsed_sec = round(time.time() - started, 2)
        logger.info(report.summary())
        self._write_failures(report, start, end)
        return report

    def _run_pool(
        self,
        plan: List[Tuple[str, Optional[Tuple[str, str]]]],
        freq: str,
        report: CollectReport,
    ) -> None:
        workers = min(self.max_workers, len(plan))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._collect_one, s, gap, freq): s for s, gap in plan}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    new_rows, err, rejects = fut.result()
                except Exception as e:  # 兜底：线程内未捕获的异常不得吞掉
                    report.failed[sym] = f"未捕获异常: {type(e).__name__}: {e}"
                    continue
                if err is not None:
                    report.failed[sym] = err
                elif rejects:
                    report.rejected[sym] = rejects
                else:
                    report.ingested += 1
                    report.rows_new += new_rows

    # ------------------------------------------------------------------
    # 单标的
    # ------------------------------------------------------------------

    def _collect_one(
        self,
        symbol: str,
        gap: Optional[Tuple[str, str]],
        freq: str,
    ) -> Tuple[int, Optional[str], List[str]]:
        """返回 (新增行数, 取数失败原因, 校验拒收原因列表)。

        后两者语义不同、处置方式也不同，必须分开返回：
          取数失败 -> 重跑 / 换数据源
          校验拒收 -> 人工看数据质量（重跑只会重复得到同样的脏数据）
        """
        if gap is None:
            return 0, None, []
        lo, hi = gap

        time.sleep(random.uniform(*self.jitter))

        try:
            df = self._fetch(symbol, lo, hi, freq)
        except Exception as e:
            # 取数失败：显式回报原因，绝不吞掉（停牌/ST/次新的失败是非随机的）
            return 0, f"{type(e).__name__}: {e}", []

        if df is None or df.empty:
            return 0, "数据源返回空数据", []

        try:
            result = self.warehouse.put(symbol, df, freq=freq)
        except Exception as e:
            return 0, f"入库异常: {type(e).__name__}: {e}", []

        if result.rejected:
            # 取到了但脏：与"取不到"区分开，需人工介入
            return 0, None, list(result.reject_reasons)
        return result.rows_new, None, []

    def _fetch(self, symbol: str, lo: str, hi: str, freq: str) -> pd.DataFrame:
        if freq.startswith("min"):
            period = freq[3:] or "5"
            return self.fetcher.get_minute(symbol, start_date=lo, end_date=hi, period=period)
        return self.fetcher.get_daily(symbol, start_date=lo, end_date=hi)

    # ------------------------------------------------------------------
    # 失败留痕
    # ------------------------------------------------------------------

    def _write_failures(self, report: CollectReport, start: str, end: str) -> None:
        """把失败清单落盘，供人工排查与下次重跑。

        只写失败项：**没有失败就不留文件**，避免"有文件=有问题"的语义被噪声污染。
        """
        if report.ok:
            return
        root = Path(self.warehouse.root)
        p = root / f"_failures_{report.freq}.json"
        payload = {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "range": {"start": start, "end": end},
            "requested": report.requested,
            "ingested": report.ingested,
            "failed_fetch": report.failed,
            "rejected_validation": report.rejected,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.warning(f"本次采集存在失败项，清单已写入: {p}")


__all__ = ["MarketDataCollector", "CollectReport"]
