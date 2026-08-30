"""数据源插件注册中心的回归测试

核心不变量：
  1. 可逆副作用 —— register() 返回的 disposer 必须完整还原注册前状态，
     包括"原本不存在"和"原本是别的源"两种情况，且保持原有注册顺序
  2. 配置即组装 —— build_source_chain 不认识任何具体源，只按名字查注册中心
  3. 失败显式化 —— 缺少必需配置时跳过该源并告警，而非在 factory 内静默降级
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finhack_pro.data.registry import (
    ENTRY_POINT_GROUP,
    DataSourceRegistry,
    MissingSourceConfig,
    RegistryError,
    SourceSpec,
    default_registry,
)
from finhack_pro.data.sources import BaseDataSource, build_source_chain

# ============================================================================
# 测试替身
# ============================================================================


class _StubSource(BaseDataSource):
    name = "stub"

    def __init__(self, adjust: str = "qfq", **params) -> None:
        super().__init__(adjust=adjust, **params)
        self.tag = params.get("tag", "")

    def get_daily(self, symbol, start_date, end_date):
        return pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "close": [1.0]})


def _stub_factory(**cfg):
    return _StubSource(**cfg)


@pytest.fixture()
def registry():
    """每个用例一个干净注册中心，避免相互污染。"""
    return DataSourceRegistry()


# ============================================================================
# 注册与可逆性
# ============================================================================


def test_register_and_create(registry):
    registry.register("demo", _stub_factory, description="演示源")
    src = registry.create("demo", adjust="qfq", tag="T")
    assert isinstance(src, _StubSource)
    assert src.tag == "T"
    assert "demo" in registry


def test_name_is_case_insensitive(registry):
    registry.register("Demo", _stub_factory)
    assert "demo" in registry
    assert registry.spec("DEMO") is not None
    assert registry.names() == ["demo"]


def test_disposer_restores_absence(registry):
    """原本不存在 -> dispose 后必须消失。"""
    dispose = registry.register("temp", _stub_factory)
    assert "temp" in registry
    dispose()
    assert "temp" not in registry
    assert len(registry) == 0


def test_disposer_restores_previous_entry(registry):
    """原本是别的源 -> dispose 后必须还原成原来那个，而非删除。"""
    registry.register("slot", lambda **c: _StubSource(**c))
    original = registry.spec("slot")

    dispose = registry.register("slot", _stub_factory, replace=True)
    assert registry.spec("slot").factory is _stub_factory

    dispose()
    assert registry.spec("slot") is original
    assert registry.spec("slot").factory is not _stub_factory


def test_disposer_preserves_registration_order(registry):
    """临时覆盖再还原，不得把源挪到末尾（dict 保序对诊断输出很重要）。"""
    registry.register("a", _stub_factory)
    registry.register("b", _stub_factory)
    registry.register("c", _stub_factory)

    dispose = registry.register("b", _stub_factory, replace=True)
    assert registry.names() == ["a", "b", "c"]
    dispose()
    assert registry.names() == ["a", "b", "c"]


def test_disposer_is_idempotent(registry):
    dispose = registry.register("x", _stub_factory)
    dispose()
    dispose()  # 重复调用不得抛异常
    assert "x" not in registry


def test_duplicate_registration_rejected_by_default(registry):
    """重名通常是配置错误，静默覆盖会让"我注册的源没生效"极难排查。"""
    registry.register("dup", _stub_factory)
    with pytest.raises(RegistryError, match="已注册"):
        registry.register("dup", _stub_factory)
    # 显式声明才可覆盖
    registry.register("dup", _stub_factory, replace=True)
    assert len(registry) == 1


def test_empty_name_rejected(registry):
    with pytest.raises(RegistryError, match="不能为空"):
        registry.register("   ", _stub_factory)


def test_non_callable_factory_rejected(registry):
    with pytest.raises(RegistryError, match="不可调用"):
        registry.register("bad", "not-callable")


def test_unregister(registry):
    registry.register("gone", _stub_factory)
    assert registry.unregister("gone")
    assert not registry.unregister("gone")


def test_clear(registry):
    registry.register("a", _stub_factory)
    registry.clear()
    assert len(registry) == 0


def test_describe_and_len(registry):
    registry.register("p", _stub_factory, description="P 源")
    registry.register("q", _stub_factory, description="Q 源")
    assert registry.describe() == {"p": "P 源", "q": "Q 源"}
    assert len(registry) == 2


# ============================================================================
# 实例化校验
# ============================================================================


def test_create_unknown_source_raises(registry):
    with pytest.raises(RegistryError, match="未注册的数据源"):
        registry.create("nope")


def test_missing_required_config_raises(registry):
    registry.register("needcfg", _stub_factory, required_config=("token",))
    with pytest.raises(MissingSourceConfig, match="token"):
        registry.create("needcfg")
    # 提供了就能过
    assert registry.create("needcfg", token="abc") is not None


def test_try_create_returns_none_on_missing_config(registry):
    """配置不全 -> 返回 None 由调用方跳过，而非抛错中断整条链。"""
    registry.register("needcfg", _stub_factory, required_config=("token",))
    assert registry.try_create("needcfg") is None
    assert registry.try_create("needcfg", token="abc") is not None


def test_try_create_returns_none_for_unknown(registry):
    assert registry.try_create("nope") is None


def test_factory_returning_wrong_type_is_rejected(registry):
    """返回非 BaseDataSource 必须当场报错，不能拖到取数时才炸。"""
    registry.register("wrong", lambda **c: object())
    with pytest.raises(RegistryError):
        registry.create("wrong")


def test_duck_typing_fallback_when_base_unimportable(registry, monkeypatch):
    """拿不到 BaseDataSource 时退化为鸭子类型检查，但仍不许缺 get_daily。"""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "finhack_pro.data.sources":
            raise ImportError("blocked")
        return real_import(name, *a, **k)

    class _Duck:
        def get_daily(self, symbol, s, e):
            return pd.DataFrame()

    registry.register("duck", lambda **c: _Duck())
    monkeypatch.setattr(builtins, "__import__", _blocked)
    assert registry.create("duck") is not None

    class _NotDuck:
        pass

    registry.register("notduck", lambda **c: _NotDuck())
    with pytest.raises(RegistryError, match="get_daily"):
        registry.create("notduck")


# ============================================================================
# 配置即组装：build_source_chain
# ============================================================================


def test_builtin_sources_registered():
    from finhack_pro.data.registry import _register_builtins

    _register_builtins()
    names = default_registry.names()
    for expected in ("akshare_tx", "akshare_em", "akshare_sina", "baostock", "tushare", "warehouse"):
        assert expected in names


def test_register_builtins_is_idempotent():
    from finhack_pro.data.registry import _register_builtins

    _register_builtins()
    before = default_registry.names()
    _register_builtins()
    assert default_registry.names() == before


def test_chain_uses_registry_not_hardcoded():
    """新增一个源只需注册，不必改 build_source_chain。"""
    dispose = default_registry.register(
        "tmp_probe", _stub_factory, description="临时探针", origin="runtime"
    )
    try:
        chain = build_source_chain(sources=["tmp_probe"], adjust="qfq")
        # 链上元素被 RetryDataSource 包装，但 name 透传内部源名
        assert [s.name for s in chain] == ["stub"]
        assert isinstance(chain[0].inner, _StubSource)
    finally:
        dispose()
    assert "tmp_probe" not in default_registry


def test_chain_skips_unknown_source(tmp_path):
    """未知源跳过并告警，不拖垮整条链。"""
    chain = build_source_chain(sources=["definitely_unknown", "akshare_tx"])
    assert [s.name for s in chain] == ["akshare_tx"]


def test_chain_skips_source_with_missing_config():
    """tushare 无 token -> 跳过，而非带着空 token 去实例化。"""
    chain = build_source_chain(sources=["tushare", "akshare_tx"], tushare_token="")
    assert [s.name for s in chain] == ["akshare_tx"]


def test_all_sources_unavailable_raises():
    """全部源都缺配置时，必须显式报错而非返回空链（空链会把后续失败变成谜）。"""
    with pytest.raises(ValueError, match="没有可用的数据源"):
        build_source_chain(sources=["tushare", "warehouse"])


def test_warehouse_source_in_chain(tmp_path):
    wh_dir = tmp_path / "wh"
    chain = build_source_chain(
        sources=["warehouse", "akshare_tx"], warehouse_dir=str(wh_dir)
    )
    names = [s.name for s in chain]
    assert names == ["warehouse", "akshare_tx"]


def test_warehouse_source_not_wrapped_in_retry(tmp_path):
    """本地读取不存在网络抖动，包 RetryDataSource 只会把 miss 放大 3 倍。"""
    from finhack_pro.data.sources import RetryDataSource

    wh_dir = tmp_path / "wh"
    chain = build_source_chain(
        sources=["warehouse", "akshare_tx"], warehouse_dir=str(wh_dir)
    )
    assert isinstance(chain[0], RetryDataSource) is False
    assert isinstance(chain[1], RetryDataSource) is not False  # 在线源仍要重试


def test_warehouse_source_requires_dir():
    from finhack_pro.data.registry import _register_builtins
    from finhack_pro.data.registry import default_registry as reg

    _register_builtins()
    spec = reg.spec("warehouse")
    assert spec is not None
    assert "warehouse_dir" in spec.required_config
    with pytest.raises(MissingSourceConfig):
        reg.create("warehouse")


def test_legacy_source_mapping_still_works():
    """未提供 sources 时，legacy source 映射不得回归。"""
    chain = build_source_chain(source="akshare")
    assert [s.name for s in chain][:2] == ["akshare_tx", "akshare_em"]
    chain = build_source_chain(source="tushare", tushare_token="tok")
    assert "tushare" in [s.name for s in chain]


def test_empty_chain_raises():
    with pytest.raises(ValueError, match="没有可用的数据源"):
        build_source_chain(sources=["definitely_unknown"])


# ============================================================================
# 仓库作为数据源的端到端行为
# ============================================================================


def _ohlcv(n: int = 60, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 10 + np.cumsum(rng.normal(0, 0.1, n))
    h = c + abs(rng.normal(0.05, 0.01, n))
    lo = c - abs(rng.normal(0.05, 0.01, n))
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-02", periods=n),
            "open": (h + lo) / 2,
            "high": h,
            "low": lo,
            "close": c,
            "volume": rng.integers(1e6, 5e6, n).astype(float),
        }
    )


def test_warehouse_source_reads_ingested_data(tmp_path):
    from finhack_pro.data.warehouse import MarketWarehouse

    wh = MarketWarehouse(tmp_path / "wh")
    df = _ohlcv()
    wh.put("600519", df)

    chain = build_source_chain(sources=["warehouse"], warehouse_dir=str(tmp_path / "wh"))
    got = chain[0].get_daily("600519", "2024-01-02", "2024-12-31")
    assert len(got) == len(df)


def test_warehouse_source_returns_empty_when_not_covered(tmp_path):
    """未覆盖必须返回空表让上层回退，绝不伪造数据。"""
    chain = build_source_chain(sources=["warehouse"], warehouse_dir=str(tmp_path / "wh"))
    got = chain[0].get_daily("999999", "2024-01-02", "2024-12-31")
    assert got.empty


def test_warehouse_source_never_uses_network_on_miss(tmp_path):
    """未覆盖时不得偷偷联网 —— 那会把不受控请求混进全市场扫描路径。"""
    chain = build_source_chain(sources=["warehouse"], warehouse_dir=str(tmp_path / "wh"))
    src = chain[0]
    assert src.retryable is False


# ============================================================================
# entry_points 自动发现
# ============================================================================


def test_discover_handles_missing_group_gracefully(registry):
    """组不存在时返回空列表，不得抛异常中断启动。"""
    assert registry.discover(group="definitely.not.a.real.group") == []


def test_discover_registers_entry_point(registry, monkeypatch):
    """模拟一个已安装包声明了 entry_point。"""

    class _FakeEP:
        name = "plugin_src"
        value = "pkg:factory"

        def load(self):
            return _stub_factory

    import importlib.metadata as md

    monkeypatch.setattr(md, "entry_points", lambda group: [_FakeEP()])
    registered = registry.discover(group=ENTRY_POINT_GROUP)

    assert registered == ["plugin_src"]
    assert registry.spec("plugin_src").origin == "entry_point"
    assert registry.create("plugin_src") is not None


def test_discover_skips_broken_plugin(registry, monkeypatch):
    """单个插件加载失败不拖垮整体。"""

    class _BrokenEP:
        name = "broken"
        value = "pkg:missing"

        def load(self):
            raise ImportError("包坏了")

    import importlib.metadata as md

    monkeypatch.setattr(md, "entry_points", lambda group: [_BrokenEP()])
    assert registry.discover(group=ENTRY_POINT_GROUP) == []
    assert len(registry) == 0


def test_discover_does_not_override_existing_by_default(registry, monkeypatch):
    """自动发现不得盖掉显式注册的源 —— 避免装个包就换掉线上数据源。"""

    class _FakeEP:
        name = "mine"
        value = "pkg:factory"

        def load(self):
            return _stub_factory

    registry.register("mine", _stub_factory, origin="runtime")
    original = registry.spec("mine")

    import importlib.metadata as md

    monkeypatch.setattr(md, "entry_points", lambda group: [_FakeEP()])
    registry.discover(group=ENTRY_POINT_GROUP)
    assert registry.spec("mine") is original
