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
    ) -> None:
        """初始化数据获取器

        Args:
            source: 数据源 (akshare / tushare)
            tushare_token: tushare API token
            cache_dir: 缓存目录
            adjust: 复权方式 (qfq 前复权 / hfq 后复权 / "" 不复权)
        """
        self.source = source
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.adjust = adjust

        # 统一缓存：DataCache（pickle+gzip，TTL+MD5 校验，按日期过滤）
        from finhack_pro.data.cache import DataCache
        self._cache = DataCache(cache_dir=str(self.cache_dir))

        # 全市场快照缓存锁（get_realtime 并发保护）
        self._snapshot_lock = threading.Lock()

        # 初始化数据源客户端
        self._tushare_pro: Optional[Any] = None
        self._akshare_available: bool = False
        self._tushare_available: bool = False

        if source == "tushare" and tushare_token:
            self._init_tushare(tushare_token)

        # akshare总是尝试初始化
        self._init_akshare()

        logger.info(
            f"数据获取器初始化: source={source}, adjust={adjust}, "
            f"akshare={'可用' if self._akshare_available else '不可用'}, "
            f"tushare={'可用' if self._tushare_available else '不可用'}"
        )

    def _init_tushare(self, token: str) -> None:
        """初始化tushare"""
        try:
            import tushare
            tushare.set_token(token)
            self._tushare_pro = tushare.pro_api()
            self._tushare_available = True
            logger.info("Tushare初始化成功")
        except ImportError:
            logger.warning("tushare包未安装")
        except Exception as e:
            logger.warning(f"Tushare初始化失败: {e}")

    def _init_akshare(self) -> None:
        """初始化akshare"""
        try:
            import akshare  # noqa: F401
            self._akshare_available = True
            logger.info("AkShare初始化成功")
        except ImportError:
            logger.warning("akshare包未安装")

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
        # 相比旧的"日期入文件名"缓存，命中率大幅提高（不同日期范围共享同一份缓存）。
        if use_cache:
            cached = self._cache.get(std_symbol, start_date, end_date, freq="daily")
            if cached is not None:
                logger.debug(f"缓存命中: {std_symbol} ({start_date}~{end_date})")
                return cached

        # 获取数据
        df = pd.DataFrame()
        if self.source == "tushare" and self._tushare_available:
            df = self._fetch_daily_tushare(std_symbol, start_date, end_date)
        elif self._akshare_available:
            df = self._fetch_daily_akshare(std_symbol, start_date, end_date)

        # fallback: 如果主数据源失败，尝试另一个
        if df.empty:
            if self.source == "tushare" and self._akshare_available:
                logger.info("Tushare获取失败，尝试AkShare...")
                df = self._fetch_daily_akshare(std_symbol, start_date, end_date)
            elif self.source == "akshare" and self._tushare_available:
                logger.info("AkShare获取失败，尝试Tushare...")
                df = self._fetch_daily_tushare(std_symbol, start_date, end_date)

        if not df.empty:
            # 标准化列名
            df = self._standardize_columns(df)
            # 写入统一缓存
            self._cache.set(std_symbol, df, freq="daily")
            logger.info(f"数据获取成功: {std_symbol}, {len(df)}条记录")

        # 按日期范围过滤返回
        if not df.empty and "date" in df.columns:
            df = df[
                (df["date"] >= pd.to_datetime(start_date))
                & (df["date"] <= pd.to_datetime(end_date))
            ].reset_index(drop=True)

        return df

    def _fetch_daily_tushare(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """通过tushare获取日线数据"""
        try:
            assert self._tushare_pro is not None
            # tushare使用带后缀的代码
            ts_symbol = self._to_tushare_symbol(symbol)
            df = self._tushare_pro.daily(
                ts_code=ts_symbol,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if df is not None and not df.empty:
                df = df.rename(columns={
                    "trade_date": "date",
                    "vol": "volume",
                })
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
            return df or pd.DataFrame()
        except Exception as e:
            logger.error(f"Tushare获取日线失败: {e}")
            return pd.DataFrame()

    def _fetch_daily_akshare(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """通过akshare获取日线数据"""
        try:
            import akshare as ak

            # akshare使用纯数字代码
            ak_symbol = self._to_akshare_symbol(symbol)
            df = ak.stock_zh_a_hist(
                symbol=ak_symbol,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust=self.adjust,  # 复权方式配置化（qfq/hfq/空）
            )
            if df is not None and not df.empty:
                # akshare列名: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
                df = df.rename(columns={
                    "日期": "date",
                    "开盘": "open",
                    "收盘": "close",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                    "成交额": "amount",
                    "涨跌幅": "change_pct",
                    "换手率": "turnover",
                })
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date").reset_index(drop=True)
            return df or pd.DataFrame()
        except Exception as e:
            logger.error(f"AkShare获取日线失败: {e}")
            return pd.DataFrame()

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
                return df or pd.DataFrame()
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
    ) -> Dict[str, pd.DataFrame]:
        """批量下载数据（串行版，兼容同步调用方）

        大量标的时推荐使用 batch_download_async（并发 + 限流）。

        Args:
            symbols: 标的代码列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            {symbol: DataFrame} 字典
        """
        results: Dict[str, pd.DataFrame] = {}
        total = len(symbols)

        for i, symbol in enumerate(symbols):
            logger.info(f"下载进度: {i + 1}/{total} - {symbol}")
            try:
                df = self.get_daily(symbol, start_date, end_date)
                if not df.empty:
                    results[symbol] = df
            except Exception as e:
                logger.error(f"下载 {symbol} 失败: {e}")

        logger.info(f"批量下载完成: {len(results)}/{total} 成功")
        return results

    async def batch_download_async(
        self,
        symbols: List[str],
        start_date: str = "2020-01-01",
        end_date: str = "",
        max_concurrent: int = 8,
    ) -> Dict[str, pd.DataFrame]:
        """批量下载数据（异步并发版）

        使用 asyncio.Semaphore 限流，避免对数据源造成压力。
        先检查统一缓存，缓存命中直接返回，不触发网络请求。

        Args:
            symbols: 标的代码列表
            start_date: 开始日期
            end_date: 结束日期
            max_concurrent: 最大并发数（默认 8）

        Returns:
            {symbol: DataFrame} 字典
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
                except Exception as e:
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
    ) -> Dict[str, pd.DataFrame]:
        """批量下载（同步入口，内部运行事件循环）

        无 asyncio 上下文的同步代码可直接调用本方法获得并发收益。

        Args:
            symbols: 标的代码列表
            start_date: 开始日期
            end_date: 结束日期
            max_concurrent: 最大并发数（默认 8）

        Returns:
            {symbol: DataFrame} 字典
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
            return self.batch_download(symbols, start_date, end_date)

        return loop.run_until_complete(
            self.batch_download_async(
                symbols, start_date, end_date, max_concurrent
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

        # 确保必要列存在
        required = ["date", "open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                df[col] = 0.0

        # 派生昨收列（供涨跌停撮合约束使用）：
        # 数据源未提供 pre_close 时，用前一根 close 填充
        if "pre_close" not in df.columns:
            df["pre_close"] = df["close"].shift(1)
        df["pre_close"] = df["pre_close"].fillna(method="bfill").fillna(df["close"])

        return df
