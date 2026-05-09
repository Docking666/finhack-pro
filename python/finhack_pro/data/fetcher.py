"""
数据获取模块

支持tushare和akshare两个数据源，自动fallback。
提供日线、分钟线、实时行情的获取接口，支持批量下载和CSV缓存。
"""

from __future__ import annotations

import os
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
    数据存储为CSV格式，兼容Rust引擎。

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
    ) -> None:
        """初始化数据获取器

        Args:
            source: 数据源 (akshare / tushare)
            tushare_token: tushare API token
            cache_dir: 缓存目录
        """
        self.source = source
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 初始化数据源客户端
        self._tushare_pro: Optional[Any] = None
        self._akshare_available: bool = False
        self._tushare_available: bool = False

        if source == "tushare" and tushare_token:
            self._init_tushare(tushare_token)

        # akshare总是尝试初始化
        self._init_akshare()

        logger.info(
            f"数据获取器初始化: source={source}, "
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

        # 检查缓存
        cache_file = self.cache_dir / f"{std_symbol}_daily_{start_date}_{end_date}.csv"
        if use_cache and cache_file.exists():
            logger.debug(f"从缓存加载: {std_symbol}")
            df = pd.read_csv(cache_file, parse_dates=["date"])
            return df

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
            # 保存缓存
            df.to_csv(cache_file, index=False)
            logger.info(f"数据获取成功: {std_symbol}, {len(df)}条记录")

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
                adjust="qfq",  # 前复权
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
                    adjust="qfq",
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

        Args:
            symbol: 标的代码

        Returns:
            实时行情字典
        """
        std_symbol = self._standardize_symbol(symbol)

        if self._akshare_available:
            try:
                import akshare as ak
                ak_symbol = self._to_akshare_symbol(std_symbol)
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
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

    def batch_download(
        self,
        symbols: List[str],
        start_date: str = "2020-01-01",
        end_date: str = "",
    ) -> Dict[str, pd.DataFrame]:
        """批量下载数据

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
        }
        df = df.rename(columns={k: v for k, v in column_map.items() if k in df.columns})

        # 确保必要列存在
        required = ["date", "open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                df[col] = 0.0

        return df
