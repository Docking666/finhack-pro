"""选股筛选层的回归测试

三条核心不变量：

1. **LLM 只在编译期出场一次**。编译器产出声明式 FilterSpec，
   执行引擎完全确定性、不含任何 LLM 调用。
2. **编译失败必须可见**。非法字段进入 unresolved，绝不静默丢弃 ——
   "用户提了三个条件只执行两个"是最危险的失效模式。
3. **因子算不出 ≠ 条件不满足**。算不出是 unavailable（计入 skipped），
   把两者混同会让股票池系统性剔除新上市 / 长期停牌标的。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from finhack_pro.screening import (
    CompileError,
    Condition,
    ConditionCompiler,
    FactorRegistry,
    FilterSpec,
    ScreenEngine,
    SpecError,
    build_default_factor_registry,
    chat_fn_from_llm_client,
    register_level_factors,
)

# ============================================================================
# 测试数据
# ============================================================================


def _ohlcv(
    n: int = 80,
    trend: float = 0.0,
    seed: int = 1,
    base: float = 10.0,
    volume: float = 2e6,
) -> pd.DataFrame:
    """构造 OHLCV。trend>0 表示上涨趋势，volume 控制量能水平。"""
    rng = np.random.default_rng(seed)
    close = base + trend * np.arange(n) + np.cumsum(rng.normal(0, 0.05, n))
    high = close + np.abs(rng.normal(0.04, 0.01, n))
    low = close - np.abs(rng.normal(0.04, 0.01, n))
    vol = volume * (1 + rng.normal(0, 0.05, n))
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=n),
            "open": (high + low) / 2,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
    )


# ============================================================================
# 因子注册表
# ============================================================================


def test_default_registry_has_builtin_factors():
    reg = build_default_factor_registry()
    for name in ("close", "ret_20", "ma20", "vol_ratio_5_20", "above_ma20_days"):
        assert name in reg


def test_register_disposer_restores_absence():
    reg = build_default_factor_registry()
    dispose = reg.register("tmp", "临时因子", lambda df: 1.0)
    assert "tmp" in reg
    dispose()
    assert "tmp" not in reg


def test_register_disposer_restores_previous():
    reg = build_default_factor_registry()
    original = reg.spec("close")
    dispose = reg.register("close", "覆盖版", lambda df: 99.0, replace=True)
    assert reg.compute("close", _ohlcv()) == 99.0
    dispose()
    assert reg.spec("close") is original


def test_duplicate_registration_rejected():
    reg = build_default_factor_registry()
    with pytest.raises(ValueError, match="已注册"):
        reg.register("close", "重复", lambda df: 1.0)


def test_empty_name_and_non_callable_rejected():
    reg = FactorRegistry()
    with pytest.raises(ValueError, match="不能为空"):
        reg.register("  ", "x", lambda df: 1.0)
    with pytest.raises(ValueError, match="不可调用"):
        reg.register("bad", "x", "not-callable")


def test_name_is_case_insensitive():
    reg = FactorRegistry()
    reg.register("MyFactor", "x", lambda df: 1.0)
    assert "myfactor" in reg


def test_compute_returns_nan_when_history_insufficient():
    """历史不足 -> NaN（不可用），而非 0 或抛异常。"""
    reg = build_default_factor_registry()
    short = _ohlcv(n=10)
    assert np.isnan(reg.compute("ret_60", short))
    assert np.isnan(reg.compute("ma60", short))


def test_compute_returns_nan_when_column_missing():
    """缺列 -> NaN。绝不伪造 0 冒充真实值。"""
    reg = build_default_factor_registry()
    assert np.isnan(reg.compute("amount", _ohlcv()))


def test_compute_unknown_factor_raises():
    reg = build_default_factor_registry()
    with pytest.raises(KeyError):
        reg.compute("nope", _ohlcv())


def test_compute_exception_becomes_nan():
    reg = FactorRegistry()

    def boom(df):
        raise RuntimeError("炸了")

    reg.register("boom", "会炸的因子", boom)
    assert np.isnan(reg.compute("boom", _ohlcv()))


def test_describe_for_prompt_lists_all_factors():
    reg = build_default_factor_registry()
    text = reg.describe_for_prompt()
    for name in reg.names():
        assert name in text
    assert "收盘价" in text  # 中文说明是 LLM 选对因子的前提


def test_unregister_and_len():
    reg = build_default_factor_registry()
    n = len(reg)
    assert reg.unregister("close")
    assert not reg.unregister("close")
    assert len(reg) == n - 1


def test_register_level_factors_and_dispose():
    from finhack_pro.data.levels import SupportResistanceDetector

    reg = build_default_factor_registry()
    dispose = register_level_factors(reg, SupportResistanceDetector())
    assert "support_strength" in reg
    assert "dist_to_support" in reg
    dispose()
    assert "support_strength" not in reg
    assert "dist_to_support" not in reg


def test_level_factors_compute_on_trending_series():
    from finhack_pro.data.levels import SupportResistanceDetector

    reg = build_default_factor_registry()
    dispose = register_level_factors(reg, SupportResistanceDetector())
    try:
        df = _ohlcv(n=120, seed=5)
        strength = reg.compute("support_strength", df)
        dist = reg.compute("dist_to_support", df)
        assert np.isfinite(strength)
        assert 0.0 <= strength <= 1.0
        assert np.isfinite(dist)
    finally:
        dispose()


# ============================================================================
# Condition 校验与判定
# ============================================================================


@pytest.fixture()
def reg():
    return build_default_factor_registry()


def test_condition_validates_against_registry(reg):
    Condition("ret_20", ">", 5.0).validate(reg)
    Condition("close", "between", [9.0, 11.0]).validate(reg)


def test_unknown_field_rejected(reg):
    with pytest.raises(SpecError, match="未知字段"):
        Condition("pe_ratio", ">", 10).validate(reg)


def test_unsupported_operator_rejected(reg):
    with pytest.raises(SpecError, match="不支持"):
        Condition("close", "like", 10).validate(reg)


def test_between_requires_two_values(reg):
    with pytest.raises(SpecError, match="between"):
        Condition("close", "between", [1.0]).validate(reg)


def test_between_rejects_inverted_bounds(reg):
    with pytest.raises(SpecError, match="下界"):
        Condition("close", "between", [11.0, 9.0]).validate(reg)


def test_numeric_field_rejects_non_numeric_value(reg):
    with pytest.raises(SpecError, match="必须是数字"):
        Condition("close", ">", "很高").validate(reg)


@pytest.mark.parametrize(
    "op,value,x,expected",
    [
        (">", 5.0, 6.0, True),
        (">", 5.0, 5.0, False),
        (">=", 5.0, 5.0, True),
        ("<", 5.0, 4.0, True),
        ("<=", 5.0, 5.0, True),
        ("==", 5.0, 5.0, True),
        ("!=", 5.0, 4.0, True),
        ("between", [1.0, 10.0], 5.0, True),
        ("between", [1.0, 10.0], 11.0, False),
    ],
)
def test_evaluate_numeric_ops(op, value, x, expected):
    assert Condition("f", op, value).evaluate(x) is expected


def test_evaluate_category_ops():
    assert Condition("f", "in", ["A", "B"]).evaluate("A") is True
    assert Condition("f", "not_in", ["A", "B"]).evaluate("C") is True


def test_condition_str_and_roundtrip():
    c = Condition("ret_20", ">", 5.0, description="近20日涨幅超5%")
    assert "ret_20 > 5.0" in str(c)
    assert "近20日涨幅超5%" in str(c)
    back = Condition.from_dict(c.to_dict())
    assert back.field == c.field and back.op == c.op and back.value == c.value


def test_condition_field_is_normalized():
    c = Condition("  RET_20  ", ">", 1)
    assert c.field == "ret_20"


# ============================================================================
# FilterSpec
# ============================================================================


def test_spec_ok_property(reg):
    spec = FilterSpec(conditions=[Condition("close", ">", 1.0)])
    assert spec.ok
    spec.unresolved.append("管理层靠谱")
    assert not spec.ok


def test_spec_empty_conditions_not_ok():
    assert not FilterSpec().ok


def test_spec_validate_rejects_bad_logic(reg):
    spec = FilterSpec(conditions=[Condition("close", ">", 1.0)], logic="xor")
    with pytest.raises(SpecError, match="logic"):
        spec.validate(reg)


def test_spec_validate_rejects_empty(reg):
    with pytest.raises(SpecError, match="为空"):
        FilterSpec().validate(reg)


def test_spec_validate_rejects_unknown_order_by(reg):
    spec = FilterSpec(conditions=[Condition("close", ">", 1.0)], order_by="nope")
    with pytest.raises(SpecError, match="排序字段"):
        spec.validate(reg)


def test_required_fields_dedup_and_include_order_by():
    spec = FilterSpec(
        conditions=[
            Condition("ret_20", ">", 0),
            Condition("ret_20", "<", 50),
            Condition("vol_ratio_5_20", ">", 1),
        ],
        order_by="close",
    )
    assert spec.required_fields() == ["ret_20", "vol_ratio_5_20", "close"]


def test_spec_roundtrip():
    spec = FilterSpec(
        conditions=[Condition("ret_20", ">", 5.0, description="强势")],
        logic="all",
        order_by="ret_20",
        as_of="2024-06-30",
        unresolved=["管理层靠谱"],
        raw_query="强势股",
    )
    back = FilterSpec.from_dict(spec.to_dict())
    assert back.logic == spec.logic
    assert back.order_by == spec.order_by
    assert back.unresolved == spec.unresolved
    assert back.conditions[0].field == "ret_20"
    assert back.raw_query == spec.raw_query


def test_spec_summary_includes_unresolved():
    spec = FilterSpec(
        conditions=[Condition("close", ">", 1.0)], unresolved=["龙头股"]
    )
    assert "龙头股" in spec.summary()


# ============================================================================
# 执行引擎
# ============================================================================


def _market() -> dict:
    return {
        "UP": _ohlcv(n=80, trend=0.03, seed=1),     # 上涨
        "FLAT": _ohlcv(n=80, trend=0.0, seed=2),    # 横盘
        "SHORT": _ohlcv(n=10, seed=3),              # 历史不足
    }


def test_engine_hits_and_skips(reg):
    spec = FilterSpec(conditions=[Condition("ret_20", ">", 1.0)])
    spec.validate(reg)
    result = ScreenEngine(reg).screen(_market(), spec)

    assert "SHORT" in result.skipped          # 算不出 -> 跳过，不是"不满足"
    assert "因子 ret_20 不可用" in result.skipped["SHORT"]
    assert result.evaluated == 2
    # coverage_rate 按 4 位小数四舍五入（便于展示），故用绝对容差
    assert result.coverage_rate == pytest.approx(2 / 3, abs=1e-4)
    assert set(result.symbols) == {"UP"}


def test_engine_logic_all_vs_any(reg):
    """all 需全部满足，any 需任一满足 —— 后者结果必为前者的超集。"""
    data = _market()
    # close > 100 无人满足，故 all 必为空；any 则退化为只看 ret_20
    two = FilterSpec(
        conditions=[Condition("ret_20", ">", 1.0), Condition("close", ">", 100.0)]
    )
    two.validate(reg)
    assert ScreenEngine(reg).screen(data, two).symbols == []

    two.logic = "any"
    any_hits = set(ScreenEngine(reg).screen(data, two).symbols)

    single = FilterSpec(conditions=[Condition("ret_20", ">", 1.0)])
    single.validate(reg)
    assert any_hits == set(ScreenEngine(reg).screen(data, single).symbols)
    assert "UP" in any_hits


def test_engine_order_by(reg):
    spec = FilterSpec(
        conditions=[Condition("above_ma20_days", ">=", 0)], order_by="ret_20"
    )
    spec.validate(reg)
    result = ScreenEngine(reg).screen(_market(), spec)
    if len(result.hits) == 2:
        vals = [h.values["ret_20"] for h in result.hits]
        assert vals == sorted(vals, reverse=True)


def test_engine_ascending_order(reg):
    spec = FilterSpec(
        conditions=[Condition("above_ma20_days", ">=", 0)],
        order_by="ret_20",
        ascending=True,
    )
    spec.validate(reg)
    result = ScreenEngine(reg).screen(_market(), spec)
    if len(result.hits) == 2:
        vals = [h.values["ret_20"] for h in result.hits]
        assert vals == sorted(vals)


def test_engine_limit(reg):
    spec = FilterSpec(conditions=[Condition("above_ma20_days", ">=", 0)], limit=1)
    spec.validate(reg)
    assert len(ScreenEngine(reg).screen(_market(), spec).hits) == 1


def test_engine_limit_override(reg):
    spec = FilterSpec(conditions=[Condition("above_ma20_days", ">=", 0)], limit=5)
    spec.validate(reg)
    assert len(ScreenEngine(reg).screen(_market(), spec, limit=1).hits) == 1


def test_engine_empty_conditions_raises(reg):
    """空条件会让所有标的"通过"，等价于没筛选 —— 必须显式报错。"""
    with pytest.raises(SpecError, match="条件为空"):
        ScreenEngine(reg).screen(_market(), FilterSpec())


def test_engine_records_hit_explanation(reg):
    spec = FilterSpec(conditions=[Condition("ret_20", ">", 1.0)])
    spec.validate(reg)
    result = ScreenEngine(reg).screen(_market(), spec)
    assert "满足 ret_20" in result.hits[0].explains()


def test_engine_empty_dataframe_skipped(reg):
    spec = FilterSpec(conditions=[Condition("close", ">", 0)])
    spec.validate(reg)
    result = ScreenEngine(reg).screen({"EMPTY": pd.DataFrame()}, spec)
    assert result.skipped["EMPTY"] == "无数据"
    assert result.evaluated == 0


def test_engine_summary_contains_coverage(reg):
    spec = FilterSpec(conditions=[Condition("close", ">", 0)])
    spec.validate(reg)
    assert "覆盖率=" in ScreenEngine(reg).screen(_market(), spec).summary()


# ============================================================================
# 编译器（LLM 唯一出场处）
# ============================================================================


def _chat_returning(payload: dict):
    def _fn(system: str, user: str) -> str:
        return json.dumps(payload, ensure_ascii=False)

    return _fn


def test_compiler_produces_spec(reg):
    payload = {
        "conditions": [
            {"field": "ret_20", "op": ">", "value": 5.0, "description": "近20日涨幅超5%"},
            {"field": "vol_ratio_5_20", "op": ">", "value": 1.2, "description": "放量"},
        ],
        "logic": "all",
        "order_by": "ret_20",
        "unresolved": [],
    }
    compiler = ConditionCompiler(_chat_returning(payload), reg)
    spec = compiler.compile("最近放量上涨的强势股", as_of="2024-06-30")

    assert spec.ok
    assert len(spec.conditions) == 2
    assert spec.as_of == "2024-06-30"
    assert spec.raw_query == "最近放量上涨的强势股"
    spec.validate(reg)


def test_compiler_handles_json_fence(reg):
    payload = {"conditions": [{"field": "close", "op": ">", "value": 1.0}]}

    def _fn(system, user):
        return "好的，编译结果如下：\n```json\n" + json.dumps(payload) + "\n```\n"

    spec = ConditionCompiler(_fn, reg).compile("收盘价大于1")
    assert spec.conditions[0].field == "close"


def test_compiler_puts_invalid_field_into_unresolved(reg):
    """LLM 编造的字段名不得被静默忽略，必须进 unresolved。"""
    payload = {
        "conditions": [
            {"field": "close", "op": ">", "value": 1.0, "description": "股价大于1"},
            {"field": "pe_ratio", "op": "<", "value": 20, "description": "低估值"},
        ]
    }
    spec = ConditionCompiler(_chat_returning(payload), reg).compile("股价大于1且低估值")

    assert len(spec.conditions) == 1
    assert spec.unresolved == ["低估值"]
    assert not spec.ok


def test_compiler_surfaces_llm_unresolved(reg):
    payload = {
        "conditions": [{"field": "close", "op": ">", "value": 1.0}],
        "unresolved": ["管理层靠谱"],
    }
    spec = ConditionCompiler(_chat_returning(payload), reg).compile("好公司")
    assert spec.unresolved == ["管理层靠谱"]
    assert not spec.ok


def test_compiler_raises_when_no_valid_condition(reg):
    payload = {"conditions": [{"field": "nope", "op": ">", "value": 1}]}
    with pytest.raises(CompileError, match="没有任何条件通过校验"):
        ConditionCompiler(_chat_returning(payload), reg).compile("随便")


def test_compiler_raises_on_non_json(reg):
    with pytest.raises(CompileError, match="未找到合法 JSON"):
        ConditionCompiler(lambda s, u: "我不太明白您的意思", reg).compile("随便")


def test_compiler_raises_on_empty_response(reg):
    with pytest.raises(CompileError, match="返回为空"):
        ConditionCompiler(lambda s, u: "   ", reg).compile("随便")


def test_compiler_rejects_symbol_recommendations(reg):
    """编译器产出必须是条件，不能是标的 —— 那是执行引擎的事。"""
    payload = {
        "conditions": [{"field": "ret_20", "op": ">", "value": 5.0}],
        "unresolved": [],
    }

    def _fn(system, user):
        return json.dumps(payload) + "\n另外我觉得 600519 和 000001 都不错。"

    with pytest.raises(CompileError, match="不得推荐具体标的"):
        ConditionCompiler(_fn, reg).compile("强势股")


def test_compiler_caps_condition_count(reg):
    payload = {
        "conditions": [
            {"field": "close", "op": ">", "value": float(i)} for i in range(6)
        ]
    }
    compiler = ConditionCompiler(_chat_returning(payload), reg, max_conditions=3)
    spec = compiler.compile("很多条件")
    assert len(spec.conditions) == 3
    assert any("条件数超限" in u for u in spec.unresolved)


def test_compiler_empty_query_rejected(reg):
    compiler = ConditionCompiler(_chat_returning({}), reg)
    with pytest.raises(ValueError, match="不能为空"):
        compiler.compile("   ")


def test_compiler_prompt_contains_factor_catalog(reg):
    seen = {}

    def _fn(system, user):
        seen["system"] = system
        return json.dumps({"conditions": [{"field": "close", "op": ">", "value": 1}]})

    ConditionCompiler(_fn, reg).compile("股价大于1", as_of="2024-06-30")
    for name in reg.names():
        assert name in seen["system"]
    assert "2024-06-30" in seen["system"]   # 时间必须锚定决策日
    assert "严禁输出任何股票代码" in seen["system"]


def test_compiler_requires_callable_chat_fn(reg):
    with pytest.raises(ValueError, match="必须可调用"):
        ConditionCompiler("not-callable", reg)


def test_compiler_async_chat_fn(reg):
    payload = {"conditions": [{"field": "close", "op": ">", "value": 1.0}]}

    async def _fn(system, user):
        return json.dumps(payload)

    spec = ConditionCompiler(_fn, reg).compile("股价大于1")
    assert spec.conditions[0].field == "close"


def test_compiler_acompile(reg):
    import asyncio

    payload = {"conditions": [{"field": "close", "op": ">", "value": 1.0}]}

    async def _fn(system, user):
        return json.dumps(payload)

    spec = asyncio.run(ConditionCompiler(_fn, reg).acompile("股价大于1"))
    assert spec.conditions[0].field == "close"


def test_compiler_normalizes_bad_logic(reg):
    payload = {"conditions": [{"field": "close", "op": ">", "value": 1.0}], "logic": "xor"}
    spec = ConditionCompiler(_chat_returning(payload), reg).compile("x")
    assert spec.logic == "all"


def test_chat_fn_adapter_from_llm_client():
    class _Client:
        def __init__(self):
            self.calls = []

        async def chat(self, message, system="", **kwargs):
            self.calls.append((message, system))
            return "ok"

    client = _Client()
    fn = chat_fn_from_llm_client(client)
    import asyncio

    assert asyncio.run(fn("SYS", "USER")) == "ok"
    assert client.calls == [("USER", "SYS")]


def test_chat_fn_adapter_rejects_bad_client():
    with pytest.raises(ValueError, match="chat"):
        chat_fn_from_llm_client(object())


# ============================================================================
# 端到端：编译 -> 执行
# ============================================================================


def test_end_to_end_compile_then_screen(reg):
    """一次 LLM 编译 + 确定性执行 —— LLM 不参与任何单只股票的评判。"""
    calls = []

    payload = {
        "conditions": [
            {"field": "ret_20", "op": ">", "value": 1.0, "description": "近20日上涨"},
            {"field": "above_ma20_days", "op": ">", "value": 0.0, "description": "站上20日线"},
        ],
        "logic": "all",
        "order_by": "ret_20",
    }

    def _fn(system, user):
        calls.append(user)
        return json.dumps(payload)

    spec = ConditionCompiler(_fn, reg).compile("站上20日线的上涨股", as_of="2024-06-30")
    spec.validate(reg)

    big_market = {f"S{i:03d}": _ohlcv(n=80, trend=0.02 * (i % 3), seed=i) for i in range(20)}
    result = ScreenEngine(reg).screen(big_market, spec)

    # 关键断言：20 只股票，LLM 只被调用 1 次
    assert len(calls) == 1
    assert result.evaluated == 20
    assert set(result.symbols) <= set(big_market)
    # 命中的确实满足条件
    for hit in result.hits:
        assert hit.values["ret_20"] > 1.0
        assert hit.values["above_ma20_days"] > 0.0
