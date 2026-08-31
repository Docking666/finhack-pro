"""
数据源插件注册中心

为什么需要它
------------
``sources.SOURCE_REGISTRY`` 是一个静态字典，只能提供内置源。这带来三个问题：

1. **外部能力挂不进来**。本地量化仓库、miniQMT ``xtdata``、聚宽 / Tushare Pro
   等适配器，都无法在不改 ``sources.py`` 的前提下接入。
2. **无法热插拔**。测试里想临时替换数据源、或运行时发现某源不可用想摘掉它，
   只能改全局字典，且撤销要自己记原值 —— 极易漏还原，污染后续用例。
3. **每个源的构造参数被硬编码**在 ``build_source_chain`` 里（tushare 要 token
   就得写 if 分支），加一个新源要动核心函数。

本模块移植 DeepSeek Harness 四条范式中最有价值的一条：

    **可逆副作用** —— 注册即登记撤销动作。``register()`` 返回一个 disposer，
    调用它即完整还原到注册前的状态（包括"原本不存在"与"原本是别的源"两种情况，
    且保持原有注册顺序）。插件因此可安全热插拔、可失败回滚、可在测试中临时替换，
    不需要"重启进程"这种粗暴手段。

另两条一并提供：

    **三角色** —— 定义方（本项目定义 ``BaseDataSource`` 契约）、
    提供方（akshare / baostock / tushare / 本地仓库 / miniQMT / 用户自定义）、
    消费方（``DataFetcher``）。三方只通过契约耦合。

    **配置即组装** —— ``build_source_chain`` 不再认识任何具体源，
    只按配置里的名字去注册中心取，源是否可用以 ``required_config`` 声明的
    必需配置是否齐备为准。

Usage:
    >>> from finhack_pro.data.registry import default_registry
    >>> dispose = default_registry.register(
    ...     "miniQMT",
    ...     lambda **cfg: XtDataAdapter(**cfg),
    ...     description="miniQMT 行情（需券商开通）",
    ...     required_config=("qmt_path",),
    ... )
    >>> ...                      # 用完了
    >>> dispose()                # 完整还原
"""

from __future__ import annotations

import importlib.metadata as metadata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

#: entry_points 组名。第三方包在 pyproject 中声明该组即可被自动发现。
ENTRY_POINT_GROUP = "finhack_pro.data_sources"


class RegistryError(ValueError):
    """注册中心的使用错误（重名、工厂不可调用、类型不符等）"""


class MissingSourceConfig(ValueError):
    """实例化数据源时缺少必需配置"""


@dataclass
class SourceSpec:
    """一个已注册数据源的声明"""

    name: str
    factory: Callable[..., Any]
    description: str = ""
    #: 实例化前必须提供的配置项。缺任一项即视为"该源不可用"，
    #: 由调用方决定跳过还是报错 —— 不要在 factory 里静默降级。
    required_config: Tuple[str, ...] = ()
    #: 来源标记，便于排查：builtin / entry_point / runtime / config
    origin: str = "runtime"


