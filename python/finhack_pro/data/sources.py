"""
可插拔数据源架构（SDD：真实多源、失败真实回退/显式失败，禁止 mock 兜底）

背景：akshare 的东方财富端点常被远端反爬断开（RemoteDisconnected），
单源依赖脆弱。本模块提供统一的 BaseDataSource 接口与内置适配器，
支持：
  - 内置源：akshare_tx(腾讯) / akshare_em(东方财富) / akshare_sina(新浪)
            / baostock(证券宝，零注册) / tushare(需 token)
  - 配置选择与优先级：data.sources: [akshare_tx, baostock, tushare]
  - 用户自定义源：data.custom_source: "my_module.MyDataSource"（实现 get_daily）

原则（SDD）：
  - get_daily 失败必须抛异常或返回空表，由上层真实回退，绝不伪造数据；
  - 全部源失败时上层显式抛错（流水线标记 failed），不静默返回空。
"""

from __future__ import annotations

import importlib
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

import pandas as pd

logger = logging.getLogger(__name__)

# 标准日线必需列（与 DataFetcher._standardize_columns 一致）
REQUIRED_DAILY_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def to_tx_symbol(symbol: str) -> str:
    """腾讯/新浪证券接口代码格式（带市场前缀）：sh600519 / sz000001 / bj43xxxx"""
    s = symbol.strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        return s
    if s.startswith(("6", "9")):  # 沪市 600/601/603/605/688/689/900
        return f"sh{s}"
    if s.startswith(("4", "8", "92")):  # 北交所 43/83/87/92
        return f"bj{s}"
    return f"sz{s}"  # 深市 000/001/002/003/300/301/399


def to_baostock_symbol(symbol: str) -> str:
    """证券宝代码格式：sh.600519 / sz.000001 / bj.430047"""
    s = symbol.strip().lower().replace(".", "")
    if s.startswith(("sh", "sz", "bj")):
        return f"{s[:2]}.{s[2:]}"
    if s.startswith(("6", "9")):
        return f"sh.{s}"
    if s.startswith(("4", "8", "92")):
        return f"bj.{s}"
    return f"sz.{s}"


def to_tushare_symbol(symbol: str) -> str:
    """tushare 代码格式：600519.SH / 000001.SZ / 430047.BJ"""
    s = symbol.strip().upper()
    if "." in s:
        return s
    if s.startswith(("6", "9")):
        return f"{s}.SH"
    if s.startswith(("4", "8")):
        return f"{s}.BJ"
    return f"{s}.SZ"


