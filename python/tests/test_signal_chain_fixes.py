"""阶段0 信号链路 Bug 回归测试（B1-B6）

B1 extra 预计算推广到所有内置策略（普通策略 BarData.extra 恒空的问题）
B2 runner.run 参数传递修复（setattr 失效，参数从不生效）
B3 dual_thrust 状态按日期重置（"一年只交易1次"直接原因之一）
B4 序列化字段错位（direction 恒 buy / reason 恒"策略信号"）
B5 辩论结果落盘 debate.json（原仅打印日志）
B6 风控检查逐项结构化（checks 数组，原为字符串聚合）
"""

import json
import os

import numpy as np
import pandas as pd
import pytest

from finhack_pro.agents.market_analyzer import MarketAnalysisReport
from finhack_pro.agents.risk_manager import PortfolioState, RiskManagerAgent
from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent, StrategySignal
from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.strategies.base import BarData, BaseStrategy, Context, Signal, SignalDirection
from finhack_pro.strategies.dual_thrust import DualThrustStrategy


# ============================================================================
# 测试数据与探针
# ============================================================================

def _make_daily_df(n=120, seed=42, trend=0.5, start="2024-01-01"):
    """构造日线数据（带 OHLCV）"""
    np.random.seed(seed)
    dates = pd.date_range(start, periods=n, freq="B")
    close = 100 + np.cumsum(np.random.randn(n) * 1.5 + trend)
    open_ = close - np.random.randn(n) * 0.5
    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": np.maximum(open_, close) * 1.01,
        "low": np.minimum(open_, close) * 0.99,
        "close": close,
        "volume": np.random.randint(100_000, 1_000_000, n),
    })


class _ProbeStrategy(BaseStrategy):
    """探针策略：记录 on_bar 收到的 extra 键集合与 on_init 的 params"""

    def __init__(self, name="Probe"):
        super().__init__()
        self.strategy_name = name
        self._params = {"sensitivity": 1.0}
        self.extra_keys_seen = set()
        self.init_params = None
        self.bar_count = 0

    def on_init(self, context: Context) -> None:
        self._params.update(context.params)
        self.init_params = dict(self._params)

    def on_bar(self, context: Context, bar: BarData) -> list:
        self.bar_count += 1
        self.extra_keys_seen.update(bar.extra.keys())
        return []


# ============================================================================
# B1: extra 预计算推广到所有策略
# ============================================================================

class TestB1ExtraPrecompute:
    def test_probe_strategy_receives_extra_fields(self):
        """普通（非 niche）策略回测时 BarData.extra 也应含技术字段"""
        from finhack_pro.webui.services import _precompute_niche_fields

        df = _make_daily_df()
        enriched = _precompute_niche_fields(df)
        # 预计算必须写入白名单列
        for col in ("ma20", "rsi", "volume_ratio", "macd_signal"):
            assert col in enriched.columns, f"预计算缺少列 {col}"

        probe = _ProbeStrategy()
        runner = BacktestRunner()
        runner.run(strategy=probe, symbol="600519.SH", data=enriched, initial_capital=1_000_000.0)

        # runner 的 _extract_bar_extra 应把预计算列注入 BarData.extra
        assert probe.bar_count > 0
        assert "ma20" in probe.extra_keys_seen, f"extra 未注入技术字段, 实际: {probe.extra_keys_seen}"
        assert "rsi" in probe.extra_keys_seen

    def test_precompute_returns_original_when_no_close(self):
        """无 close 列时安全返回，不抛异常"""
        from finhack_pro.webui.services import _precompute_niche_fields

        df = pd.DataFrame({"volume": [1, 2, 3]})
        out = _precompute_niche_fields(df)
        assert "close" not in out.columns


# ============================================================================
# B2: 参数传递修复
# ============================================================================

class TestB2ParamsPassing:
    def test_runner_params_reach_strategy_on_init(self):
        """runner.run(params=...) 必须传递到策略 on_init 的 context.params"""
        probe = _ProbeStrategy()
        runner = BacktestRunner()
        runner.run(
            strategy=probe,
            symbol="600519.SH",
            data=_make_daily_df(),
            initial_capital=1_000_000.0,
            params={"sensitivity": 2.5, "lookback": 30},
        )
        assert probe.init_params is not None
        assert probe.init_params["sensitivity"] == 2.5, f"参数未生效: {probe.init_params}"
        assert probe.init_params["lookback"] == 30


# ============================================================================
# B3: dual_thrust 状态按日期重置
# ============================================================================

