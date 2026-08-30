"""
P0 回归测试：Agent 工具层三处缺陷修复。

覆盖：
1. 微观事件 Agent 龙虎榜字段名与 FetchDragonTigerTool 不对齐
   （曾误读 buy_amount/sell_amount，导致 net_buy 恒 0、方向恒 negative）。
2. CalculateIndicatorTool 只返回静态释义文本、从不返回指标数值。
3. 情感词典在 tool_registry 内两份重复定义（易漂移）。

原则（SDD）：工具取不到数据时显式返回"不可用"，绝不伪造数值兜底。
"""

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import pytest

from finhack_pro.agents.micro_event_agent import MicroEventAgent, MicroEventType
from finhack_pro.agents.tool_registry import (
    AnalyzeSentimentTool,
    CalculateIndicatorTool,
    ToolRegistry,
    _classify_sentiment,
    create_default_toolkit,
)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _StubToolRegistry:
    """仅返回预设龙虎榜记录的 ToolRegistry 替身"""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._records = records
        self.calls: List[str] = []

    async def call_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        caller_agent_id: str = "test",
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.calls.append(tool_name)
        if tool_name != "fetch_dragon_tiger":
            return {"success": False, "error": f"未预期的工具调用: {tool_name}"}
        return {"success": True, "result": {"records": self._records}}


class _StubFetcher:
    """仅返回预设日线 DataFrame 的 DataFetcher 替身"""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def get_daily(
        self,
        symbol: str,
        start_date: str = "",
        end_date: str = "",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        return self._df


def _make_agent(records: List[Dict[str, Any]]) -> MicroEventAgent:
    return MicroEventAgent(
        config={"provider": "openai", "api_key": "", "model": "test"},
        shared_memory=None,
        tool_registry=_StubToolRegistry(records),
    )


def _make_ohlcv(bars: int = 200, seed: int = 7) -> pd.DataFrame:
    """构造确定性 OHLCV 日线，避免测试依赖联网行情"""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, bars))
    close = np.maximum(close, 1.0)
    high = close + np.abs(rng.normal(0, 0.5, bars))
    low = close - np.abs(rng.normal(0, 0.5, bars))
    open_ = close + rng.normal(0, 0.3, bars)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=bars, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, bars).astype(float),
        }
    )


# ---------------------------------------------------------------------------
# 1. 龙虎榜字段对齐
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dragon_tiger_net_buy_positive():
    """净买入 > 0 → impact_direction = positive"""
    agent = _make_agent([{"date": "2026-08-01", "net_buy": 2.5e8,
                          "total_buy": 4.0e8, "total_sell": 1.5e8}])
    events = await agent._scan_dragon_tiger("600519", 30)

    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == MicroEventType.DRAGON_TIGER
    assert ev.impact_direction == "positive"
    assert ev.impact_level == "high"          # |net_buy| = 2.5e8 > 1e8
    assert "净买入" in ev.title
    assert "2.50亿" in ev.title


@pytest.mark.asyncio
async def test_dragon_tiger_net_sell_negative():
    """净卖出 < 0 → impact_direction = negative"""
    agent = _make_agent([{"date": "2026-08-01", "net_buy": -3.0e8,
                          "total_buy": 1.0e8, "total_sell": 4.0e8}])
    events = await agent._scan_dragon_tiger("600519", 30)

    assert len(events) == 1
    ev = events[0]
    assert ev.impact_direction == "negative"
    assert ev.impact_level == "high"
    assert "净卖出" in ev.title


@pytest.mark.asyncio
async def test_dragon_tiger_zero_net_is_neutral():
    """净额为 0 → neutral（修复前因 `net_buy > 0` 为 False 而误判 negative）"""
    agent = _make_agent([{"date": "2026-08-01", "net_buy": 0.0,
                          "total_buy": 1.0e8, "total_sell": 1.0e8}])
    events = await agent._scan_dragon_tiger("600519", 30)

    assert len(events) == 1
    ev = events[0]
    assert ev.impact_direction == "neutral"
    assert ev.impact_level == "medium"


@pytest.mark.asyncio
async def test_dragon_tiger_missing_net_buy_falls_back_to_totals():
    """数据源未提供 net_buy 时，用 total_buy - total_sell 推导，不得退化为 0"""
    agent = _make_agent([{"date": "2026-08-01",
                          "total_buy": 5.0e8, "total_sell": 2.0e8}])
    events = await agent._scan_dragon_tiger("600519", 30)

    assert len(events) == 1
    ev = events[0]
    assert ev.impact_direction == "positive"
    assert "3.00亿" in ev.title      # 净额 3 亿


@pytest.mark.asyncio
async def test_dragon_tiger_ignores_legacy_field_names():
    """仅存在历史错误字段 buy_amount/sell_amount 时，不得再被当作有效净额"""
    agent = _make_agent([{"date": "2026-08-01",
                          "buy_amount": 4.0e8, "sell_amount": 1.0e8}])
    events = await agent._scan_dragon_tiger("600519", 30)

    assert len(events) == 1
    # 三个字段均缺失 → 净额 0 → neutral（而非修复前的 negative）
    assert events[0].impact_direction == "neutral"


