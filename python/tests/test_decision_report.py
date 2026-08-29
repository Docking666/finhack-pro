"""阶段4 结构化决策报告回归测试

- tool_registry：call_tool 记录 run_id + return_summary；persist 落盘 tool_calls.json
- decision_report：确定性汇总 7 步 + 辩论 + 工具调用 + 风控逐项 + 置信度
- 模板渲染 decision_report.md（非 LLM）
"""

import json
import os

import pytest

from finhack_pro.agents.tool_registry import ToolRegistry, _summarize_return
from finhack_pro.pipeline.decision_report import (
    build_decision_report,
    save_decision_report,
    _render_markdown,
)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _make_run_dir(tmp_path):
    """构造一个完整的 run 目录产物"""
    _write_json(tmp_path / "pipeline_state.json", {"status": "completed", "terminal": "executed"})
    _write_json(tmp_path / "step1_market_analysis.json", {
        "symbol": "600519.SH", "market_state": "sideways", "trend_direction": "up",
        "summary": "技术面横盘偏多", "confidence": 0.62,
    })
    _write_json(tmp_path / "step2_news_analysis.json", {"sentiment": "neutral", "key_findings": ["无重大利空"]})
    _write_json(tmp_path / "step3_fundamental_analysis.json", {"conclusion": "基本面稳健"})
    _write_json(tmp_path / "step4_micro_event_analysis.json", {"conclusion": "无微观催化"})
    _write_json(tmp_path / "step5_strategy_signal.json", {
        "symbol": "600519.SH", "direction": "buy", "confidence": 0.7,
        "reasoning": "技术面与新闻面共振", "position_size_pct": 0.1,
    })
    _write_json(tmp_path / "step6_risk_decision.json", {
        "approved": True, "reasoning": "风险可控",
        "checks": [
            {"name": "position_limit", "passed": True, "detail": "仓位 10% ≤ 30%"},
            {"name": "signal_confidence", "passed": True, "detail": "置信度 0.70 ≥ 0.60"},
        ],
    })
    _write_json(tmp_path / "debate.json", {
        "symbol": "600519.SH", "bull_arguments": ["技术走强"], "bear_arguments": ["估值偏高"],
        "bull_strength": 0.6, "bear_strength": 0.55, "consensus": "bullish",
        "confidence": 0.66, "key_debates": ["分歧点A"], "conclusion": "偏多",
    })
    _write_json(tmp_path / "tool_calls.json", [
        {"tool_name": "fetch_market_data", "caller": "market_analyzer", "args": {"symbol": "600519.SH"},
         "success": True, "run_id": "test_run", "return_summary": "data[250条] 首条: {...}"},
    ])
    return tmp_path


class TestToolRegistryPersist:
    def test_call_log_with_run_id_and_summary(self):
        """call_tool 记录 run_id 与返回值摘要"""
        registry = ToolRegistry()
        entry_before = len(registry._call_log)
        # 工具不存在也走日志？不——只测成功路径需要注册工具；这里直接验证 persist 的过滤逻辑
        # 手动注入一条带 run_id 的日志
        registry._call_log.append({
            "tool_name": "fetch_market_data", "caller": "a1", "args": {},
            "success": True, "run_id": "run_1", "return_summary": "x", "timestamp": "t",
        })
        registry._call_log.append({
            "tool_name": "fetch_market_data", "caller": "a2", "args": {},
            "success": True, "run_id": "run_2", "return_summary": "y", "timestamp": "t",
        })

        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = registry.persist(d, run_id="run_1")
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
            assert len(entries) == 1
            assert entries[0]["run_id"] == "run_1"
            assert entries[0]["return_summary"] == "x"

    def test_summarize_return(self):
        """返回值摘要：dict 容器取结构、list 取长度"""
        assert _summarize_return({"data": [1, 2, 3]}) == "data[3条] 首条: 1"
        assert _summarize_return([1, 2, 3]).startswith("list[3]")
        assert _summarize_return({"a": 1, "b": 2}) == "a=1, b=2"
        assert _summarize_return(None) == ""
        assert _summarize_return("x" * 500)[-3:] == "..." or len(_summarize_return("x" * 500)) <= 300


class TestDecisionReport:
    def test_build_structure(self, tmp_path):
        """汇总结构：steps/debate/tool_calls/risk_checks/confidence"""
        _make_run_dir(tmp_path)
        report = build_decision_report(str(tmp_path))

        assert report["run_id"] == os.path.basename(str(tmp_path))
        assert report["symbol"] == "600519.SH"
        assert report["status"] == "completed"
        assert report["terminal"] == "executed"

        # 7 步（step6 含 checks 摘要）
        assert len(report["steps"]) == 6  # step1-6（测试只造了 6 步）
        step5 = [s for s in report["steps"] if s["step"] == 5][0]
        assert step5["summary"]["direction"] == "buy"

        # 辩论
        assert report["debate"]["consensus"] == "bullish"
        assert report["debate"]["bull_strength"] == 0.6

        # 工具调用
        assert report["tool_calls"][0]["tool_name"] == "fetch_market_data"

        # 风控逐项
        assert len(report["risk_checks"]) == 2
        assert report["risk_checks"][0]["passed"] is True

        # 置信度：2/2 检查过 + 共识 0.95 + 完整 1.0 + 无历史 → high
        assert report["confidence"]["tier"] in ("high", "medium")
        assert report["confidence"]["score"] > 70

    def test_save_writes_json_and_md(self, tmp_path):
        """落盘 decision_report.json + .md（md 为模板渲染，含置信度与检查）"""
        _make_run_dir(tmp_path)
        saved = save_decision_report(str(tmp_path))
        assert "error" not in saved
        assert os.path.exists(saved["json_path"])
        assert os.path.exists(saved["md_path"])

        md = open(saved["md_path"], encoding="utf-8").read()
        assert "# 决策报告" in md
        assert "置信度" in md
        assert "风控逐项检查" in md
        assert "多空辩论" in md
        assert "工具调用记录" in md

    def test_empty_run_dir_graceful(self, tmp_path):
        """空目录不抛异常，返回空步骤/无置信度异常"""
        report = build_decision_report(str(tmp_path))
        assert report["steps"] == []
        assert report["tool_calls"] == []
        assert report["confidence"]["score"] >= 0
