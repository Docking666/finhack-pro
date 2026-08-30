#!/usr/bin/env python3
"""
数据采集脚本

下载股票历史数据并保存为CSV格式。

Usage:
    # 下载单只股票
    python scripts/fetch_data.py --symbol 600519.SH --start 2023-01-01 --end 2024-01-01

    # 批量下载
    python scripts/fetch_data.py --symbols 600519.SH,000001.SZ,000858.SZ --start 2020-01-01

    # 使用tushare数据源
    python scripts/fetch_data.py --symbol 600519.SH --source tushare --tushare-token YOUR_TOKEN
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger

from finhack_pro.config import get_config, reset_config
from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.data.technical import TechnicalIndicator
from finhack_pro.utils.logger import setup_logger


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="FinHack Pro 数据采集工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--symbol", "-s",
        type=str,
        default="",
        help="单个标的代码",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="多个标的代码(逗号分隔)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2020-01-01",
        help="开始日期 (默认: 2020-01-01)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="结束日期 (默认: 今天)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="akshare",
        choices=["akshare", "tushare"],
        help="数据源 (默认: akshare)",
    )
    parser.add_argument(
        "--tushare-token",
        type=str,
        default="",
        help="Tushare API Token",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data",
        help="数据输出目录 (默认: data)",
    )
    parser.add_argument(
        "--add-indicators",
        action="store_true",
        help="是否添加技术指标",
    )
    parser.add_argument(
        "--warehouse",
        action="store_true",
        help=(
            "写入本地量化仓库（永久事实库）而非 CSV。全市场扫描与回测可复现依赖该模式： "
            "支持断点续传（已覆盖区间自动跳过）、限流抖动、失败清单落盘。"
        ),
    )
    parser.add_argument(
        "--warehouse-dir",
        type=str,
        default="",
        help=f"仓库根目录 (默认取配置 data.warehouse_dir)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并发数 (默认: 4；过高易触发数据源反爬)",
    )
    parser.add_argument(
        "--freq",
        type=str,
        default="daily",
        help="频率分区: daily / min5 / min15 / min30 / min60 (默认: daily)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="",
        help="配置文件路径",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别",
    )

    return parser.parse_args()


def main() -> None:
    """主函数"""
    args = parse_args()

    # 初始化日志
    setup_logger(log_level=args.log_level)

    # 加载配置
    reset_config()
    config = get_config(args.config or None)

    # 确定要下载的标的
    symbols: list[str] = []
    if args.symbol:
        symbols.append(args.symbol)
    if args.symbols:
        symbols.extend(s.strip() for s in args.symbols.split(","))

    if not symbols:
        # 默认下载一些常见标的
        symbols = [
            "600519.SH",  # 贵州茅台
            "000001.SZ",  # 平安银行
            "000858.SZ",  # 五粮液
            "601318.SH",  # 中国平安
            "000333.SZ",  # 美的集团
        ]
        logger.info(f"未指定标的，使用默认列表: {symbols}")

    logger.info("=" * 60)
    logger.info("FinHack Pro 数据采集工具")
    logger.info("=" * 60)
    logger.info(f"标的数量: {len(symbols)}")
    logger.info(f"数据源: {args.source}")
    logger.info(f"时间范围: {args.start} ~ {args.end or '今天'}")
    logger.info(f"输出目录: {args.output_dir}")

    # 创建输出目录
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 初始化数据获取器
    fetcher = DataFetcher(
        source=args.source,
        tushare_token=args.tushare_token or config.data.tushare_token,
        cache_dir=str(output_dir / "cache"),
    )

    # ---- 仓库模式：断点续传 + 限流 + 失败显式化 ----
    if args.warehouse:
        from finhack_pro.data.collector import MarketDataCollector
        from finhack_pro.data.warehouse import MarketWarehouse

        wh_dir = args.warehouse_dir or config.data.warehouse_dir
        wh = MarketWarehouse(wh_dir, backend=config.data.warehouse_backend)
        collector = MarketDataCollector(wh, fetcher, max_workers=args.workers)
        report = collector.run(symbols, start=args.start, end=args.end, freq=args.freq)

        logger.info("=" * 60)
        logger.info(report.summary())
        logger.info(f"仓库目录: {Path(wh_dir).absolute()}")
        if not report.ok:
            # 非随机失败必须中断 CI：静默继续会让股票池系统性偏离
            for sym, reason in list(report.failed.items())[:20]:
                logger.error(f"  取数失败 {sym}: {reason}")
            for sym, reasons in list(report.rejected.items())[:20]:
                logger.error(f"  校验拒收 {sym}: {'；'.join(reasons)}")
            sys.exit(1)
        return

    # ---- 传统 CSV 模式 ----
    errors: dict[str, str] = {}
    results = fetcher.batch_download(symbols, args.start, args.end, errors=errors)

    # 保存数据
    success_count = 0
    for symbol, df in results.items():
        if df.empty:
            logger.warning(f"{symbol}: 数据为空，跳过")
            continue

        # 添加技术指标
        if args.add_indicators:
            ti = TechnicalIndicator()
            df = ti.add_all_indicators(df)

        # 保存CSV
        std_symbol = symbol.replace(".", "_")
        output_file = output_dir / f"{std_symbol}.csv"
        df.to_csv(output_file, index=False)
        logger.info(f"已保存: {output_file} ({len(df)} 条记录)")
        success_count += 1

    logger.info("=" * 60)
    logger.info(f"数据采集完成: {success_count}/{len(symbols)} 成功")
    logger.info(f"数据保存在: {output_dir.absolute()}")

    if errors:
        # 退出码非 0 才能让 CI / 定时任务感知"数据不全"
        for sym, reason in errors.items():
            logger.error(f"  失败 {sym}: {reason}")
        logger.error(f"共 {len(errors)}/{len(symbols)} 个标的取数失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