class BaseDataSource(ABC):
    """数据源适配器基类。

    get_daily 返回已重命名（英文标准列）的日线 DataFrame；
    失败时抛异常（推荐）或返回空表，由上层按优先级真实回退。
    """

    name: str = "base"

    def __init__(self, adjust: str = "qfq", **params: Any) -> None:
        self.adjust = adjust
        self.params = params

    @abstractmethod
    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取日线行情。返回标准列（date/open/high/low/close/volume...）"""
        raise NotImplementedError


class AkshareTXDataSource(BaseDataSource):
    """akshare 腾讯证券日线端点（默认）。东财被封锁时优先使用。"""

    name = "akshare_tx"

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        import akshare as ak

        df = ak.stock_zh_a_hist_tx(
            symbol=to_tx_symbol(symbol),
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust=self.adjust,
            timeout=15,
        )
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.rename(columns={
            "date": "date", "open": "open", "close": "close",
            "high": "high", "low": "low", "volume": "volume",
            "amount": "amount", "turnover": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)


class AkshareEMDataSource(BaseDataSource):
    """akshare 东方财富日线端点（原默认，当前环境常被反爬断开）。"""

    name = "akshare_em"

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        import akshare as ak

        df = ak.stock_zh_a_hist(
            symbol=symbol.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", ""),
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust=self.adjust,
        )
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "涨跌幅": "change_pct", "换手率": "turnover",
        })
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)


class AkshareSinaDataSource(BaseDataSource):
    """akshare 新浪财经日线端点。"""

    name = "akshare_sina"

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        import akshare as ak

        df = ak.stock_zh_a_daily(
            symbol=to_tx_symbol(symbol),
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust=self.adjust,
        )
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.rename(columns={
            "date": "date", "open": "open", "close": "close",
            "high": "high", "low": "low", "volume": "volume",
            "amount": "amount",
        })
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)


class BaostockDataSource(BaseDataSource):
    """证券宝（Baostock）：零注册、零 token 的独立免费源（不依赖东财/腾讯）。

    注意：baostock 的 volume 单位为「股」，其他源（东财/腾讯/tushare）为「手」，
    如需跨源一致性请自行换算（股 / 100 = 手）。本适配器不做隐式换算。
    """

    name = "baostock"

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        import baostock as bs

        adjustflag = "2" if self.adjust == "qfq" else ("1" if self.adjust == "hfq" else "3")
        lg = bs.login()
        try:
            if lg.error_code != "0":
                raise ValueError(f"Baostock 登录失败: {lg.error_msg}")
            rs = bs.query_history_k_data_plus(
                to_baostock_symbol(symbol),
                "date,code,open,high,low,close,volume,amount,pctChg",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag=adjustflag,
            )
            if rs.error_code != "0":
                raise ValueError(f"Baostock 查询失败: {rs.error_msg}")
            rows: List[Dict[str, str]] = []
            while rs.next():
                data = rs.get_row_data()
                if data:
                    rows.append(dict(zip(rs.fields, data)))
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            for col in ["open", "high", "low", "close", "volume", "amount", "pctChg"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.sort_values("date").reset_index(drop=True)
        finally:
            bs.logout()


class TushareDataSource(BaseDataSource):
    """tushare pro 日线端点（需 token；token 无效/缺失时构造抛错）。"""

    name = "tushare"

    def __init__(self, token: str, adjust: str = "qfq", **params: Any) -> None:
        super().__init__(adjust=adjust, **params)
        if not token:
            raise ValueError("Tushare 数据源需要配置 tushare_token")
        import tushare

        tushare.set_token(token)
        self._pro = tushare.pro_api()

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        df = self._pro.daily(
            ts_code=to_tushare_symbol(symbol),
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        )
        if df is None or len(df) == 0:
            return pd.DataFrame()
        df = df.rename(columns={"trade_date": "date", "vol": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)


class RetryDataSource(BaseDataSource):
    """重试包装器：对内部数据源 get_daily 做有限次重试，缓解网络抖动 /
    反爬瞬时断开（RemoteDisconnected）导致的假失败。重试耗尽仍失败则抛出最后异常，
    由上层依序回退到下一个数据源（SDD：真实失败，禁止伪造）。"""

    name = "retry"  # 占位，构造时改为内部源名

    def __init__(self, inner: BaseDataSource, retries: int = 3, timeout: int = 15) -> None:
        super().__init__(adjust=inner.adjust)
        self.inner = inner
        self.retries = retries
        self.timeout = timeout
        self.name = inner.name  # 对外暴露内部源名，便于链路诊断

    def get_daily(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                df = self.inner.get_daily(symbol, start_date, end_date)
                if df is None or len(df) == 0:
                    last_err = ValueError(f"{self.inner.name}: 返回空数据")
                    logger.info("数据源 %s 第%d次返回空，重试", self.inner.name, attempt)
                    continue
                return df
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("数据源 %s 第%d次失败: %s", self.inner.name, attempt, e)
                continue
        logger.error("数据源 %s 重试 %d 次仍失败", self.inner.name, self.retries)
        raise last_err if last_err else ValueError(f"{self.inner.name}: 未知失败")


# 内置源注册表
SOURCE_REGISTRY: Dict[str, Type[BaseDataSource]] = {
    "akshare_tx": AkshareTXDataSource,
    "akshare_em": AkshareEMDataSource,
    "akshare_sina": AkshareSinaDataSource,
    "baostock": BaostockDataSource,
    "tushare": TushareDataSource,
}


def load_custom_source(spec: str, adjust: str = "qfq") -> BaseDataSource:
    """动态加载用户自定义数据源：spec 形如 "my_module.MyDataSource"。

    自定义类必须继承 BaseDataSource 并实现 get_daily（SDD：真实取数，禁止 mock）。
    """
    if not spec or "." not in spec:
        raise ValueError(
            f"自定义数据源配置无效: {spec!r}。格式应为 'module.ClassName'，"
            f"且该类须继承 BaseDataSource。"
        )
    module_path, _, class_name = spec.rpartition(".")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name, None)
    if cls is None or not isinstance(cls, type) or not issubclass(cls, BaseDataSource):
        raise ValueError(
            f"自定义数据源 {spec!r} 不是有效的 BaseDataSource 子类"
        )
    logger.info("加载自定义数据源: %s", spec)
    return cls(adjust=adjust)


def build_source_chain(
    source: str = "akshare",
    tushare_token: str = "",
    adjust: str = "qfq",
    sources: Optional[List[str]] = None,
    custom_source: str = "",
) -> List[BaseDataSource]:
    """按配置构建数据源链（按优先级排列，失败时依序真实回退）。

    Args:
        source: 兼容旧配置（akshare / tushare），未提供 sources 时生效
        tushare_token: tushare token（提供时才启用 tushare 源）
        adjust: 复权方式
        sources: 显式源优先级列表，如 ["akshare_tx", "baostock", "tushare"]；
                 含 "custom" 时启用自定义源（需同时提供 custom_source）
        custom_source: 用户自定义源，如 "my_module.MyDataSource"
    """
    chain: List[BaseDataSource] = []

    if sources:
        for name in sources:
            name = (name or "").strip().lower()
            if not name:
                continue
            if name == "custom":
                if custom_source:
                    chain.append(load_custom_source(custom_source, adjust=adjust))
                else:
                    logger.warning("配置了 custom 源但未提供 custom_source，跳过")
                continue
            if name not in SOURCE_REGISTRY:
                logger.warning("未知数据源: %s，跳过", name)
                continue
            cls = SOURCE_REGISTRY[name]
            if name == "tushare":
                if tushare_token:
                    chain.append(cls(token=tushare_token, adjust=adjust))
                else:
                    logger.warning("配置了 tushare 源但未提供 tushare_token，跳过")
                continue
            chain.append(cls(adjust=adjust))
    else:
        # 兼容旧配置映射（legacy）
        tushare_ok = bool(tushare_token)
        if source == "tushare":
            if tushare_ok:
                chain.append(TushareDataSource(token=tushare_token, adjust=adjust))
            chain.append(AkshareTXDataSource(adjust=adjust))
            chain.append(AkshareEMDataSource(adjust=adjust))
        else:  # akshare（默认）：腾讯优先（绕开东财封锁），东财次之，tushare 兜底
            chain.append(AkshareTXDataSource(adjust=adjust))
            chain.append(AkshareEMDataSource(adjust=adjust))
            if tushare_ok:
                chain.append(TushareDataSource(token=tushare_token, adjust=adjust))

    if not chain:
        raise ValueError(
            "数据源配置无效：没有可用的数据源。请配置 data.source / data.sources "
            "或 data.custom_source 后重试。"
        )
    # 统一加重试包装（SDD：瞬时失败真实重试，避免单源网络抖动导致全链路失败；
    # 重试耗尽仍失败则抛最后异常，由 fetcher 依序回退到下一个源）
    return [RetryDataSource(s, retries=3, timeout=15) for s in chain]