# ---------------------------------------------------------------------------
# 2. CalculateIndicatorTool 真实计算
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_calculate_indicator_returns_real_values():
    """注入数据源后必须返回真实指标数值，而非释义文本"""
    tool = CalculateIndicatorTool(data_fetcher=_StubFetcher(_make_ohlcv()))
    out = await tool.execute(symbol="600519", indicator="rsi", period="14")

    assert out["available"] is True
    assert out["symbol"] == "600519"
    assert "rsi" in out["values"]
    rsi = out["values"]["rsi"]
    assert rsi is not None, "RSI 不应为 None（200 根 K 线足以计算 14 日 RSI）"
    assert 0.0 <= rsi <= 100.0, f"RSI 应落在 0-100，实际 {rsi}"
    # 释义作为辅助信息保留，但不得是唯一返回内容
    assert out["interpretation"] == "RSI>70超买, RSI<30超卖, 50为多空分界"


@pytest.mark.asyncio
async def test_calculate_indicator_macd_values():
    tool = CalculateIndicatorTool(data_fetcher=_StubFetcher(_make_ohlcv()))
    out = await tool.execute(symbol="600519", indicator="macd")

    assert out["available"] is True
    for col in ("macd", "macd_signal", "macd_hist"):
        assert out["values"].get(col) is not None, f"{col} 应有真实数值"


@pytest.mark.asyncio
async def test_calculate_indicator_all_returns_every_family():
    tool = CalculateIndicatorTool(data_fetcher=_StubFetcher(_make_ohlcv()))
    out = await tool.execute(symbol="600519", indicator="all")

    assert out["available"] is True
    for col in ("rsi", "macd", "bb_upper", "ma_20", "atr", "obv", "k"):
        assert col in out["values"], f"all 应包含 {col}"
        assert out["values"][col] is not None, f"{col} 应有真实数值"
    assert set(out["interpretation"]) >= {"rsi", "macd", "kdj"}


@pytest.mark.asyncio
async def test_calculate_indicator_without_fetcher_is_explicit():
    """无数据源时显式返回不可用，绝不伪造数值（SDD 禁止 mock 兜底）"""
    tool = CalculateIndicatorTool()  # 未注入 data_fetcher
    out = await tool.execute(symbol="600519", indicator="rsi")

    assert out["available"] is False
    assert out["values"] == {}
    assert "数据源未配置" in out["note"]


@pytest.mark.asyncio
async def test_calculate_indicator_empty_data_is_explicit():
    """行情为空时显式说明，不返回 0 值"""
    tool = CalculateIndicatorTool(data_fetcher=_StubFetcher(pd.DataFrame()))
    out = await tool.execute(symbol="600519", indicator="rsi")

    assert out["available"] is False
    assert out["values"] == {}


@pytest.mark.asyncio
async def test_calculate_indicator_unknown_name():
    tool = CalculateIndicatorTool(data_fetcher=_StubFetcher(_make_ohlcv()))
    out = await tool.execute(symbol="600519", indicator="not_an_indicator")

    assert "error" in out
    assert "rsi" in out["available"]


# ---------------------------------------------------------------------------
# 3. 情感词典单一来源
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sentiment_tool_matches_module_classifier():
    """AnalyzeSentimentTool 与 _classify_sentiment 必须一致（词典单一来源）"""
    tool = AnalyzeSentimentTool()
    samples = [
        "公司业绩增长超预期，管理层增持",
        "营收下滑不及预期，遭大股东减持",
        "公司召开董事会",
        "",
    ]
    for text in samples:
        out = await tool.execute(text=text)
        assert out["sentiment"] == _classify_sentiment(text), (
            f"情感判定不一致: {text!r} → tool={out['sentiment']}, "
            f"classifier={_classify_sentiment(text)}"
        )


@pytest.mark.asyncio
async def test_sentiment_keyword_coverage():
    tool = AnalyzeSentimentTool()
    pos = await tool.execute(text="净利润创新高，股价突破平台")
    neg = await tool.execute(text="涉嫌违规被立案调查，股价暴跌")

    assert pos["sentiment"] == "positive"
    assert neg["sentiment"] == "negative"
    assert pos["method"] == "keyword_based"


# ---------------------------------------------------------------------------
# 4. 工具集装配：data_fetcher 必须透传
# ---------------------------------------------------------------------------
def test_create_default_toolkit_passes_fetcher_to_indicator_tool():
    """create_default_toolkit 必须把 data_fetcher 透传给 CalculateIndicatorTool，
    否则该工具恒返回"数据源未配置"（修复前的实际状态）。"""
    fetcher = _StubFetcher(_make_ohlcv())
    registry = create_default_toolkit(data_fetcher=fetcher)

    tool = registry.get_tool("calculate_indicator")
    assert tool is not None
    assert tool._fetcher is fetcher
