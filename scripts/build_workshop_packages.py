"""
创意工坊首批内置策略打包脚本

把内置策略打包为工坊标准 zip 包，作为冷启动内容。
输出目录：python/data/workshop/

Usage:
    python scripts/build_workshop_packages.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from finhack_pro.workshop import PackageManager, StrategyManifest


# 内置策略元数据（与策略源码注释保持一致）
BUILTIN_STRATEGIES = [
    {
        "id": "dual_thrust",
        "name": "Dual Thrust 开盘区间突破",
        "version": "1.0.0",
        "author": "finhack",
        "description": "经典开盘区间突破策略：计算过去 N 日 HH/LC/HC/LL，突破上轨买入、跌破下轨卖出，带止损止盈。",
        "source": "finhack_pro/strategies/dual_thrust.py",
        "entry_class": "DualThrustStrategy",
        "params_schema": {
            "type": "object",
            "properties": {
                "k1": {"type": "number", "minimum": 0.0, "default": 0.5, "title": "上轨系数"},
                "k2": {"type": "number", "minimum": 0.0, "default": 0.5, "title": "下轨系数"},
                "lookback": {"type": "integer", "minimum": 2, "default": 20, "title": "回看天数"},
                "stop_loss_pct": {"type": "number", "minimum": 0.0, "default": 0.03, "title": "止损比例"},
                "take_profit_pct": {"type": "number", "minimum": 0.0, "default": 0.06, "title": "止盈比例"},
            },
            "required": ["k1", "k2", "lookback"],
        },
    },
    {
        "id": "momentum",
        "name": "动量突破策略",
        "version": "1.0.0",
        "author": "finhack",
        "description": "动量策略：基于 N 日收益率的动量筛选与再平衡，适合中线趋势跟踪。",
        "source": "finhack_pro/strategies/momentum.py",
        "entry_class": "MomentumStrategy",
        "params_schema": {
            "type": "object",
            "properties": {
                "momentum_period": {"type": "integer", "minimum": 2, "default": 20, "title": "动量周期"},
                "rebalance_days": {"type": "integer", "minimum": 1, "default": 5, "title": "再平衡间隔"},
                "top_n": {"type": "integer", "minimum": 1, "default": 3, "title": "持仓数量"},
            },
            "required": ["momentum_period"],
        },
    },
    {
        "id": "mean_reversion",
        "name": "均值回归策略",
        "version": "1.0.0",
        "author": "finhack",
        "description": "均值回归策略：RSI 超卖买入、超买卖出，适合震荡市。",
        "source": "finhack_pro/strategies/mean_reversion.py",
        "entry_class": "MeanReversionStrategy",
        "params_schema": {
            "type": "object",
            "properties": {
                "rsi_period": {"type": "integer", "minimum": 2, "default": 14, "title": "RSI 周期"},
                "oversold": {"type": "integer", "minimum": 1, "default": 30, "title": "超卖阈值"},
                "overbought": {"type": "integer", "minimum": 50, "default": 70, "title": "超买阈值"},
            },
            "required": ["rsi_period"],
        },
    },
]


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    python_dir = root / "python"
    strategies_dir = python_dir / "finhack_pro" / "strategies"
    out_dir = python_dir / "data" / "workshop"

    manager = PackageManager(
        workshop_dir=str(out_dir),
        strategies_dir=str(strategies_dir),
        allowlist_scope="finhack",
    )

    print(f"输出目录: {out_dir}")
    for spec in BUILTIN_STRATEGIES:
        src_file = python_dir / spec["source"]
        if not src_file.exists():
            print(f"  [SKIP] 源码不存在: {src_file}")
            continue

        manifest = StrategyManifest.from_dict({
            "id": spec["id"],
            "name": spec["name"],
            "version": spec["version"],
            "author": spec["author"],
            "description": spec["description"],
            "type": "strategy",
            "entry": "strategy.py",
            "entry_class": spec["entry_class"],
            "params_schema": spec["params_schema"],
        })
        pkg_path = manager.pack(strategy_dir=str(src_file.parent), manifest=manifest, out_dir=str(out_dir))
        print(f"  [OK] {manifest.package_id} → {pkg_path.name}")

    print("\n打包完成。可用 PackageManager.install() 安装这些包。")


if __name__ == "__main__":
    main()
