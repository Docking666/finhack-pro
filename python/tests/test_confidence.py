"""阶段3 置信度合成回归测试（零 LLM，确定性因子）"""

import numpy as np
import pandas as pd
import pytest

from finhack_pro.backtest.confidence import (
    backtest_confidence,
    data_completeness_from_df,
    pipeline_confidence,
    tier_for_score,
)


def _df_with_cols(extra_cols=("ma20", "rsi", "volume_ratio", "macd_signal"), n=100):
    df = pd.DataFrame({"close": np.linspace(100, 110, n), "volume": np.full(n, 1000)})
    for c in extra_cols:
        df[c] = 1.0
    return df


class TestTier:
    def test_tier_thresholds(self):
        assert tier_for_score(90) == "high"
        assert tier_for_score(70) == "high"
        assert tier_for_score(69.9) == "medium"
        assert tier_for_score(50) == "medium"
        assert tier_for_score(49.9) == "low"
        assert tier_for_score(0) == "low"


class TestBacktestConfidence:
    def test_full_mark_high(self):
        """验证分 100 + 完整度 1.0 → 100 分 high"""
        out = backtest_confidence({"overall_score": 100.0}, 1.0)
        assert out["score"] == 100.0
        assert out["tier"] == "high"
        assert out["factors"]["validation_score"] == 100.0

    def test_zero_validation_low(self):
        """无验证（validation=None）→ 仅完整度贡献 → 中低档"""
        out = backtest_confidence(None, 0.5)
        assert out["score"] == 20.0  # 0*0.6 + 0.5*100*0.4
        assert out["tier"] == "low"

    def test_weight_ratio(self):
        """验证分 80 + 完整度 0.5 → 0.6*80 + 0.4*50 = 68 → medium"""
        out = backtest_confidence({"overall_score": 80.0}, 0.5)
        assert out["score"] == 68.0
        assert out["tier"] == "medium"

    def test_validation_error_graceful(self):
        """validation 含 error（无 overall_score）→ 不抛异常"""
        out = backtest_confidence({"error": "x"}, 1.0)
        assert out["score"] == 40.0
        assert out["tier"] == "low"


class TestDataCompleteness:
    def test_full_columns(self):
        """4/7 白名单列 + 100% 行完整度 → 0.743"""
        val = data_completeness_from_df(_df_with_cols())
        assert val > 0.7
        assert val < 0.8

    def test_missing_columns_lower(self):
        bare = _df_with_cols(extra_cols=())
        full = _df_with_cols()
        assert data_completeness_from_df(bare) < data_completeness_from_df(full)

    def test_none_safe(self):
        assert data_completeness_from_df(None) == 0.0

    def test_empty_df_safe(self):
        assert data_completeness_from_df(pd.DataFrame()) == 0.0


class TestPipelineConfidence:
    def test_risk_checks_rate(self):
        """风控 8/10 通过 → risk_pass_rate 0.8"""
        checks = [{"name": f"c{i}", "passed": i < 8} for i in range(10)]
        out = pipeline_confidence(risk_checks=checks, data_completeness=1.0)
        assert out["factors"]["risk_pass_rate"] == 0.8

    def test_debate_consensus(self):
        """bull 0.55 / bear 0.5 → consensus 0.95"""
        out = pipeline_confidence(
            risk_checks=[],
            debate={"bull_strength": 0.55, "bear_strength": 0.5},
            data_completeness=1.0,
        )
        assert out["factors"]["debate_consensus"] == 0.95

    def test_historical_weight_redistribution(self):
        """无历史分 → 权重重分配给前三项（总和仍 1.0）"""
        out = pipeline_confidence(risk_checks=[], debate=None, data_completeness=1.0)
        assert out["factors"]["historical_validation"] == 0.0
        assert abs(sum(out["weights"].values()) - 1.0) < 1e-9
        assert out["weights"]["risk_pass_rate"] == 0.375

    def test_high_confidence_pipeline(self):
        """全通过 + 共识高 + 完整 → high"""
        out = pipeline_confidence(
            risk_checks=[{"name": "r", "passed": True}],
            debate={"bull_strength": 0.52, "bear_strength": 0.5},
            data_completeness=1.0,
            historical_score=80.0,
        )
        assert out["tier"] == "high"
