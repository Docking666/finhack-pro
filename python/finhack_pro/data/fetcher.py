"""
数据获取模块

支持tushare和akshare两个数据源，自动fallback。
提供日线、分钟线、实时行情的获取接口，支持批量下载。
统一使用 DataCache（pickle+gzip、TTL、MD5校验、按日期过滤），
取代旧的"日期入文件名"CSV 缓存（避免两套缓存并存）。
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class DataFetcher:
    """数据获取器

    支持tushare和akshare两个数据源，自动fallback。
    数据通过 DataCache 统一缓存（TTL + MD5 校验）。

    Usage:
        fetcher = DataFetcher(source="akshare", cache_dir="data/cache")
        df = fetcher.get_daily("600519", "2023-01-01", "2024-01-01")
        realtime = fetcher.get_realtime("600519")
    """

    def __init__(
        self,
        source: str = "akshare",
        tushare_token: str = "",
        cache_dir: str = "data/cache",
        adjust: str = "qfq",
        akshare_hist_api: str = "tx",
        sources: Optional[List[str]] = None,
        custom_source: str = "",
        warehouse_dir: str = "",
        warehouse_backend: str = "auto",
    ) -> None:
        """初始化数据获取器

        Args:
            source: 数据源 (akshare / tushare)；未提供 sources 时按 legacy 规则映射多源链
            tushare_token: tushare API token
            cache_dir: 缓存目录
            adjust: 复权方式 (qfq 前复权 / hfq 后复权 / "" 不复权)
            akshare_hist_api: akshare 日线取数端点 (tx=腾讯证券 / em=东方财富)。
                东财接口常被远端反爬断开（RemoteDisconnected），故默认 tx（腾讯）以绕开封锁。
            sources: 显式数据源优先级列表，如 ["warehouse", "akshare_tx", "baostock"]；
                含 "custom" 时启用自定义源（需配合 custom_source）。None 时用 legacy source 映射。
                名称解析走数据源注册中心，第三方插件注册后即可直接写在这里。
            custom_source: 用户自定义数据源，如 "my_module.MyDataSource"（须继承 BaseDataSource）。
            warehouse_dir: 本地量化仓库根目录（sources 含 "warehouse" 时必需）
            warehouse_backend: 仓库后端 auto / parquet / csv
        """
        self.source = source
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.adjust = adjust
        self.akshare_hist_api = akshare_hist_api or "tx"

        # 统一缓存：DataCache（pickle+gzip，TTL+MD5 校验，按日期过滤）
        from finhack_pro.data.cache import DataCache
        self._cache = DataCache(cache_dir=str(self.cache_dir))

        # 全市场快照缓存锁（get_realtime 并发保护）
        self._snapshot_lock = threading.Lock()

        # 可插拔数据源链（真实多源、依序回退；SDD：失败显式化，禁止 mock 兜底）
        from finhack_pro.data.sources import build_source_chain

        self._sources = build_source_chain(
            source=source,
            tushare_token=tushare_token,
            adjust=adjust,
            sources=sources,
            custom_source=custom_source,
            warehouse_dir=warehouse_dir,
            warehouse_backend=warehouse_backend,
        )

        # 兼容属性（供既有调用方/测试使用）
        self._akshare_available = any(
            s.name.startswith("akshare") for s in self._sources
        )
        self._tushare_available = any(s.name == "tushare" for s in self._sources)
        self._tushare_pro: Optional[Any] = None
        for s in self._sources:
            if s.name == "tushare":
                self._tushare_pro = getattr(s, "_pro", None)

        logger.info(
            f"数据获取器初始化: source={source}, adjust={adjust}, "
            f"源链={[s.name for s in self._sources]}, "
            f"akshare={'可用' if self._akshare_available else '不可用'}, "
            f"tushare={'可用' if self._tushare_available else '不可用'}"
        )

    def get_daily(
        self,
        symbol: str,
        start_date: str = "2020-01-01",
        end_date: str = "",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取日线数据

        Args:
            symbol: 标的代码(如 600519, 000001)
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)，默认为今天
            use_cache: 是否使用缓存

        Returns:
            包含OHLCV数据的DataFrame
        """
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        # 标准化标的代码
        std_symbol = self._standardize_symbol(symbol)

        # 统一缓存：DataCache 按 symbol+freq 缓存全量数据，get 时按日期过滤。
        # 缓存命中且过滤后有数据才算命中；过滤后为空（如缓存数据不含请求日期范围）
        # 视为未命中，必须回源取数——否则会拿"空行情"跑流水线（错误失败，非假数据）。
        if use_cache:
            cached = self._cache.get(std_symbol, start_date, end_date, freq="daily")
            if cached is not None and len(cached) > 0:
                logger.debug(f"缓存命中: {std_symbol} ({start_date}~{end_date})")
                return cached

        # 获取数据：依序尝试数据源链，失败（异常或空表）真实回退，全部失败显式抛错（SDD/L5a）
        errors: List[str] = []
        for src in self._sources:
            try:
                df = src.get_daily(std_symbol, start_date, end_date)
                if df is None or df.empty:
                    errors.append(f"{src.name}: 返回空数据")
                    logger.info(f"数据源 {src.name} 返回空数据，尝试下一个...")
                    continue
                # 标准化列名（缺失必要列抛 ValueError，不伪造零值；L5b）
                df = self._standardize_columns(df)
            except Exception as e:
                errors.append(f"{src.name}: {e}")
                logger.warning(f"数据源 {src.name} 获取失败: {e}")
                continue

            # 成功：写入缓存并返回
            self._cache.set(std_symbol, df, freq="daily")
            logger.info(f"数据获取成功: {std_symbol}, {len(df)}条记录 (源={src.name})")
            # 按日期范围过滤返回
            if "date" in df.columns:
                df = df[
                    (df["date"] >= pd.to_datetime(start_date))
                    & (df["date"] <= pd.to_datetime(end_date))
                ].reset_index(drop=True)
            return df

        # 失败显式化（L5a）：所有数据源均失败，绝不静默返回空 DF（SDD：禁止伪造完成结果）
        raise ValueError(
            f"数据源获取失败：{symbol} ({start_date}~{end_date}) 的所有数据源均未能返回有效行情。"
            f"尝试源: {[s.name for s in self._sources]}；详情: {'；'.join(errors)}"
        )

    def get_minute(
        self,
        symbol: str,
        start_date: str = "",
        end_date: str = "",
        period: str = "5",
    ) -> pd.DataFrame:
        """获取分钟线数据

        Args:
            symbol: 标的代码
            start_date: 开始日期
            end_date: 结束日期
            period: K线周期 (1/5/15/30/60)

        Returns:
            分钟线DataFrame
        """
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        std_symbol = self._standardize_symbol(symbol)

        if self._akshare_available:
            try:
                import akshare as ak
                ak_symbol = self._to_akshare_symbol(std_symbol)
                df = ak.stock_zh_a_hist_min_em(
                    symbol=ak_symbol,
                    period=period,
                    start_date=start_date.replace("-", " "),
                    end_date=end_date.replace("-", " "),
                    adjust=self.adjust,
                )
                if df is not None and not df.empty:
                    df = df.rename(columns={
                        "时间": "datetime",
                        "开盘": "open",
                        "收盘": "close",
                        "最高": "high",
                        "最低": "low",
                        "成交量": "volume",
                        "成交额": "amount",
                    })
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df = df.sort_values("datetime").reset_index(drop=True)
                # 勿用 `df or pd.DataFrame()`（多行 DataFrame bool 求值抛 ValueError 被误吞）
                return df if (df is not None and len(df) > 0) else pd.DataFrame()
            except Exception as e:
                logger.error(f"AkShare获取分钟线失败: {e}")

        return pd.DataFrame()

    def get_realtime(self, symbol: str) -> Dict[str, Any]:
        """获取实时行情

        东财全市场快照接口（stock_zh_a_spot_em）单次返回全市场 5000+ 行，
        逐只调用浪费严重。这里对全市场快照做 15 秒 TTL 缓存：
        同一窗口内多次查询只拉取一次快照。

        Args:
            symbol: 标的代码

        Returns:
            实时行情字典
        """
        std_symbol = self._standardize_symbol(symbol)

        if not self._akshare_available:
            return {}

        df = self._get_market_snapshot()
        if df is None or df.empty:
            return {}

        try:
            ak_symbol = self._to_akshare_symbol(std_symbol)
            row = df[df["代码"] == ak_symbol]
            if not row.empty:
                row = row.iloc[0]
                return {
                    "symbol": std_symbol,
                    "name": row.get("名称", ""),
                    "price": float(row.get("最新价", 0)),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "volume": float(row.get("成交量", 0)),
                    "amount": float(row.get("成交额", 0)),
                    "high": float(row.get("最高", 0)),
                    "low": float(row.get("最低", 0)),
                    "open": float(row.get("今开", 0)),
                    "pre_close": float(row.get("昨收", 0)),
                }
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")

        return {}

    def _get_market_snapshot(self) -> Optional[pd.DataFrame]:
        """获取全市场快照（带 15 秒 TTL 缓存）"""
        cache_path = self.cache_dir / "_realtime_snapshot.pkl.gz"
        ttl = 15.0  # 秒

        # 命中缓存
        try:
            if cache_path.exists():
                import gzip
                import pickle
                age = time.time() - cache_path.stat().st_mtime
                if age < ttl:
                    with gzip.open(cache_path, "rb") as f:
                        return pickle.load(f)
        except Exception:
            pass

        # 拉取快照（带锁，避免并发重复拉取）
        with self._snapshot_lock:
            try:
                # 双检：等待锁期间可能已被其他线程刷新
                import gzip
                import pickle
                if cache_path.exists():
                    age = time.time() - cache_path.stat().st_mtime
                    if age < ttl:
                        with gzip.open(cache_path, "rb") as f:
                            return pickle.load(f)

                import akshare as ak
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with gzip.open(cache_path, "wb") as f:
                        pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)
                    return df
            except Exception as e:
                logger.error(f"获取全市场快照失败: {e}")

        return None

    def batch_download(
        self,
        symbols: List[str],
        start_date: str = "2020-01-01",
        end_date: str = "",
        errors: Optional[Dict[str, str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """批量下载数据（串行版，兼容同步调用方）

        大量标的时推荐使用 batch_download_async（并发 + 限流）。

        Args:
            symbols: 标的代码列表
            start_date: 开始日期
            end_date: 结束日期
            errors: 可选**出参**。传入一个 dict，失败的 {symbol: 原因} 会写入其中。

        Note:
            失败必须可被调用方获取（``errors`` 出参），不能只进日志。
            在线取数的失败是**非随机**的 —— 停牌 / ST / 次新 / 退市标的更容易失败，
            仅凭 ``len(results)`` 无法判断股票池是否被系统性污染。
            返回空 DataFrame 与抛异常同样记为失败，两者都进 ``errors``。

        Returns:
            {symbol: DataFrame} 字典（仅含成功项）
        """
        results: Dict[str, pd.DataFrame] = {}
        total = len(symbols)

        for i, symbol in enumerate(symbols):
            logger.info(f"下载进度: {i + 1}/{total} - {symbol}")
            try:
                df = self.get_daily(symbol, start_date, end_date)
                if not df.empty:
                    results[symbol] = df
                else:
                    if errors is not None:
                        errors[symbol] = "数据源返回空数据"
                    logger.warning(f"{symbol}: 数据源返回空数据")
            except Exception as e:
                reason = f"{type(e).__name__}: {e}"
                if errors is not None:
                    errors[symbol] = reason
                logger.error(f"下载 {symbol} 失败: {e}")

        logger.info(f"批量下载完成: {len(results)}/{total} 成功")
        return results

    async def batch_download_async(
        self,
        symbols: List[str],
        start_date: str = "2020-01-01",
        end_date: str = "",
        max_concurrent: int = 8,
        errors: Optional[Dict[str, str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """批量下载数据（异步并发版）

        使用 asyncio.Semaphore 限流，避免对数据源造成压力。
        先检查统一缓存，缓存命中直接返回，不触发网络请求。

        Args:
            symbols: 标的代码列表
            start_date: 开始日期
            end_date: 结束日期
            max_concurrent: 最大并发数（默认 8）
            errors: 可选**出参**。传入一个 dict，失败的 {symbol: 原因} 会写入其中。
                    语义见 :meth:`batch_download` 的 Note —— 失败是非随机的，
                    静默吞掉会让股票池系统性偏离。

        Returns:
            {symbol: DataFrame} 字典（仅含成功项）
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)
        results: Dict[str, pd.DataFrame] = {}
        total = len(symbols)

        async def _download_one(symbol: str) -> None:
            async with semaphore:
                try:
                    df = self.get_daily(symbol, start_date, end_date)
                    if not df.empty:
                        results[symbol] = df
                    else:
                        if errors is not None:
                            errors[symbol] = "数据源返回空数据"
                        logger.warning(f"{symbol}: 数据源返回空数据")
                except Exception as e:
                    reason = f"{type(e).__name__}: {e}"
                    if errors is not None:
                        errors[symbol] = reason
                    logger.error(f"下载 {symbol} 失败: {e}")

        # 分片并发，避免一次性创建过多任务
        BATCH = max_concurrent * 4
        for start in range(0, total, BATCH):
            chunk = symbols[start:start + BATCH]
            await asyncio.gather(*(_download_one(s) for s in chunk))
            logger.info(
                f"并发下载进度: {min(start + BATCH, total)}/{total}"
            )

        logger.info(f"并发批量下载完成: {len(results)}/{total} 成功")
        return results

    def batch_download_runner(
        self,
        symbols: List[str],
        start_date: str = "2020-01-01",
        end_date: str = "",
        max_concurrent: int = 8,
        errors: Optional[Dict[str, str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """批量下载（同步入口，内部运行事件循环）

        无 asyncio 上下文的同步代码可直接调用本方法获得并发收益。

        Args:
            symbols: 标的代码列表
            start_date: 开始日期
            end_date: 结束日期
            max_concurrent: 最大并发数（默认 8）
            errors: 可选**出参**，失败的 {symbol: 原因}。语义见 :meth:`batch_download`。

        Returns:
            {symbol: DataFrame} 字典（仅含成功项）
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # 已在事件循环内：不能嵌套 run_until_complete，退回串行
            logger.warning("已在运行事件循环中，使用串行批量下载")
            return self.batch_download(symbols, start_date, end_date, errors=errors)

        return loop.run_until_complete(
            self.batch_download_async(
                symbols, start_date, end_date, max_concurrent, errors=errors
            )
        )

    @staticmethod
    def _standardize_symbol(symbol: str) -> str:
        """标准化标的代码(去除后缀和空格)"""
        return symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "").strip()

    @staticmethod
    def _to_tushare_symbol(symbol: str) -> str:
        """转换为tushare格式代码"""
        symbol = symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        if symbol.startswith("6"):
            return f"{symbol}.SH"
        elif symbol.startswith(("0", "3")):
            return f"{symbol}.SZ"
        elif symbol.startswith(("4", "8")):
            return f"{symbol}.BJ"
        return f"{symbol}.SH"

    @staticmethod
    def _to_akshare_symbol(symbol: str) -> str:
        """转换为akshare格式代码(纯数字)"""
        return symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "").strip()

    @staticmethod
    def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
        """标准化DataFrame列名"""
        column_map = {
            "trade_date": "date",
            "vol": "volume",
            "amount": "amount",
            "pre_close": "pre_close",
            "昨收": "pre_close",
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

        # 必要列校验（L5b）：缺失即真实失败，不伪造零值价格
        required = ["date", "open", "high", "low", "close", "volume"]
        missing = [col for col in required if col not in df.columns]
        if missing:
            raise ValueError(
                f"数据缺失必要列: {missing}（数据源返回结构异常，无法继续分析）"
            )

        # 派生昨收列（供涨跌停撮合约束使用）：
        # 数据源未提供 pre_close 时，用前一根 close 填充
        if "pre_close" not in df.columns:
            df["pre_close"] = df["close"].shift(1)
        # 注意：pandas 2.x 已移除 fillna(method=...) 参数，用 bfill() 等价替代
        df["pre_close"] = df["pre_close"].bfill().fillna(df["close"])

        return df
