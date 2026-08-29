"""结构化决策报告生成器（阶段4：确定性代码，非 LLM）

汇总流水线 run 目录下的全部产物：
- step{N}_{name}.json：7 步 Agent 结论
- debate.json：多空辩论结果（B5 落盘）
- tool_calls.json：工具调用记录（阶段4 落盘）
- pipeline_state.json：终态

生成 decision_report.json（结构化）+ decision_report.md（模板渲染），
置信度用 confidence.pipeline_confidence（零 LLM 确定性因子）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from finhack_pro.backtest.confidence import pipeline_confidence

# 7 步：step 序号 → 展示名
_STEP_LABELS = {
    1: "市场分析", 2: "新闻分析", 3: "基本面分析", 4: "微观事件分析",
    5: "策略生成(多空辩论)", 6: "风控决策", 7: "交易执行",
}
_STEP_AGENTS = {
    1: "market_analyzer", 2: "news_analyzer", 3: "fundamental_analyzer",
    4: "micro_event_analyzer", 5: "strategy_generator", 6: "risk_manager", 7: "trade_executor",
}


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _step_summary(step_no: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """单步摘要：取核心结论字段，剔除超长原文"""
    summary: Dict[str, Any] = {}
    if step_no == 5 and isinstance(data, dict):
        summary = {
            "direction": data.get("direction"),
            "confidence": data.get("confidence"),
            "reasoning": str(data.get("reasoning", ""))[:500],
        }
    elif step_no == 6 and isinstance(data, dict):
        summary = {
            "approved": data.get("approved"),
            "reasoning": str(data.get("reasoning", ""))[:500],
            "checks": data.get("checks", []),
        }
    elif isinstance(data, dict):
        for key in ("summary", "conclusion", "direction", "confidence", "sentiment", "assessment", "key_findings"):
            if key in data:
                val = data[key]
                summary[key] = val if not isinstance(val, (dict, list)) else str(val)[:300]
        if not summary:
            summary = {"_keys": list(data.keys())[:10]}
    return summary


def _scan_run_dir(run_dir: str) -> Dict[str, Any]:
    """扫描 run 目录，返回原始产物字典"""
    result: Dict[str, Any] = {"steps": {}, "debate": None, "tool_calls": [], "pipeline_state": None}
    if not os.path.isdir(run_dir):
        return result

    for fname in sorted(os.listdir(run_dir)):
        fpath = os.path.join(run_dir, fname)
        if not fname.endswith(".json") or not os.path.isfile(fpath):
            continue
        if fname == "debate.json":
            result["debate"] = _load_json(fpath)
        elif fname == "tool_calls.json":
            calls = _load_json(fpath)
            result["tool_calls"] = calls if isinstance(calls, list) else []
        elif fname == "pipeline_state.json":
            result["pipeline_state"] = _load_json(fpath)
        elif fname.startswith("step") and "_" in fname:
            # step{N}_{name}.json → step_no = int(fname[4:5])
            try:
                step_no = int(fname[4])
                result["steps"][step_no] = _load_json(fpath)
            except (ValueError, IndexError):
                continue
    return result


def build_decision_report(run_dir: str) -> Dict[str, Any]:
    """构建决策报告（幂等：直接计算，不落盘——落盘由 save_decision_report 负责）

    Returns:
        decision_report.json 的完整结构
    """
    raw = _scan_run_dir(run_dir)
    steps: List[Dict[str, Any]] = []
    for step_no in sorted(raw["steps"].keys()):
        data = raw["steps"].get(step_no) or {}
        if step_no == 5 and isinstance(data, dict):
            # 策略信号（step5 json 为 StrategySignal.model_dump）
            steps.append({
                "step": step_no,
                "name": _STEP_LABELS.get(step_no, f"Step{step_no}"),
                "agent": _STEP_AGENTS.get(step_no, ""),
                "summary": _step_summary(step_no, data),
            })
        elif isinstance(data, dict):
            steps.append({
                "step": step_no,
                "name": _STEP_LABELS.get(step_no, f"Step{step_no}"),
                "agent": _STEP_AGENTS.get(step_no, ""),
                "summary": _step_summary(step_no, data),
            })

    debate = raw.get("debate")
    tool_calls = raw.get("tool_calls") or []

    # 风控逐项检查（step6 的 checks，B6 结构化）
    risk_checks: List[Dict[str, Any]] = []
    step6 = raw["steps"].get(6) or {}
    if isinstance(step6, dict) and step6.get("checks"):
        risk_checks = step6["checks"]

    # 置信度合成（零 LLM）
    confidence = pipeline_confidence(
        risk_checks=risk_checks or None,
        debate=debate or None,
        data_completeness=1.0,  # 流水线场景默认完整；如需精确可后续注入
    )

    # 符号提取：从 step1 或 debate
    symbol = ""
    step1 = raw["steps"].get(1) or {}
    if isinstance(step1, dict) and step1.get("symbol"):
        symbol = step1["symbol"]
    elif debate and debate.get("symbol"):
        symbol = debate["symbol"]

    return {
        "run_id": os.path.basename(run_dir.rstrip("/\\")),
        "symbol": symbol,
        "status": (raw.get("pipeline_state") or {}).get("status", "unknown"),
        "terminal": (raw.get("pipeline_state") or {}).get("terminal"),
        "steps": steps,
        "debate": debate,
        "tool_calls": tool_calls,
        "risk_checks": risk_checks,
        "confidence": confidence,
    }


def _fmt(v: Any, max_len: int = 200) -> str:
    """摘要值格式化：dict/list 转紧凑串，超长截断"""
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        s = str(v)
    else:
        s = str(v)
    return s if len(s) <= max_len else s[:max_len] + "…"


def _step_table(summary: Dict[str, Any]) -> List[str]:
    """单步 summary 渲染为 markdown 表格（跳过 checks 等结构化字段）"""
    rows = [(k, _fmt(v)) for k, v in summary.items() if k not in ("checks", "reasoning")]
    if not rows:
        return []
    lines = ["| 维度 | 结论 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rows]
    return lines


def _render_markdown(report: Dict[str, Any]) -> str:
    """模板渲染 decision_report.md（确定性，非 LLM）——研究报告结构：
    执行摘要 → 各步结论表格 → 多空辩论 → 风控逐项 → 风险与不确定性 →
    工具调用附录 → 置信度构成附录
    """
    lines: List[str] = []
    symbol = report.get("symbol") or report.get("run_id")
    lines.append(f"# 决策报告 — {symbol}")
    lines.append("")
    lines.append(f"> run_id: `{report.get('run_id')}` ｜ 状态: {report.get('status')}"
                 f"（终态: {report.get('terminal') or '—'}）")
    conf = report.get("confidence") or {}
    tier_cn = {"high": "高", "medium": "中", "low": "低"}.get(conf.get("tier"), conf.get("tier", "—"))
    lines.append(f"> 置信度: **{tier_cn}**（{conf.get('score', 0)}/100，确定性因子合成，零 LLM 自评）")
    lines.append("")

    # ── 执行摘要 ──────────────────────────────────────────────
    debate = report.get("debate") or {}
    step5 = next((s for s in report.get("steps", []) if s["step"] == 5), None)
    step6 = next((s for s in report.get("steps", []) if s["step"] == 6), None)
    consensus_cn = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(
        debate.get("consensus"), debate.get("consensus") or "—"
    )
    lines.append("## 执行摘要")
    direction = "—"
    if step5 and step5.get("summary"):
        direction = step5["summary"].get("direction", "—")
    lines.append(f"- **结论方向**: 多空共识 **{consensus_cn}**（策略信号: {direction}）")
    conclusion = debate.get("conclusion") or (step5 or {}).get("summary", {}).get("reasoning", "")
    lines.append(f"- **核心观点**: {_fmt(conclusion, 160)}")
    # 风险提示：风控未通过项优先，其次分歧点
    risk_hints: List[str] = []
    for c in (report.get("risk_checks") or []):
        if not c.get("passed"):
            risk_hints.append(f"风控「{c.get('name')}」未通过：{_fmt(c.get('detail'), 80)}")
    for d in (debate.get("key_debates") or [])[:2]:
        risk_hints.append(f"多空分歧：{_fmt(d, 80)}")
    lines.append(f"- **风险提示**: {'；'.join(risk_hints) if risk_hints else '未发现显性风控阻断项'}")
    if report.get("risk_checks") is not None:
        passed_n = sum(1 for c in report["risk_checks"] if c.get("passed"))
        lines.append(f"- **风控通过**: {passed_n}/{len(report['risk_checks'])} 项")
    lines.append("")

    # ── 各步结论（表格化）────────────────────────────────────
    lines.append("## 分析链路（7 步结论）")
    lines.append("")
    for s in report.get("steps", []):
        lines.append(f"### {s['step']}. {s['name']}")
        lines.append(f"`{s['agent']}`")
        summary = s.get("summary") or {}
        if not summary:
            lines.append("> 该步未产出关键结论")
        else:
            reasoning = summary.get("reasoning")
            tbl = _step_table(summary)
            if tbl:
                lines += tbl
            if reasoning:
                lines.append("")
                lines.append(f"**推理**: {_fmt(reasoning, 300)}")
        lines.append("")

    # ── 多空辩论 ─────────────────────────────────────────────
    if debate:
        lines.append("## 多空辩论")
        lines.append("")
        lines.append("| 维度 | 数值 |")
        lines.append("|---|---|")
        lines.append(f"| 多头强度 | {debate.get('bull_strength', 0):.2f} |")
        lines.append(f"| 空头强度 | {debate.get('bear_strength', 0):.2f} |")
        lines.append(f"| 共识方向 | {consensus_cn} |")
        lines.append(f"| 共识置信度 | {debate.get('confidence', 0):.2f} |")
        lines.append("")
        for label, key in (("多头核心论点", "bull_arguments"), ("空头核心论点", "bear_arguments")):
            args = debate.get(key) or []
            if args:
                lines.append(f"**{label}**:")
                for a in args[:6]:
                    lines.append(f"- {_fmt(a, 120)}")
                lines.append("")
        if debate.get("key_debates"):
            lines.append("**关键争议点**:")
            for kd in debate["key_debates"][:5]:
                lines.append(f"- {_fmt(kd, 120)}")
            lines.append("")
        if debate.get("conclusion"):
            lines.append(f"**综合结论**: {_fmt(debate['conclusion'], 200)}")
            lines.append("")

    # ── 风控逐项（表格化）────────────────────────────────────
    checks = report.get("risk_checks") or []
    if checks:
        lines.append("## 风控逐项检查")
        lines.append("")
        lines.append("| 检查项 | 结果 | 说明 |")
        lines.append("|---|---|---|")
        for c in checks:
            mark = "✅ 通过" if c.get("passed") else "❌ 未通过"
            lines.append(f"| {c.get('name')} | {mark} | {_fmt(c.get('detail'), 100)} |")
        lines.append("")

    # ── 风险与不确定性 ───────────────────────────────────────
    lines.append("## 风险与不确定性")
    if risk_hints:
        for r in risk_hints:
            lines.append(f"- ⚠ {r}")
    else:
        lines.append("- 当前未识别到显著风险点（基于确定性规则检查）")
    completeness = (conf.get("factors") or {}).get("data_completeness")
    if completeness is not None:
        lines.append(f"- 数据完整度: {completeness * 100:.0f}%（影响置信度下界）")
    lines.append("- 本报告由确定性规则生成，不构成投资建议")
    lines.append("")

    # ── 工具调用（附录）──────────────────────────────────────
    calls = report.get("tool_calls") or []
    if calls:
        lines.append("## 附录 A · 工具调用记录")
        lines.append("")
        for c in calls[:30]:
            lines.append(
                f"- `{c.get('tool_name')}` by {c.get('caller')} "
                f"[{'✓' if c.get('success') else '✗'}] args={_fmt(c.get('args'), 100)} "
                f"→ {_fmt(c.get('return_summary'), 120)}"
            )
        if len(calls) > 30:
            lines.append(f"- …等共 {len(calls)} 次调用")
        lines.append("")

    # ── 置信度构成（附录）────────────────────────────────────
    if conf.get("factors"):
        lines.append("## 附录 B · 置信度构成")
        lines.append("")
        lines.append("| 因子 | 权重 | 值 |")
        lines.append("|---|---|---|")
        weights = conf.get("weights") or {}
        for name, w in weights.items():
            val = conf["factors"].get(name)
            lines.append(f"| {name} | {w:.2f} | {_fmt(val, 60)} |")
        lines.append("")

    return "\n".join(lines)


def save_decision_report(run_dir: str) -> Dict[str, Any]:
    """构建并落盘 decision_report.json + decision_report.md

    Returns:
        {report, json_path, md_path}；失败时 report 含 error
    """
    try:
        report = build_decision_report(run_dir)
        json_path = os.path.join(run_dir, "decision_report.json")
        md_path = os.path.join(run_dir, "decision_report.md")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_render_markdown(report))
        return {"report": report, "json_path": json_path, "md_path": md_path}
    except Exception as e:
        return {"error": str(e), "report": {}}
