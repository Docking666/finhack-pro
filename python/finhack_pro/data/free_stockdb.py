"""
free-stockdb 本地数据引擎适配器

上游项目：https://github.com/hello245m/free-stockdb（代码 MIT）
本地 C++ 时序引擎，数据落盘 ``./data``，通过 HTTP 查询（默认 127.0.0.1:7899）。

数据契约（来自上游 rd_test.py 与调用示例）
-----------------------------------------
- ``日k:CODE:YYYYMMDD`` -> dict：amount/amplitude/close/code/date/float_mv/
  float_share/high/is_st/low/name/open/pb/pct_chg/pe_ttm/pre_close/
  total_mv/total_share/turnover/vol_ratio/volume
- ``分钟k:CODE:YYYYMMDDHHMMSS`` -> dict：amount/close/code/date/high/low/open/volume
- 范围：``lo<hi``；通配：``*``；``复权:CODE:*`` 为累计复权因子序列
- ``股票代码`` -> {首位数字: [code, ...]}

**复权语义（重要，勿混）**：上游 ``日k`` 表存的是**不复权原始价**，前复权由
SDK 在内存中用 ``复权`` 表现算（qfq = 原价 × f_current / f_latest）。
本适配器默认做同样的 qfq 折算，使输出与项目内其他数据源（默认 qfq）一致；
否则本地仓库会混入两套价格口径，回测直接作废。需要原始价时传 ``adjust=""``。

**风控与 mock 数据风险（必须阅读）**
------------------------------------
上游公告明确：公共无鉴权服务器**仅供测试**，连续批量拉取触发风控后会
返回**随机 mock 数据**（cache_decoy）。这意味着：

1. **禁止从公共服务器做全市场批量导入**。正确路径是用上游的
   ``数据更新.exe`` 把数据同步到本地磁盘，再启动 ``stockdb.exe``，
   本模块只连 **127.0.0.1**（默认即如此，连远端须显式改 host）。
2. 即使本地库也可能已被污染（此前的同步触发过风控）。本适配器内置
   诱饵检测：交叉验证 ``pct_chg`` 与 ``close/pre_close``、检测整段重复
   记录，命中即显式报错而非把脏数据入库。
3. 数据本身的上游许可未在仓库声明：代码 MIT 可放心用，**数据再分发
   需自行确认**。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from finhack_pro.data.sources import BaseDataSource
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7899

#: 日K 记录中会被保留进仓库的字段（核心列之外原样保留）
_DAILY_KEEP = (
    "amount",
    "turnover",
    "pct_chg",
    "pre_close",
    "is_st",
    "pe_ttm",
    "pb",
    "total_mv",
    "float_mv",
    "vol_ratio",
)


def _naked(date: str) -> str:
    """'2024-06-30' -> '20240630'（上游时间键为裸数字）。"""
    return (date or "").replace("-", "")


class FreeStockDBError(RuntimeError):
    """free-stockdb 查询或数据完整性错误"""


class FreeStockDBDecoyError(FreeStockDBError):
    """检测到疑似风控 mock 数据（cache_decoy）。拒绝入库。"""


class FreeStockDBClient:
    """free-stockdb HTTP 客户端（默认只连本机）

    Args:
        host: 引擎地址。**默认 127.0.0.1**；连公共服务器须显式传入，
              且应知晓其 mock 数据风险（见模块文档）
        port: 引擎端口，默认 7899
        timeout: 请求超时秒数
        transport: 可注入的请求函数 ``fn(url) -> dict``，供测试替身使用；
                   缺省用 httpx 真实发请求
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 15.0,
        transport: Optional[Any] = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._transport = transport

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ------------------------------------------------------------------
    # 传输
    # ------------------------------------------------------------------

    def _get_json(self, table_expr: str, cmd: str = "get") -> Any:
        url = f"{self.base_url}/?cmd={cmd}&t={table_expr}"
        if self._transport is not None:
            return self._transport(url)
        import httpx

        resp = httpx.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 元信息
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """引擎是否可达。不可达抛错——由调用方决定降级，这里不静默。"""
        try:
            self._get_json("股票代码")
            return True
        except Exception as e:
            raise FreeStockDBError(
                f"free-stockdb 引擎不可达（{self.base_url}）：{e}。"
                f"请先启动 stockdb.exe，或确认 host/port 配置。"
            ) from e

    def list_symbols(self) -> List[str]:
        """全部标的代码。上游按首位数字分组存储，此处展平。"""
        grouped = self._get_json("股票代码")
        if not isinstance(grouped, dict):
            raise FreeStockDBError(
                f"股票代码接口返回了 {type(grouped).__name__}，预期 dict"
            )
        codes: List[str] = []
        for group in grouped.values():
            if isinstance(group, list):
                codes.extend(str(c) for c in group)
        return sorted(set(codes))

    # ------------------------------------------------------------------
    # 行情
    # ------------------------------------------------------------------

    def _fetch_records(self, table: str, code: str, start: str, end: str) -> List[Dict[str, Any]]:
        lo, hi = _naked(start), _naked(end)
        time_expr = f"{lo}<{hi}" if lo and hi else (hi or lo or "*")
        raw = self._get_json(f"{table}:{code}:{time_expr}")
        return self._parse_records(raw, table_expr=f"{table}:{code}")

    @staticmethod
    def _parse_records(raw: Any, table_expr: str) -> List[Dict[str, Any]]:
        """把引擎响应规整成 list[dict]。

        上游响应形态存在版本差异（单条 dict / list[dict] / {key: record}），
        此处做宽容解析；无法解析时显式报错而非返回空表 ——
        静默空表会被上层当成"该区间无数据"，造成股票池静默缺失。
        """
        if raw is None:
            return []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            if "date" in raw:
                items = [raw]
            else:
                items = list(raw.values())
        else:
            raise FreeStockDBError(
                f"无法解析引擎响应: {type(raw).__name__}（{table_expr}）"
            )
        return [it for it in items if isinstance(it, dict) and it.get("date") is not None]

    def get_daily_raw(self, code: str, start: str, end: str) -> List[Dict[str, Any]]:
        """原始日K（不复权）。"""
        return self._fetch_records("日k", code, start, end)

    def get_adjust_factors(self, code: str) -> Tuple[List[str], List[float]]:
        """累计复权因子序列 (dates, cums)，日期升序。无因子返回 ([], [])。"""
        raw = self._get_json(f"复权:{code}:*")
        records = self._parse_records(raw, table_expr=f"复权:{code}")
        if not records:
            return [], []
        pairs = sorted((str(r["date"])[:8], float(r.get("cum", r.get("factor", 1.0)))) for r in records)
        return [d for d, _ in pairs], [c for _, c in pairs]

    # ------------------------------------------------------------------
    # 诱饵检测（风控 mock 数据）
    # ------------------------------------------------------------------

    @staticmethod
    def check_decoy(records: List[Dict[str, Any]], tolerance: float = 1.0) -> None:
        """检测疑似风控 mock 数据，命中即抛 FreeStockDBDecoyError。

        两类信号（任一命中即拒）：
        1. pct_chg 与 close/pre_close 推算值大面积不一致 —— 随机 mock
           的字段彼此独立生成，交叉校验必然露馅；真实数据最多因四舍五入
           偏差零点几个百分点。
        2. 整段记录完全相同 —— 正常行情不会日日一字。
        """
        if len(records) < 5:
            return

        mismatch = 0
        checked = 0
        for r in records:
            pct, close, pre = r.get("pct_chg"), r.get("close"), r.get("pre_close")
            if pct is None or close is None or pre in (None, 0):
                continue
            checked += 1
            expected = (float(close) / float(pre) - 1.0) * 100.0
            if abs(float(pct) - expected) > tolerance:
                mismatch += 1
        if checked >= 5 and mismatch / checked > 0.2:
            raise FreeStockDBDecoyError(
                f"疑似风控 mock 数据：{mismatch}/{checked} 条记录的 pct_chg 与 "
                f"close/pre_close 推算值偏差超过 {tolerance}%。"
                f"请勿从公共服务器批量拉取，改用本地同步后的数据。"
            )

        first = {k: v for k, v in records[0].items() if k != "date"}
        if all({k: v for k, v in r.items() if k != "date"} == first for r in records[1:]):
            raise FreeStockDBDecoyError(
                "疑似风控 mock 数据：整段记录除日期外完全相同。"
            )

    # ------------------------------------------------------------------
    # 复权
    # ------------------------------------------------------------------

    def apply_qfq(
        self,
        records: List[Dict[str, Any]],
        dates: List[str],
        cums: List[float],
        code: str,
    ) -> List[Dict[str, Any]]:
        """前复权折算，公式与上游 SDK 一致：qfq = 原价 × f_current / f_latest。

        依赖 dates 升序 + bisect 找 <= 交易日 的最近除权日。
        """
        import bisect

        if not dates or not cums:
            return records
        f_latest = cums[-1]
        if f_latest <= 0:
            return records
        decimals = 3 if code.startswith(("1", "5")) else 2

        out: List[Dict[str, Any]] = []
        for r in records:
            d = str(r.get("date", ""))[:8]
            if not d:
                out.append(r)
                continue
            idx = bisect.bisect_right(dates, d) - 1
            f_current = cums[idx] if idx >= 0 else 1.0
            ratio = f_latest / f_current
            if abs(ratio - 1.0) < 1e-6:
                out.append(r)
                continue
            r = dict(r)
            for field in ("open", "high", "low", "close", "pre_close"):
                if r.get(field) is not None:
                    r[field] = round(float(r[field]) / ratio, decimals)
            out.append(r)
        return out

    # ------------------------------------------------------------------
    # 标准化（供 BaseDataSource 契约）
    # ------------------------------------------------------------------

    def get_daily_frame(
        self, code: str, start: str, end: str, adjust: str = "qfq"
    ) -> pd.DataFrame:
        """取日K并标准化为仓库列布局。adjust: qfq / ""（原始）。"""
        records = self.get_daily_raw(code, start, end)
        if not records:
            return pd.DataFrame()

        self.check_decoy(records)

        if adjust == "qfq":
            dates, cums = self.get_adjust_factors(code)
            records = self.apply_qfq(records, dates, cums, code)

        rows: List[Dict[str, Any]] = []
        for r in sorted(records, key=lambda x: str(x["date"])):
            row: Dict[str, Any] = {
                "date": pd.to_datetime(str(r["date"])[:8]),
                "open": r.get("open"),
                "high": r.get("high"),
                "low": r.get("low"),
                "close": r.get("close"),
                "volume": r.get("volume"),
            }
            for key in _DAILY_KEEP:
                if key in r:
                    row[key] = r[key]
            rows.append(row)

        df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        if df["date"].duplicated().any():
            # 上游通配/范围查询偶发重复键；保留最后一条并在日志可见
            logger.warning("free-stockdb 返回重复日期 {}，保留最后一条", code)
            df = df.drop_duplicates(subset="date", keep="last").reset_index(drop=True)
        return df


class FreeStockDBSource(BaseDataSource):
    """适配项目 BaseDataSource 契约的数据源

    仅在引擎本机运行时使用；构造时不主动连网（惰性），
    首次查询失败会抛 FreeStockDBError 由上层回退到下一个源。

    说明：本模块被 sources._register_builtins 延迟导入，此刻 sources
    已加载完毕，故此处模块级导入不构成循环。
    """

    name = "free_stockdb"
    # 本地 HTTP 仍可能瞬时不可用（引擎重启中），保留重试
    retryable = True

    def __init__(self, adjust: str = "qfq", **params: Any) -> None:
        super().__init__(adjust=adjust, **params)
        self.client = FreeStockDBClient(
            host=params.get("free_stockdb_host", DEFAULT_HOST),
            port=params.get("free_stockdb_port", DEFAULT_PORT),
            timeout=float(params.get("free_stockdb_timeout", 15.0)),
        )

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        df = self.client.get_daily_frame(symbol, start_date, end_date, adjust=self.adjust)
        return df if df is not None else pd.DataFrame()


__all__ = [
    "FreeStockDBClient",
    "FreeStockDBSource",
    "FreeStockDBError",
    "FreeStockDBDecoyError",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
]