class DataSourceRegistry:
    """数据源插件注册中心

    线程安全性：本类不假设并发注册场景。实际使用中注册集中在启动期完成，
    运行期只读。若将来需要运行期动态注册，再加锁不迟。
    """

    def __init__(self) -> None:
        self._specs: Dict[str, SourceSpec] = {}

    # ------------------------------------------------------------------
    # 注册（可逆）
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        factory: Callable[..., Any],
        *,
        description: str = "",
        required_config: Tuple[str, ...] = (),
        origin: str = "runtime",
        replace: bool = False,
    ) -> Callable[[], None]:
        """注册数据源，返回用于**撤销本次注册**的 disposer。

        Args:
            name: 源名称（大小写不敏感，内部统一小写）
            factory: 可调用对象，接受配置关键字参数，返回数据源实例
            description: 人类可读说明，用于诊断输出
            required_config: 必需的配置项名
            origin: 来源标记
            replace: 是否允许覆盖同名已注册源。默认 False —— 重名通常是配置错误，
                     静默覆盖会让"我注册的源没生效"这类问题极难排查

        Returns:
            disposer：无参调用即还原到注册前状态（含"原本不存在"的情况，
            并保持原有注册顺序）

        Raises:
            RegistryError: 名称为空 / 工厂不可调用 / 重名且 replace=False
        """
        key = self._normalize(name)
        if not key:
            raise RegistryError("数据源名称不能为空")
        if not callable(factory):
            raise RegistryError(f"数据源 {key!r} 的 factory 不可调用")
        if key in self._specs and not replace:
            raise RegistryError(
                f"数据源 {key!r} 已注册（origin={self._specs[key].origin}）。"
                f"确需覆盖请显式传 replace=True。"
            )

        previous = self._specs.get(key)
        self._specs[key] = SourceSpec(
            name=key,
            factory=factory,
            description=description,
            required_config=tuple(required_config),
            origin=origin,
        )

        def dispose() -> None:
            """还原到注册前状态。可重复调用。"""
            if previous is None:
                self._specs.pop(key, None)
            elif self._specs.get(key) is not None:
                # 原地替换以保持原有注册顺序（dict 保序）
                self._specs = {
                    k: (previous if k == key else v) for k, v in self._specs.items()
                }

        return dispose

    def unregister(self, name: str) -> bool:
        """注销数据源。返回是否确有注销。"""
        return self._specs.pop(self._normalize(name), None) is not None

    def clear(self) -> None:
        """清空（仅供测试与重建场景）"""
        self._specs.clear()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(name: str) -> str:
        return (name or "").strip().lower()

    def spec(self, name: str) -> Optional[SourceSpec]:
        return self._specs.get(self._normalize(name))

    def names(self) -> List[str]:
        return list(self._specs.keys())

    def describe(self) -> Dict[str, str]:
        """{name: description}，供诊断与配置校验输出。"""
        return {k: v.description for k, v in self._specs.items()}

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._normalize(name) in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    # ------------------------------------------------------------------
    # 实例化
    # ------------------------------------------------------------------

    def create(self, name: str, **config: Any) -> Any:
        """按名实例化数据源。

        Raises:
            RegistryError: 未注册
            MissingSourceConfig: 缺少 required_config 声明的配置项
        """
        spec = self.spec(name)
        if spec is None:
            raise RegistryError(
                f"未注册的数据源: {name!r}。已注册: {sorted(self._specs)}"
            )
        self._check_config(spec, config)
        instance = spec.factory(**config)
        self._validate_instance(spec, instance)
        return instance

    def try_create(self, name: str, **config: Any) -> Optional[Any]:
        """同 :meth:`create`，但缺少必需配置时返回 None 而非抛错。

        用于"配置不全就跳过该源"的组装场景 —— 这是**主动降级**，
        调用方会看到告警；与 factory 内部静默降级（不可见）有本质区别。
        """
        spec = self.spec(name)
        if spec is None:
            logger.warning("未注册的数据源: {}，跳过", name)
            return None
        missing = [k for k in spec.required_config if not config.get(k)]
        if missing:
            logger.warning(
                "数据源 {} 缺少必需配置 {}，跳过该源", spec.name, missing
            )
            return None
        return self.create(name, **config)

    @staticmethod
    def _check_config(spec: SourceSpec, config: Dict[str, Any]) -> None:
        missing = [k for k in spec.required_config if not config.get(k)]
        if missing:
            raise MissingSourceConfig(
                f"数据源 {spec.name!r} 缺少必需配置: {missing}"
            )

    @staticmethod
    def _validate_instance(spec: SourceSpec, instance: Any) -> None:
        """校验产物符合 BaseDataSource 契约。

        优先做严格的 isinstance 检查；若因导入失败拿不到基类（不应发生），
        退化为鸭子类型检查 —— 但**不静默放过一个明显错误的实现**，
        缺 get_daily 会直接报错，而不是等到取数时才炸。
        """
        try:
            from finhack_pro.data.sources import BaseDataSource

            if not isinstance(instance, BaseDataSource):
                raise RegistryError(
                    f"数据源 {spec.name!r} 的 factory 返回了 "
                    f"{type(instance).__name__}，未继承 BaseDataSource"
                )
            return
        except ImportError:
            pass
        if not callable(getattr(instance, "get_daily", None)):
            raise RegistryError(
                f"数据源 {spec.name!r} 的 factory 返回了 "
                f"{type(instance).__name__}，且未实现 get_daily()"
            )

    # ------------------------------------------------------------------
    # 插件自动发现
    # ------------------------------------------------------------------

    def discover(
        self, group: str = ENTRY_POINT_GROUP, replace: bool = False
    ) -> List[str]:
        """从已安装包的 entry_points 自动发现并注册数据源插件。

        第三方包只需在 pyproject 中声明::

            [project.entry-points."finhack_pro.data_sources"]
            my_source = "my_pkg.sources:create_source"

        Args:
            group: entry_points 组名
            replace: 是否覆盖同名已注册源。默认 False ——
                     内置源与运行时显式注册的源优先于自动发现的，
                     避免"装了个包就把线上数据源换掉"这种意外

        Returns:
            本次新注册成功的源名称列表
        """
        try:
            eps = metadata.entry_points(group=group)
        except Exception as e:  # 元数据损坏等不应让启动失败
            logger.warning("扫描 entry_points 组 {!r} 失败: {}", group, e)
            return []

        registered: List[str] = []
        for ep in eps:
            try:
                factory = ep.load()
            except Exception as e:
                # 单个插件加载失败不拖垮整体，但必须可见
                logger.warning("加载数据源插件 {!r} 失败: {}", ep.name, e)
                continue
            if not callable(factory):
                logger.warning("数据源插件 {!r} 的入口不是可调用对象，跳过", ep.name)
                continue
            try:
                self.register(
                    ep.name,
                    factory,
                    description=f"entry_point: {ep.value}",
                    origin="entry_point",
                    replace=replace,
                )
            except RegistryError as e:
                logger.info("跳过数据源插件 {!r}: {}", ep.name, e)
                continue
            registered.append(self._normalize(ep.name))
            logger.info("自动发现数据源插件: {}", ep.name)
        return registered


