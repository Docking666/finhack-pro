#!/usr/bin/env python3
"""
从 free-stockdb 本地引擎导入历史数据到量化仓库

前提（务必按顺序）：
    1. 运行上游「数据更新.exe」把历史数据同步到本地磁盘（增量、断点续传）
    2. 启动 stockdb.exe（默认 127.0.0.1:7899）
    3. 再运行本脚本

**禁止直连公共服务器批量导入**——上游公告明确：连续批量拉取触发风控后会
返回随机 mock 数据。本脚本默认只连 127.0.0.1，且入库前做诱饵检测，
疑似 mock 数据的标的会被拒收并计入失败清单，绝不静默入库。

Usage:
    # 探测：确认引擎可达、查看响应形态与单个标的的字段
    python scripts/import_free_stockdb.py --probe 600519.SH

    # 列出引擎内全部标的
    python scripts/import_free_stockdb.py --list

    # 导入（可先 --limit 20 冒烟）
    python scripts/import_free_stockdb.py --warehouse-dir data/warehouse --start 2020-01-01
    python scripts/import_free_stockdb.py --symbols 600519,000001 --start 2020-01-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

from finhack_pro.utils.logger import setup_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="free-stockdb -> 本地量化仓库 导入工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="引擎地址（默认 127.0.0.1，勿改公网）")
    parser.add_argument("--port", type=int, default=7899, help="引擎端口（默认 7899）")
    parser.add_argument("--probe", default="", help="探测模式：打印指定标的的原始响应与规整结果")
    parser.add_argument("--list", action="store_true", help="列出引擎内全部标的代码")
    parser.add_argument("--symbols", default="", help="逗号分隔的标的列表；缺省用引擎全部标的")
    parser.add_argument("--start", default="2020-01-01", help="开始日期")
    parser.add_argument("--end", default="", help="结束日期（默认今天）")
    parser.add_argument("--warehouse-dir", default="data/warehouse", help="仓库根目录")
    parser.add_argument("--workers", type=int, default=2, help="并发数（本地引擎 2 足够）")
    parser.add_argument("--limit", type=int, default=0, help=">0 时只导入前 N 个（冒烟）")
    parser.add_argument("--adjust", default="qfq", choices=["qfq", ""], help="复权方式")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logger(log_level=args.log_level)

    from finhack_pro.data.collector import MarketDataCollector
    from finhack_pro.data.free_stockdb import FreeStockDBClient, FreeStockDBSource
    from finhack_pro.data.warehouse import MarketWarehouse

    client = FreeStockDBClient(host=args.host, port=args.port)
    client.ping()
    logger.info("引擎可达: {}", client.base_url)

    if args.probe:
        code = args.probe.split(".")[0].split(".SH")[0].split(".SZ")[0]
        raw = client._fetch_records("日k", code, args.start, args.end or "20991231")
        logger.info("原始记录数: {}", len(raw))
        if raw:
            logger.info("字段: {}", sorted(raw[0].keys()))
            logger.info("首条: {}", raw[0])
            client.check_decoy(raw)
            logger.info("诱饵检测: 通过")
        return 0

    if args.list:
        codes = client.list_symbols()
        logger.info("共 {} 个标的，示例: {}", len(codes), codes[:10])
        return 0

    # ---- 导入 ----
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = client.list_symbols()
        logger.info("未指定标的，使用引擎全部 {} 个", len(symbols))

    warehouse = MarketWarehouse(args.warehouse_dir)
    source = FreeStockDBSource(
        adjust=args.adjust,
        free_stockdb_host=args.host,
        free_stockdb_port=args.port,
    )
    collector = MarketDataCollector(warehouse, source, max_workers=args.workers)
    report = collector.run(
        symbols, start=args.start, end=args.end, freq="daily", limit=args.limit
    )

    logger.info("=" * 60)
    logger.info(report.summary())
    if not report.ok:
        for sym, reason in list(report.failed.items())[:20]:
            logger.error("  失败 {}: {}", sym, reason)
        for sym, reasons in list(report.rejected.items())[:20]:
            logger.error("  拒收 {}: {}", sym, "；".join(reasons))
        logger.error(
            "失败/拒收清单已落盘：data/warehouse/_failures_daily.json。"
            "若大量『疑似 mock 数据』，说明本地引擎数据曾被风控污染，"
            "请重新运行上游数据更新工具后重试。"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
