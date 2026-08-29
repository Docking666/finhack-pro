"""置信度合成（阶段3：零 LLM，全部确定性因子）

受 Tianfu Agent 三层不确定性量化启发，但**不引入 LLM 自评**——
量化有"回测=单元测试"这一术数没有的验证手段，因此置信度全部来自
确定性信号：

- 回测场景：StrategyValidator 综合分 + 数据完整度
- 流水线场景：风控通过率 + 辩论共识 + 数据完整度 + 历史验证分

输出统一为 {score: 0-100, tier: high/medium/low, factors: {...}}，
前端/报告只展示分档 + 因子构成，不显示伪精确的小数。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 分档阈值：≥70 高 / 50-70 中 / <50 低
TIER_HIGH = 70.0
TIER_MEDIUM = 50.0


def tier_for_score(score: float) -> str:
    """分档：≥70 高 / 50-70 中 / <50 低"""
    if score >= TIER_HIGH:
        return "high"
    if score >= TIER_MEDIUM:
        return "medium"
    return "low"


def data_completeness_from_df(df: Any) -> float:
    """数据完整度 0-1（回测场景）

    指标列覆盖率（_EXTRA_COLUMNS 白名单中实际存在的列占比，B1 修复后
    普通策略预计算也会产出）+ 行完整性（close 非空比例）。
    """
    if df is None:
        return 0.0
    try:
        cols = ("volume_ratio", "ma20", "rsi", "macd_signal", "turnover", "market_cap", "net_inflow")
        present = sum(1 for c in cols if c in df.columns) / len(cols)
        n = len(df)
        nonnull = float(df["close"].notna().mean()) if n else 0.0
        return round(present * 0.6 + nonnull * 0.4, 3)
    except Exception:
        return 0.0


def backtest_confidence(
    validation: Optional[Dict[str, Any]],
    data_completeness: float,
) -> Dict[str, Any]:
    """回测场景置信度：验证分 0.60 / 数据完整度 0.40

    Args:
        validation: StrategyValidator 输出（含 overall_score 0-100）
        data_completeness: 数据完整度 0-1
    """
    v_score = float((validation or {}).get("overall_score", 0.0) or 0.0)
    v_score = max(0.0, min(100.0, v_score))
    completeness = max(0.0, min(1.0, float(data_completeness or 0.0)))

    score = round(v_score * 0.60 + completeness * 100 * 0.40, 1)
    return {
        "score": score,
        "tier": tier_for_score(score),
        "factors": {
            "validation_score": round(v_score, 1),
            "data_completeness": completeness,
        },
        "weights": {"validation_score": 0.60, "data_completeness": 0.40},
    }


def pipeline_confidence(
    risk_checks: Optional[list] = None,
    debate: Optional[Dict[str, Any]] = None,
    data_completeness: float = 1.0,
    historical_score: Optional[float] = None,
) -> Dict[str, Any]:
    """流水线场景置信度：四因子固定权重

    权重：风控通过率 0.30 / 辩论共识 0.30 / 数据完整度 0.20 / 历史验证分 0.20
    （历史验证分缺失时权重重分配给前三项：0.375/0.375/0.25）

    Args:
        risk_checks: 风控逐项检查（B6 结构化），[{name, passed, detail}]
        debate: BullBearDebateResult 的 model_dump（含 bull_strength/bear_strength）
        data_completeness: 数据完整度 0-1
        historical_score: 该标的历史回测验证分 0-100（无则 None）
    """
    factors: Dict[str, float] = {}

    # 1. 风控通过率
    if risk_checks:
        passed = sum(1 for c in risk_checks if c.get("passed"))
        risk_rate = passed / max(len(risk_checks), 1)
    else:
        risk_rate = 1.0  # 无检查视为通过（流水线前置场景）
    factors["risk_pass_rate"] = round(risk_rate, 3)

    # 2. 辩论共识：1 - |bull - bear|
    if debate and debate.get("bull_strength") is not None and debate.get("bear_strength") is not None:
        consensus = 1.0 - abs(float(debate["bull_strength"]) - float(debate["bear_strength"]))
    else:
        consensus = 0.5  # 无辩论取中性
    factors["debate_consensus"] = round(max(0.0, min(1.0, consensus)), 3)

    # 3. 数据完整度
    completeness = max(0.0, min(1.0, float(data_completeness or 0.0)))
    factors["data_completeness"] = completeness

    # 4. 历史验证分（缺失则重分配权重）
    if historical_score is not None:
        hist = max(0.0, min(100.0, float(historical_score)))
        factors["historical_validation"] = round(hist, 1)
        weights = {"risk_pass_rate": 0.30, "debate_consensus": 0.30,
                   "data_completeness": 0.20, "historical_validation": 0.20}
    else:
        factors["historical_validation"] = 0.0
        weights = {"risk_pass_rate": 0.375, "debate_consensus": 0.375,
                   "data_completeness": 0.25, "historical_validation": 0.0}

    score = round(
        factors["risk_pass_rate"] * 100 * weights["risk_pass_rate"]
        + factors["debate_consensus"] * 100 * weights["debate_consensus"]
        + completeness * 100 * weights["data_completeness"]
        + (factors["historical_validation"] if historical_score is not None else 0.0) * weights["historical_validation"],
        1,
    )
    return {
        "score": score,
        "tier": tier_for_score(score),
        "factors": factors,
        "weights": weights,
    }
