"""
数据验证模块

提供 OHLCV 数据结构验证、自动修复、异常检测和数据质量报告功能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# 数据类定义
# ============================================================================


@dataclass
class DataAnomaly:
    """数据异常记录"""

    type: str  # 异常类型: price_gap, volume_spike, zero_volume, stale_prices
    index: Any  # 异常所在位置索引
    value: Any  # 异常值
    expected_range: str  # 期望范围描述
    severity: str  # 严重程度: low, medium, high


@dataclass
class ValidationResult:
    """验证结果"""

    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def add_error(self, msg: str) -> None:
        """添加错误"""
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str) -> None:
        """添加警告"""
        self.warnings.append(msg)


# ============================================================================
# 数据验证器
# ============================================================================


class DataValidator:
    """OHLCV 数据验证器

    提供全面的数据质量检查，包括结构验证、类型检查、逻辑约束检查
    和异常检测。

    Usage:
        validator = DataValidator()
        result = validator.validate_ohlcv(df)
        if not result.is_valid:
            print(result.errors)
    """

    REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
    OHLC_COLUMNS = ["open", "high", "low", "close"]

    def validate_ohlcv(self, df: pd.DataFrame) -> ValidationResult:
        """验证 OHLCV 数据结构

        Args:
            df: 待验证的 DataFrame

        Returns:
            ValidationResult 包含验证结果、错误和警告
        """
        result = ValidationResult()
        result.stats["row_count"] = len(df)
        result.stats["column_count"] = len(df.columns)

        # 空数据检查
        if df.empty:
            result.add_error("DataFrame 为空")
            return result

        # 检查必需列
        missing_cols = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            result.add_error(f"缺少必需列: {missing_cols}")
            return result

        # 检查 date 列类型
        if not pd.api.types.is_datetime64_any_dtype(df["date"]):
            result.add_error("date 列不是 datetime 类型")

        # 检查 OHLCV 列是否为数值类型
        for col in self.OHLC_COLUMNS + ["volume"]:
            if not pd.api.types.is_numeric_dtype(df[col]):
                result.add_error(f"{col} 列不是数值类型")

        # 检查关键列是否有 NaN
        for col in self.REQUIRED_COLUMNS:
            nan_count = df[col].isna().sum()
            if nan_count > 0:
                result.add_error(f"{col} 列存在 {nan_count} 个 NaN 值")

        # 检查 high >= low
        invalid_hl = df[df["high"] < df["low"]]
        if not invalid_hl.empty:
            count = len(invalid_hl)
            result.add_error(f"存在 {count} 行 high < low")

        # 检查 high >= open
        invalid_ho = df[df["high"] < df["open"]]
        if not invalid_ho.empty:
            count = len(invalid_ho)
            result.add_warning(f"存在 {count} 行 high < open")

        # 检查 high >= close
        invalid_hc = df[df["high"] < df["close"]]
        if not invalid_hc.empty:
            count = len(invalid_hc)
            result.add_warning(f"存在 {count} 行 high < close")

        # 检查 low <= open
        invalid_lo = df[df["low"] > df["open"]]
        if not invalid_lo.empty:
            count = len(invalid_lo)
            result.add_warning(f"存在 {count} 行 low > open")

        # 检查 low <= close
        invalid_lc = df[df["low"] > df["close"]]
        if not invalid_lc.empty:
            count = len(invalid_lc)
            result.add_warning(f"存在 {count} 行 low > close")

        # 检查 volume >= 0
        neg_vol = df[df["volume"] < 0]
        if not neg_vol.empty:
            count = len(neg_vol)
            result.add_error(f"存在 {count} 行 volume < 0")

        # 检查价格 > 0
        for col in self.OHLC_COLUMNS:
            non_pos = df[df[col] <= 0]
            if not non_pos.empty:
                count = len(non_pos)
                result.add_error(f"存在 {count} 行 {col} <= 0")

        # 检查日期排序
        if pd.api.types.is_datetime64_any_dtype(df["date"]):
            if not df["date"].is_monotonic_increasing:
                result.add_error("日期未按升序排列")

            # 检查重复日期
            dup_count = df["date"].duplicated().sum()
            if dup_count > 0:
                result.add_error(f"存在 {dup_count} 个重复日期")

        # 统计信息
        if pd.api.types.is_datetime64_any_dtype(df["date"]):
            result.stats["date_range"] = (
                f"{df['date'].min().strftime('%Y-%m-%d')} ~ "
                f"{df['date'].max().strftime('%Y-%m-%d')}"
            )
        result.stats["nan_counts"] = {
            col: int(df[col].isna().sum()) for col in self.REQUIRED_COLUMNS
        }

        return result

    def validate_ohlcv_batch(
        self, data: Dict[str, pd.DataFrame]
    ) -> Dict[str, ValidationResult]:
        """批量验证多标的数据

        Args:
            data: {symbol: DataFrame} 字典

        Returns:
            {symbol: ValidationResult} 字典
        """
        results = {}
        for symbol, df in data.items():
            results[symbol] = self.validate_ohlcv(df)
            logger.debug(f"验证 {symbol}: {'通过' if results[symbol].is_valid else '失败'}")
        return results

    def clean_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        """自动修复常见 OHLCV 数据问题

        执行以下修复:
        - 前向填充 NaN 值
        - 修复 high < low 的情况（交换 high 和 low）
        - 移除零/负价格行
        - 去除重复日期（保留最后一条）
        - 按日期升序排列

        Args:
            df: 待修复的 DataFrame

        Returns:
            修复后的 DataFrame
        """
        if df.empty:
            return df

        result = df.copy()

        # 确保必需列存在
        for col in self.REQUIRED_COLUMNS:
            if col not in result.columns:
                result[col] = np.nan

        # 前向填充 NaN 值
        numeric_cols = [c for c in result.columns if pd.api.types.is_numeric_dtype(result[c])]
        result[numeric_cols] = result[numeric_cols].ffill()

        # 修复 high < low: 交换 high 和 low
        swap_mask = result["high"] < result["low"]
        if swap_mask.any():
            logger.debug(f"修复 {swap_mask.sum()} 行 high < low (交换)")
            result.loc[swap_mask, ["high", "low"]] = result.loc[swap_mask, ["low", "high"]].values

        # 移除零/负价格行
        price_mask = pd.Series(True, index=result.index)
        for col in self.OHLC_COLUMNS:
            if col in result.columns:
                price_mask &= result[col] > 0
        removed_count = (~price_mask).sum()
        if removed_count > 0:
            logger.debug(f"移除 {removed_count} 行零/负价格")
            result = result[price_mask].copy()

        # 去除重复日期，保留最后一条
        if "date" in result.columns:
            dup_count = result["date"].duplicated().sum()
            if dup_count > 0:
                logger.debug(f"去除 {dup_count} 个重复日期")
                result = result.drop_duplicates(subset=["date"], keep="last").copy()

            # 按日期升序排列
            result = result.sort_values("date").reset_index(drop=True)

        return result

    def detect_anomalies(self, df: pd.DataFrame) -> List[DataAnomaly]:
        """检测数据异常

        检测以下异常:
        - 价格缺口 > 20% (可疑)
        - 成交量突增 > 10 倍平均值
        - 零成交量交易日
        - 停滞价格 (OHLC 连续 5+ 天完全相同)

        Args:
            df: 待检测的 DataFrame

        Returns:
            DataAnomaly 列表
        """
        anomalies: List[DataAnomaly] = []

        if df.empty or len(df) < 2:
            return anomalies

        # 确保按日期排序
        if "date" in df.columns:
            df = df.sort_values("date").reset_index(drop=True)

        # 1. 价格缺口 > 20%
        if "close" in df.columns:
            prev_close = df["close"].shift(1)
            pct_change = (df["close"] - prev_close).abs() / prev_close.replace(0, np.nan)
            gap_mask = pct_change > 0.20
            for idx in df.index[gap_mask]:
                anomalies.append(
                    DataAnomaly(
                        type="price_gap",
                        index=idx,
                        value=round(float(pct_change.loc[idx]), 4),
                        expected_range="日涨跌幅 <= 20%",
                        severity="high",
                    )
                )

        # 2. 成交量突增 > 10 倍平均值
        if "volume" in df.columns:
            avg_vol = df["volume"].rolling(20, min_periods=1).mean()
            vol_ratio = df["volume"] / avg_vol.replace(0, np.nan)
            spike_mask = vol_ratio > 10
            for idx in df.index[spike_mask]:
                anomalies.append(
                    DataAnomaly(
                        type="volume_spike",
                        index=idx,
                        value=round(float(vol_ratio.loc[idx]), 2),
                        expected_range="成交量 <= 10x 均值",
                        severity="medium",
                    )
                )

        # 3. 零成交量交易日
        if "volume" in df.columns:
            zero_vol_mask = df["volume"] == 0
            for idx in df.index[zero_vol_mask]:
                anomalies.append(
                    DataAnomaly(
                        type="zero_volume",
                        index=idx,
                        value=0,
                        expected_range="volume > 0",
                        severity="medium",
                    )
                )

        # 4. 停滞价格 (OHLC 连续 5+ 天完全相同)
        if all(c in df.columns for c in self.OHLC_COLUMNS):
            ohlc_tuple = list(zip(df["open"], df["high"], df["low"], df["close"]))
            streak = 0
            streak_start = 0
            for i in range(1, len(ohlc_tuple)):
                if ohlc_tuple[i] == ohlc_tuple[i - 1]:
                    if streak == 0:
                        streak_start = i - 1
                    streak += 1
                    if streak >= 4:  # 连续 5 天 (当前 + 前 4)
                        anomalies.append(
                            DataAnomaly(
                                type="stale_prices",
                                index=streak_start,
                                value=f"连续 {streak + 1} 天 OHLC 相同",
                                expected_range="OHLC 不应连续多日相同",
                                severity="low",
                            )
                        )
                        break  # 只报告一次
                else:
                    streak = 0

        return anomalies


# ============================================================================
# 数据质量报告
# ============================================================================


class DataQualityReport:
    """数据质量报告生成器

    Usage:
        report = DataQualityReport()
        report_str = report.generate(df, symbol="600519")
    """

    def __init__(self) -> None:
        self.validator = DataValidator()

    def generate(
        self,
        df: pd.DataFrame,
        symbol: str = "",
    ) -> str:
        """生成数据质量报告

        Args:
            df: 待分析的 DataFrame
            symbol: 标的代码 (用于报告标题)

        Returns:
            格式化的质量报告字符串
        """
        lines: List[str] = []
        title = f"数据质量报告 - {symbol}" if symbol else "数据质量报告"
        lines.append("=" * 60)
        lines.append(title)
        lines.append("=" * 60)

        if df.empty:
            lines.append("WARNING: DataFrame 为空")
            return "\n".join(lines)

        # 基本信息
        lines.append("\n[基本信息]")
        lines.append(f"  行数: {len(df)}")
        lines.append(f"  列数: {len(df.columns)}")
        lines.append(f"  列名: {list(df.columns)}")

        if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
            lines.append(
                f"  日期范围: {df['date'].min().strftime('%Y-%m-%d')} ~ "
                f"{df['date'].max().strftime('%Y-%m-%d')}"
            )

        # 验证结果
        result = self.validator.validate_ohlcv(df)
        lines.append("\n[验证结果]")
        lines.append(f"  状态: {'通过' if result.is_valid else '未通过'}")

        if result.errors:
            lines.append(f"  错误 ({len(result.errors)}):")
            for err in result.errors:
                lines.append(f"    - {err}")

        if result.warnings:
            lines.append(f"  警告 ({len(result.warnings)}):")
            for warn in result.warnings:
                lines.append(f"    - {warn}")

        # 统计摘要
        lines.append("\n[统计摘要]")
        for col in DataValidator.OHLC_COLUMNS:
            if col in df.columns:
                lines.append(
                    f"  {col}: mean={df[col].mean():.2f}, "
                    f"std={df[col].std():.2f}, "
                    f"min={df[col].min():.2f}, "
                    f"max={df[col].max():.2f}"
                )
        if "volume" in df.columns:
            lines.append(
                f"  volume: mean={df['volume'].mean():.0f}, "
                f"std={df['volume'].std():.0f}, "
                f"min={df['volume'].min():.0f}, "
                f"max={df['volume'].max():.0f}"
            )

        # 异常检测
        anomalies = self.validator.detect_anomalies(df)
        lines.append("\n[异常检测]")
        lines.append(f"  发现异常: {len(anomalies)} 个")
        if anomalies:
            anomaly_types = {}
            for a in anomalies:
                anomaly_types[a.type] = anomaly_types.get(a.type, 0) + 1
            for atype, count in anomaly_types.items():
                lines.append(f"    - {atype}: {count} 个")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