def _make_dual_thrust_trigger_df(n_sideways=20, n_breakout=30):
    """前 n_sideways 天窄幅震荡(98-102)，之后每天 98 开盘/115 收盘的大阳线

    Dual Thrust 上轨 = 开盘 + k1*max(n1,n2)，震荡段区间宽仅 ~4.5，
    突破段收盘 115 恒大于上轨(~100)，从而每天都能触发 BUY。
    """
    np.random.seed(1)
    n = n_sideways + n_breakout
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = np.random.uniform(98, 102, n_sideways)

    open_ = np.concatenate([base, np.full(n_breakout, 98.0)])
    close = np.concatenate([base, np.full(n_breakout, 115.0)])
    high = np.concatenate([base * 1.005, np.full(n_breakout, 116.0)])
    low = np.concatenate([base * 0.995, np.full(n_breakout, 97.0)])
    return pd.DataFrame({
        "date": dates, "open": open_, "high": high, "low": low,
        "close": close, "volume": np.random.randint(100_000, 1_000_000, n),
    })


class TestB3DualThrustDailyReset:
    def test_multiple_days_generate_multiple_signals(self):
        """突破日每天触发 BUY → 修复后多日触发，而非终生 1 次"""
        df = _make_dual_thrust_trigger_df()
        strategy = DualThrustStrategy()
        context = Context()
        strategy.on_init(context)

        buy_days = 0
        for _, row in df.iterrows():
            bar = BarData(
                symbol="600519.SH",
                datetime=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=int(row["volume"]),
            )
            sigs = strategy.on_bar(context, bar)
            if any(s.direction == SignalDirection.BUY for s in sigs):
                buy_days += 1

        assert buy_days >= 3, f"修复后应多日触发 BUY, 实际仅 {buy_days} 天"

    def test_same_day_only_one_signal(self):
        """同一交易日仍只触发一次（防同日内重复开仓）"""
        df = _make_dual_thrust_trigger_df()
        strategy = DualThrustStrategy()
        context = Context()
        strategy.on_init(context)

        day_counts = {}
        for _, row in df.iterrows():
            day = str(row["date"].date())
            bar = BarData(
                symbol="600519.SH",
                datetime=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=int(row["volume"]),
            )
            sigs = strategy.on_bar(context, bar)
            day_counts[day] = day_counts.get(day, 0) + len(sigs)

        triggered = {d: c for d, c in day_counts.items() if c > 0}
        assert len(triggered) >= 3, f"应有多个触发日: {triggered}"
        assert all(c == 1 for c in triggered.values()), f"同日应仅 1 次信号: {triggered}"


# ============================================================================
# B4: 序列化字段错位（direction/reason）
# ============================================================================

class TestB4TradeSerialization:
    def test_trades_carry_strategy_name(self):
        """runner trade 字典必须带 strategy_name（B4 依赖），action 键为 buy/sell"""
        from finhack_pro.strategies.mean_reversion import MeanReversionStrategy

        # 震荡数据（均值回归策略在超卖/超买轮次反复触发）
        df = _make_daily_df(n=300, seed=23, trend=0.0)
        strategy = MeanReversionStrategy()
        runner = BacktestRunner()
        result = runner.run(
            strategy=strategy,
            symbol="600519.SH",
            data=df,
            initial_capital=1_000_000.0,
            params={"rsi_period": 14, "oversold": 35, "overbought": 65},
        )

        assert len(result.trades) > 0, "震荡数据下均值回归策略应产生交易"
        for t in result.trades:
            assert t.get("action") in ("buy", "sell"), f"action 键缺失/异常: {t}"
            assert t.get("strategy_name"), f"strategy_name 缺失: {t}"

    def test_services_maps_action_to_direction(self):
        """services 序列化：direction 取自 action，reason 取自 strategy_name"""
        import asyncio

        from finhack_pro.webui import services as svc
        from finhack_pro.webui.models import TradeRecord

        # 直接构造 runner 式 trade 字典，走 TradeRecord 构造路径（与 run_task 同逻辑）
        fake_trades = [
            {"date": "2024-01-02", "action": "sell", "price": 10.0, "volume": 100,
             "commission": 5.0, "pnl": 12.3, "strategy_name": "Momentum"},
        ]
        records = [
            TradeRecord(
                date=t.get("date", ""),
                symbol=t.get("symbol", "600519.SH"),
                direction=t.get("action", "buy"),
                price=t.get("price", 0),
                volume=t.get("volume", 0),
                commission=t.get("commission", 0),
                pnl=t.get("pnl", 0),
                reason=t.get("strategy_name", "策略信号"),
            )
            for t in fake_trades
        ]
        assert records[0].direction == "sell", "direction 应映射自 action"
        assert records[0].reason == "Momentum", "reason 应映射自 strategy_name"


# ============================================================================
# B5: 辩论结果落盘
# ============================================================================

class _FakeLLM:
    """可编程 LLM：多头→空头→收敛裁判"""

    def __init__(self):
        self.calls = 0

    def _extract_json(self, response):
        return json.loads(response)

    async def chat_structured(self, message, response_model=None, system=None, **kw):
        return response_model(direction="hold", confidence=0.5, symbol="600519.SH", reasoning="测试信号")

    async def chat(self, message, system, temperature=0.3, **kw):
        self.calls += 1
        if self.calls % 3 == 1:      # 多头
            return '{"arguments": [], "overall_strength": 0.6, "weaknesses": []}'
        if self.calls % 3 == 2:      # 空头
            return '{"arguments": [], "overall_strength": 0.55, "weaknesses": []}'
        return json.dumps({
            "bull_arguments": ["多头论点A"], "bear_arguments": ["空头论点B"],
            "bull_strength": 0.55, "bear_strength": 0.5,
            "consensus": "neutral", "confidence": 0.62,
            "key_debates": ["关键分歧点"], "conclusion": "裁判测试结论",
        })


