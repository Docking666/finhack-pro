"""
选股筛选（Screening）

分三层，职责严格分离：

    factors.py   因子注册表 —— 定义"可筛什么"
    spec.py      FilterSpec 契约 —— 定义"筛什么"（LLM 的产出止步于此）
    compiler.py  自然语言 → FilterSpec —— LLM 唯一出场处，编译期一次
    engine.py    确定性执行 —— 无 LLM，同一输入永远同一输出

数据流::

    自然语言
      │   compiler.py（LLM × 1 次）
      ▼
    FilterSpec ─── 可缓存 / 可审计 / 可人工修订
      │   engine.py（确定性，逐因子向量化）
      ▼
    ScreenResult（命中列表 + 跳过清单 + 覆盖率）

为什么不逐股调用 LLM：

    5400 只 × 每次约 1 秒 ≈ 1.5 小时，且同一份输入两次跑出不同结果。
    成本、延迟、可复现性三条全不过关。LLM 的价值在"理解人的意图"，
    而不在"评价 5400 只股票" —— 后者是确定性的数值计算。
"""

from finhack_pro.screening.compiler import (
    ChatFn,
    CompileError,
    ConditionCompiler,
    chat_fn_from_llm_client,
)
from finhack_pro.screening.engine import ScreenEngine, ScreenHit, ScreenResult
from finhack_pro.screening.factors import (
    FactorFn,
    FactorRegistry,
    FactorSpec,
    build_default_factor_registry,
    register_level_factors,
)
from finhack_pro.screening.spec import (
    ALL_OPS,
    CATEGORY_OPS,
    NUMERIC_OPS,
    Condition,
    FilterSpec,
    SpecError,
)

__all__ = [
    # 因子层
    "FactorRegistry",
    "FactorSpec",
    "FactorFn",
    "build_default_factor_registry",
    "register_level_factors",
    # 契约层
    "FilterSpec",
    "Condition",
    "SpecError",
    "NUMERIC_OPS",
    "CATEGORY_OPS",
    "ALL_OPS",
    # 编译层（LLM 唯一出场处）
    "ConditionCompiler",
    "CompileError",
    "ChatFn",
    "chat_fn_from_llm_client",
    # 执行层
    "ScreenEngine",
    "ScreenResult",
    "ScreenHit",
]
