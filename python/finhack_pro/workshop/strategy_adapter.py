"""
策略工坊代码适配器 - WorkshopStrategyAdapter

桥接"工坊旧 API"策略代码与"回测引擎新 API"：

旧 API（策略工坊模板 / LLM 生成）:
    class MyStrategy(BaseStrategy):
        def on_bar(self, bar):          # 单参数
            if ... and not self.position:
                self.buy(bar.close, size=100)
            elif ... and self.position:
                self.sell(bar.close, size=self.position.quantity)

新 API（backtest/runner.py）:
    class MyStrategy(BaseStrategy):
        def on_bar(self, context, bar) -> List[Signal]:   # 双参数返回信号列表

适配器在受限命名空间中动态 exec 用户代码，将旧 API 的
`self.buy / self.sell / self.position / self.bars` 桥接为新 API 的 Signal 输出。

安全：执行前必须通过 PackageScanner.scan_code（AST 静态扫描拒绝危险模块/内建/方法）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from finhack_pro.backtest.runner import BacktestRunner
from finhack_pro.strategies.base import (
    BarData,
    BaseStrategy,
    Context,
    Signal,
    SignalDirection,
)
from finhack_pro.utils.logger import get_logger
from finhack_pro.workshop.security import PackageScanner, SecurityIssue

logger = get_logger(__name__)


class StrategySecurityError(RuntimeError):
    """策略代码未通过安全扫描"""


class LegacyPosition:
    """旧 API 的 self.position 桩（近似持仓同步，最终由 runner 仲裁）"""
    quantity: int = 0

    def __bool__(self) -> bool:
        """空仓时为 False：兼容旧代码 `if not self.position` 判断"""
        return self.quantity > 0


class _LegacyBaseStrategy:
    """旧 API 策略基类桩

    提供旧代码依赖的默认行为；子类可覆盖 on_bar。
    真正的买卖经 self.buy/self.sell 重定向到适配器。
    """

    def __init__(self) -> None:
        self.bars: List[BarData] = []
        self.position = LegacyPosition()

    def on_bar(self, bar: BarData) -> None:
        return None

    def buy(self, price: float, size: int = 100) -> None:
        return None

    def sell(self, price: float, size: Optional[int] = None) -> None:
        return None


class WorkshopStrategyAdapter(BaseStrategy):
    """旧 API 策略代码 → 新 API 回测引擎适配器

    Args:
        code: 用户策略代码（Python 源码）
        symbol: 标的代码（用于 Signal.symbol）
    """

    def __init__(self, code: str, symbol: str = "", params: Optional[Dict[str, Any]] = None):
        super().__init__()
        self._code = code
        self._symbol = symbol
        self._params = params or {}
        self._signals: List[Signal] = []
        self._strategy: Any = None
        self._new_api: bool = False  # True=新 API(on_bar(context,bar)->List[Signal])；False=旧 API
        self.bars: List[BarData] = []
        self.position = LegacyPosition()

    def _scan(self) -> List[SecurityIssue]:
        """AST 安全扫描（exec 前必须调用）"""
        scanner = PackageScanner()
        return scanner.scan_code(self._code)

    def _load(self) -> None:
        """在受限命名空间中编译并执行用户代码，找到策略子类"""
        # 先安全扫描：高危 issue 直接拒绝
        issues = self._scan()
        if issues:
            high = [i for i in issues if i.severity == "high"]
            if high:
                details = "; ".join(f"L{i.line}:{i.message}" for i in high[:5])
                raise StrategySecurityError(f"策略代码含危险操作，已拒绝执行: {details}")

        # 受限命名空间：只暴露旧 API 基类 + 策略模块所需
        ns: Dict[str, Any] = {
            "BaseStrategy": _LegacyBaseStrategy,
            "SignalDirection": SignalDirection,
            "Signal": Signal,
        }
        try:
            compiled = compile(self._code, "<workshop_strategy>", "exec")
            exec(compiled, ns)  # noqa: S102 - 已通过 AST 安全扫描
        except SyntaxError as e:
            raise StrategySecurityError(f"策略代码语法错误: {e}") from e

        # 找到 BaseStrategy 的子类并实例化
        # 注意：exec 上下文中的类 __module__ 为 'builtins'，不能按 module 过滤
        strategy_cls = None
        for name, obj in ns.items():
            if (
                isinstance(obj, type)
                and issubclass(obj, _LegacyBaseStrategy)
                and obj is not _LegacyBaseStrategy
                and obj is not BaseStrategy
            ):
                strategy_cls = obj
                break

        if strategy_cls is None:
            raise StrategySecurityError(
                "未找到策略类：请确保代码定义了继承 BaseStrategy 的类"
            )

        # 检测 API 形态：on_bar 双参(新 API, 返回 List[Signal]) vs 单参(旧 API, self.buy/sell)
        import inspect as _inspect

        try:
            _sig = _inspect.signature(strategy_cls.on_bar)
            _pos_params = [
                p for p in _sig.parameters.values()
                if p.name != "self"
                and p.kind not in (_inspect.Parameter.VAR_POSITIONAL, _inspect.Parameter.VAR_KEYWORD)
            ]
            self._new_api = len(_pos_params) >= 2
        except (ValueError, TypeError):
            self._new_api = False

        try:
            self._strategy = strategy_cls() if not self._params else strategy_cls(**self._params)
        except TypeError:
            # 参数不匹配时用默认构造
            self._strategy = strategy_cls()

    # ------------------------------------------------------------------
    # 旧 API 桥接
    # ------------------------------------------------------------------

    def buy(self, price: float, size: int = 100) -> None:
        """旧 API buy → 入队 BUY Signal"""
        self._signals.append(Signal(
            symbol=self._symbol,
            direction=SignalDirection.BUY,
            price=float(price),
            volume=int(size),
            strategy_name="workshop",
        ))
        self.position.quantity += int(size)

    def sell(self, price: float, size: Optional[int] = None) -> None:
        """旧 API sell → 入队 SELL Signal"""
        qty = int(size or self.position.quantity)
        self._signals.append(Signal(
            symbol=self._symbol,
            direction=SignalDirection.SELL,
            price=float(price),
            volume=qty,
            strategy_name="workshop",
        ))
        self.position.quantity = 0

    # ------------------------------------------------------------------
    # 新 API 生命周期
    # ------------------------------------------------------------------

    def on_init(self, context: Context) -> None:
        """首次调用时加载用户代码"""
        if self._strategy is None:
            self._load()
        # 透传 params 到旧策略构造后的属性（若支持 set_parameters）
        legacy = self._strategy
        setter = getattr(legacy, "set_parameters", None)
        if callable(setter) and self._params:
            setter(self._params)

    def on_bar(self, context: Context, bar: BarData) -> List[Signal]:
        """新 API on_bar → 按策略 API 形态分发：新 API 直接返回信号；旧 API 桥接"""
        if self._strategy is None:
            self._load()
        if not self._symbol:
            self._symbol = bar.symbol  # 首次以实际标的为准

        legacy = self._strategy

        # ---- 新 API：on_bar(context, bar) -> List[Signal] ----
        if self._new_api:
            try:
                signals = legacy.on_bar(context, bar) or []
                # 规范化：确保 Symbol 对象带 strategy_name
                for sig in signals:
                    if not getattr(sig, "strategy_name", ""):
                        sig.strategy_name = "workshop"
                return list(signals)
            except Exception as e:
                logger.warning(f"策略 on_bar 执行异常: {e}")
                return []

        # ---- 旧 API：单参 on_bar(bar) + self.buy/sell 桥接 ----
        self.bars.append(bar)
        if hasattr(legacy, "bars"):
            legacy.bars = self.bars
        if hasattr(legacy, "position"):
            legacy.position = self.position

        self._signals.clear()
        self._bind_legacy_actions(legacy)

        try:
            legacy.on_bar(bar)
        except Exception as e:
            logger.warning(f"策略 on_bar 执行异常: {e}")

        result, self._signals = self._signals, []
        return result

    def _bind_legacy_actions(self, legacy: Any) -> None:
        """把旧策略实例的 buy/sell 绑定到适配器的桥接方法，并注入 bars/position 属性

        旧策略的 __init__ 常覆盖基类且不调用 super().__init__(),
        因此 bars/position 可能不存在——在 on_bar 前强制注入。
        """
        try:
            legacy.buy = self.buy  # type: ignore[method-assign]
            legacy.sell = self.sell  # type: ignore[method-assign]
        except Exception:
            pass
        # 注入旧 API 依赖的属性（若旧代码未在 __init__ 初始化）
        if not hasattr(legacy, "bars"):
            try:
                legacy.bars = self.bars
            except Exception:
                pass
        if not hasattr(legacy, "position"):
            try:
                legacy.position = self.position
            except Exception:
                pass