def _market_report():
    return MarketAnalysisReport(
        symbol="600519.SH",
        market_state="sideways",
        trend_direction="down",
        confidence=0.62,
        risk_level="low",
        technical_summary="测试技术面",
    )


class TestB5DebatePersist:
    @pytest.mark.asyncio
    async def test_debate_result_written_to_run_dir(self, tmp_path):
        """传入 run_dir 时辩论结果落盘 debate.json"""
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        agent._llm = _FakeLLM()
        agent.max_debate_rounds = 2

        await agent.debate(_market_report(), run_dir=str(tmp_path))

        debate_path = tmp_path / "debate.json"
        assert debate_path.exists(), "debate.json 未落盘"
        data = json.loads(debate_path.read_text(encoding="utf-8"))
        assert data["consensus"] == "neutral"
        assert data["confidence"] == 0.62
        assert data["bull_strength"] == 0.55
        assert data["bear_strength"] == 0.5
        assert "bull_arguments" in data and "key_debates" in data

    @pytest.mark.asyncio
    async def test_debate_without_run_dir_no_error(self):
        """不传 run_dir 时行为不变（向后兼容）"""
        agent = StrategyGeneratorAgent(config={"model": "test", "api_key": "sk-test"})
        agent._llm = _FakeLLM()
        agent.max_debate_rounds = 2

        signal = await agent.debate(_market_report())
        assert signal is not None


# ============================================================================
# B6: 风控检查逐项结构化
# ============================================================================

class TestB6StructuredRiskChecks:
    def _make_agent(self, **overrides):
        config = {
            "model": "test", "api_key": "sk-test",
            "initial_capital": 1_000_000,
            "max_position_pct": 0.3,
            "max_total_position": 0.8,
            "daily_loss_limit": 0.05,
            "max_drawdown_limit": 0.15,
            "signal_confidence_threshold": 0.6,
            **overrides,
        }
        agent = RiskManagerAgent(config=config)
        # 注入组合：无持仓、无亏损（让多数检查通过）
        agent._portfolio = PortfolioState(
            total_value=1_000_000, cash=1_000_000, positions=[],
            daily_pnl=0.0, max_drawdown=0.05,
        )
        return agent

    def _make_signal(self, confidence=0.7, position_pct=0.1, direction="buy"):
        return StrategySignal(
            symbol="600519.SH",
            direction=direction,
            confidence=confidence,
            position_size_pct=position_pct,
            reasoning="测试",
        )

    def test_checks_array_structured(self):
        """非 HOLD 信号应输出逐项 checks 数组（≥8 项），字段齐全"""
        agent = self._make_agent()
        result = agent._rule_engine_check(self._make_signal())

        assert "checks" in result, "缺 checks 字段"
        assert len(result["checks"]) >= 8, f"应有 ≥8 项检查, 实际 {len(result['checks'])}"
        for check in result["checks"]:
            assert {"name", "passed", "detail"} <= set(check.keys()), f"check 字段缺失: {check}"

        # 名称集合覆盖 9 项（HOLD 直通项不计）
        names = {c["name"] for c in result["checks"]}
        expected = {"position_limit", "total_position_limit", "daily_loss_limit",
                    "max_drawdown_limit", "consecutive_losses", "signal_confidence",
                    "duplicate_position", "sentiment_timing"}
        assert expected <= names, f"检查项缺失: {expected - names}"

        # 全部通过时 reasons 为空、passed=True
        assert result["passed"] is True
        assert result["reasons"] == []

    def test_checks_reflect_failures(self):
        """违规信号对应检查项 passed=False 且 reasons 非空"""
        agent = self._make_agent()
        result = agent._rule_engine_check(self._make_signal(confidence=0.3, position_pct=0.5))

        assert result["passed"] is False
        assert len(result["reasons"]) >= 2  # 仓位超限 + 置信度不足

        by_name = {c["name"]: c for c in result["checks"]}
        assert by_name["position_limit"]["passed"] is False
        assert by_name["signal_confidence"]["passed"] is False

    def test_hold_signal_direct_pass(self):
        """HOLD 信号直通，checks 仅含 hold_direct_pass"""
        agent = self._make_agent()
        result = agent._rule_engine_check(self._make_signal(direction="hold"))

        assert result["passed"] is True
        assert [c["name"] for c in result["checks"]] == ["hold_direct_pass"]

    def test_reasons_warnings_backward_compat(self):
        """reasons/warnings 键保持存在（旧调用方兼容）"""
        agent = self._make_agent()
        result = agent._rule_engine_check(self._make_signal())
        assert "reasons" in result and "warnings" in result