# ============================================================================
# 默认注册中心（内置源由 sources.py 注入，见 _register_builtins）
# ============================================================================

default_registry = DataSourceRegistry()


def _register_builtins() -> None:
    """把内置数据源登记进默认注册中心。

    放在函数里而非模块顶层：避免 registry 与 sources 形成模块级循环导入
    （sources.build_source_chain 需要用 default_registry）。
    """
    from finhack_pro.data.free_stockdb import FreeStockDBSource
    from finhack_pro.data.sources import (
        AkshareEMDataSource,
        AkshareSinaDataSource,
        AkshareTXDataSource,
        BaostockDataSource,
        TushareDataSource,
        WarehouseDataSource,
    )

    builtins: List[Tuple[str, Callable[..., Any], str, Tuple[str, ...]]] = [
        (
            "akshare_tx",
            lambda **cfg: AkshareTXDataSource(**cfg),
            "AkShare 腾讯证券日线（默认首选，绕开东财封锁）",
            (),
        ),
        (
            "akshare_em",
            lambda **cfg: AkshareEMDataSource(**cfg),
            "AkShare 东方财富日线（端点常被反爬断开）",
            (),
        ),
        (
            "akshare_sina",
            lambda **cfg: AkshareSinaDataSource(**cfg),
            "AkShare 新浪日线",
            (),
        ),
        ("baostock", lambda **cfg: BaostockDataSource(**cfg), "Baostock 日线", ()),
        (
            "tushare",
            lambda **cfg: TushareDataSource(**cfg),
            "Tushare Pro 日线（需 token）",
            ("tushare_token",),
        ),
        (
            "warehouse",
            lambda **cfg: WarehouseDataSource(**cfg),
            "本地量化仓库（永久事实库，全市场扫描首选）",
            ("warehouse_dir",),
        ),
        (
            "free_stockdb",
            lambda **cfg: FreeStockDBSource(**cfg),
            "free-stockdb 本地数据引擎（须先启动 stockdb.exe；只连本机，见其模块文档的 mock 数据风险）",
            (),
        ),
    ]

    for name, factory, desc, required in builtins:
        if name in default_registry:
            continue
        default_registry.register(
            name,
            factory,
            description=desc,
            required_config=required,
            origin="builtin",
        )


__all__ = [
    "DataSourceRegistry",
    "SourceSpec",
    "RegistryError",
    "MissingSourceConfig",
    "default_registry",
    "ENTRY_POINT_GROUP",
]
