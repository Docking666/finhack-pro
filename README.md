# FinHack Pro - 多智能体量化交易系统

[![Rust](https://img.shields.io/badge/Rust-1.75+-orange.svg)](https://www.rust-lang.org)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/Docking666/finhack-pro?include_prereleases)](https://github.com/Docking666/finhack-pro/releases)

> 基于 **Rust + Python 混合架构** 的高性能多智能体量化交易系统，支持A股回测与实盘交易。7个AI智能体协同工作，覆盖技术面、基本面、舆情、微观事件等多维度分析。

## 目录

- [快速开始（推荐）](#快速开始推荐)
- [系统概述](#系统概述)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [安全与可靠性](#安全与可靠性)
- [回测引擎系统](#回测引擎系统)
- [性能加速模块](#性能加速模块)
- [Rust 核心桥接服务](#rust-核心桥接服务)
- [可观测性模块](#可观测性模块)
- [信号处理流水线](#信号处理流水线)
- [差异化策略框架](#差异化策略框架)
- [WebUI 管理界面](#webui-管理界面)
- [接口文档](#接口文档)
- [部署教程](#部署教程)
- [详细教程](#详细教程)
- [配置说明](#配置说明)
- [桌面版](#桌面版)
- [常见问题](#常见问题)
- [贡献指南](#贡献指南)

---

## 快速开始（推荐）

### 方式一：下载桌面版（零配置）

> 最简单的方式，无需安装任何开发环境。

1. 前往 [Releases](https://github.com/Docking666/finhack-pro/releases) 下载对应平台安装包
2. 安装并启动应用
3. 在"API配置"页面填入 OpenAI API Key
4. 开始体验回测和 Agent 分析

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows | `FinHack-Pro-*-x64-setup.exe` | Windows 64位安装包 |
| macOS (Intel) | `FinHack-Pro-*-x64.dmg` | macOS Intel芯片 |
| macOS (Apple Silicon) | `FinHack-Pro-*-arm64.dmg` | macOS M1/M2/M3芯片 |

### 方式二：Python 纯模式（推荐开发者）

> 只需 Python 3.10+，无需编译 Rust，5分钟上手。

```bash
# 1. 克隆仓库
git clone https://github.com/Docking666/finhack-pro.git
cd finhack-pro/python

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key（至少配一个 LLM 和一个数据源）
cp ../.env.example ../.env
# 编辑 .env，填入你的 API Key：
#   OPENAI_API_KEY=sk-xxx          (必需，驱动智能体分析)
#   TUSHARE_TOKEN=xxx              (可选，A股数据源)
#   不配 TUSHARE 也能用，系统会自动使用 AKShare 免费数据源

# 4. 运行智能体分析（示例：分析贵州茅台）
python -m finhack_pro.agents.coordinator --symbol 600519.SH

# 5. 或启动 WebUI 可视化界面
pip install fastapi uvicorn[standard] python-multipart
python -m finhack_pro.webui.app
# 浏览器访问 http://localhost:8000
```

### 方式三：完整模式（Rust + Python）

> 需要编译 Rust 核心，适合需要极致性能的场景。

```bash
# 1. 安装 Rust (https://rustup.rs)
# 2. 编译 Rust 核心
cargo build --release

# 3. 其余步骤同方式二
```

**环境要求汇总：**

| 组件 | 桌面版 | Python模式 | 完整模式 |
|------|--------|-----------|---------|
| Python 3.10+ | 内置 | **必需** | **必需** |
| Rust 1.75+ | 内置 | 不需要 | **必需** |
| OpenAI API Key | **必需** | **必需** | **必需** |
| Tushare Token | 可选 | 可选 | 可选 |
| PostgreSQL | 不需要 | 不需要 | 可选 |
| Redis | 不需要 | 不需要 | 可选 |

---

## 系统概述

FinHack Pro 是一个面向A股市场的多智能体量化交易系统，采用 **Rust 核心引擎 + Python 策略层** 的混合架构设计：

- **Rust 核心层**：高性能数据处理、风控引擎、执行引擎、回测引擎
- **Python 策略层**：7个AI智能体协同工作，支持LLM驱动的多空辩论
- **信号处理层**：7种滤波器 + 信号聚合器 + 策略验证框架
- **差异化策略**：5种面向个人投资者的微观策略

### 七智能体协作架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    七智能体分析流水线                               │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ╔══ Phase 1: 并行数据采集与分析（asyncio并发）════════════╗      │
│  ║                                                        ║      │
│  ║  市场数据 ──→ [市场分析Agent] ──→ 技术面分析报告        ║      │
│  ║  新闻数据 ──→ [新闻社媒Agent] ──→ 情感分析报告          ║      │
│  ║  财务数据 ──→ [基本面Agent]   ──→ 基本面分析报告        ║      │
│  ║  另类数据 ──→ [微观事件Agent] ──→ 微观事件报告          ║      │
│  ║                                                        ║      │
│  ╚════════════════════════════════════════════════════════╝      │
│                            ↓                                     │
│  ╔══ Phase 2: 串行决策与执行 ══════════════════════════════╗      │
│  ║                                                        ║      │
│  ║  四份报告 → [多空研究员] → 多空辩论 → 策略信号          ║      │
│  ║      ↓                                                 ║      │
│  ║  策略信号 → [信号聚合器] → 去重/加权/滤波 → 聚合信号    ║      │
│  ║      ↓                                                 ║      │
│  ║  聚合信号 → [风险管理Agent] → 风控决策                  ║      │
│  ║      ↓                                                 ║      │
│  ║  风控通过 → [交易执行Agent] → 执行报告                  ║      │
│  ║                                                        ║      │
│  ╚════════════════════════════════════════════════════════╝      │
│                                                                  │
│  ════════════════════════════════════════════════════════════    │
│  ↕ 共享记忆系统 (17种记忆类型，所有Agent读写)                     │
│  ↕ 共享工具集 (14个内置工具，所有Agent调用)                       │
└──────────────────────────────────────────────────────────────────┘
```

**关键设计：**
- **Phase 1 并行**：Step 1-4 使用 `asyncio.create_task` 并发执行，SharedMemory 内部有 `asyncio.Lock` 保护并发写入，单任务失败不阻塞其他任务
- **短路逻辑**：策略信号为 HOLD 时直接结束流水线
- **记忆衰减**：旧记忆自动降权，重要记忆持久化到 JSONL 文件

---

## 核心特性

### 智能体系统

| 智能体 | 角色 | 职责 | 核心能力 |
|--------|------|------|----------|
| **市场分析Agent** | MARKET_ANALYZER | 技术面分析 | RSI/MACD/布林带/均线系统、趋势判断 |
| **新闻社媒Agent** | NEWS_ANALYST | 舆情监控 | 新闻搜索、情感分析、事件识别 |
| **基本面Agent** | FUNDAMENTAL_ANALYST | 财务分析 | PE/PB/ROE/成长性、投资评级 |
| **微观事件Agent** | MICRO_EVENT_MONITOR | 微观事件监控 | 龙虎榜/大宗交易/北向资金/融资融券/交易所公告 |
| **多空研究员** | STRATEGY_GENERATOR | 策略生成 | **多空辩论机制**、对抗性思考 |
| **风险管理Agent** | RISK_MANAGER | 风控审核 | 仓位限制、VaR、回撤控制 |
| **交易执行Agent** | TRADE_EXECUTOR | 订单执行 | TWAP/VWAP、A股规则适配 |

### 共享基础设施

#### 共享记忆系统

- **17种记忆类型**：市场观察、分析报告、新闻事件、情感、策略决策、风控决策、执行记录、交易结果、Agent思考、系统事件、微观事件、另类数据、供应链、行业趋势、龙虎榜、交易所公告
- **4级重要性**：LOW / MEDIUM / HIGH / CRITICAL
- **多条件检索**：按类型、时间、关键词、标签、Agent筛选
- **记忆衰减**：旧记忆自动降权，重要记忆持久化到JSONL文件

#### 共享工具集

- **14个内置工具**（7个通用 + 7个另类数据）：

| 工具名称 | 分类 | 说明 |
|----------|------|------|
| `fetch_market_data` | 数据获取 | 获取A股日线/分钟线行情 |
| `calculate_indicator` | 技术分析 | 计算RSI/MACD/布林带等技术指标 |
| `search_news` | 舆情 | 搜索相关新闻 |
| `analyze_sentiment` | 舆情 | 新闻/社媒情感分析 |
| `fetch_fundamental` | 基本面 | 获取财务数据 |
| `get_portfolio_status` | 风控 | 获取组合状态 |
| `calculate_risk_metrics` | 风控 | 计算风险指标 |
| `fetch_dragon_tiger` | 另类数据 | 龙虎榜数据（游资/机构动向） |
| `fetch_exchange_notices` | 另类数据 | 交易所公告（停复牌/风险提示） |
| `fetch_sentiment_data` | 另类数据 | 股吧/雪球舆情数据 |
| `fetch_industry_hot` | 另类数据 | 行业板块热度排名 |
| `fetch_block_trade` | 另类数据 | 大宗交易数据 |
| `fetch_north_flow` | 另类数据 | 北向资金流入流出 |
| `fetch_margin_trading` | 另类数据 | 融资融券数据 |

- **LLM Function Calling**：自动生成 OpenAI/Anthropic 格式的工具描述
- **权限控制**：可按 Agent 角色限制工具访问

### 性能特性

- **Rust核心**：零成本抽象，Tokio异步运行时
- **内存安全**：所有权系统保证，无GC停顿
- **精确计算**：rust_decimal处理所有金融数值
- **A股规则**：涨跌停/T+1/手续费(万三+千一印花税)/滑点模拟
- **Phase 1 并行**：4个分析Agent并发执行，预计减少60%等待时间

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                      FinHack Pro 系统架构                         │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  🐍 Python 策略层 (AI智能体 + 策略研究 + 信号处理)                 │
│                                                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │ 市场分析Agent │ │新闻社媒Agent │ │ 基本面Agent  │  ← 并行     │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘             │
│  ┌──────┴───────┐       │               │                      │
│  │微观事件Agent  │       │               │  ← 并行              │
│  └──────┬───────┘       │               │                      │
│         └───────────────┼───────────────┘                      │
│                         ↓                                       │
│  ┌──────────────────────────────────────────┐                   │
│  │ 信号聚合器 + 滤波管道 (7种滤波器)         │                   │
│  │ 去重 → 加权 → KAMA/FRAMA/卡尔曼 → 聚合   │                   │
│  └──────────────────┬───────────────────────┘                   │
│                     ↓                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │ 多空研究员   │ │ 风险管理Agent │ │ 交易执行Agent │             │
│  │(策略生成)    │ │              │ │              │  ← 串行     │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
│                                                                  │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐             │
│  │ 策略验证框架 │ │ 差异化策略   │ │ 策略工坊     │             │
│  │(Walk-Forward │ │(小市值/事件  │ │(AI辅助生成)  │             │
│  │ Monte Carlo) │ │ 驱动/情绪反转)│ │              │             │
│  └──────────────┘ └──────────────┘ └──────────────┘             │
│                                                                  │
│  ════════════════════════════════════════════════════════════    │
│  共享记忆(SharedMemory, 17种类型) + 工具集(ToolRegistry, 14个)   │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  🦀 Rust 核心层 (高性能引擎，可选)                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │finhack-core│ │finhack-bus │ │finhack-risk│ │finhack-execution│
│  │ 核心类型  │ │ 消息总线  │ │ 风控引擎  │ │ 执行引擎  │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│  ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │finhack-backtest│ │finhack-data│ │finhack-api │ │finhack-bridge│   │
│  │ 回测引擎     │ │ 数据引擎  │ │ REST API │ │ Python桥接  │   │
│  └──────────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│  🗄️ 基础设施（可选）                                             │
│  ├── PostgreSQL  (数据持久化)                                    │
│  ├── Redis      (缓存/消息队列)                                  │
│  ├── InfluxDB   (时序数据)                                       │
│  └── Grafana    (监控面板)                                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 安全与可靠性

### 密钥安全管理 (`utils/security.py`)

| 组件 | 说明 |
|------|------|
| `SecretManager` | XOR+Base64 混淆存储密钥，支持环境变量加载，自动识别敏感字段（api_key/secret/token/password） |
| `mask_secrets(text)` | 正则脱敏文本中的密钥（OpenAI `sk-xxx`、Anthropic `sk-ant-xxx`、Tushare hex32、Bearer JWT） |
| `LogSanitizer` | 日志脱敏过滤器，集成 loguru，防止密钥泄露到日志 |

```python
from finhack_pro.utils import SecretManager, mask_secrets, LogSanitizer

# 密钥管理
sm = SecretManager()
sm.set("openai_key", "sk-abc123secret")
key = sm.get("openai_key")  # 自动解密

# 文本脱敏
safe = mask_secrets("API key is sk-abc123secret")
# 输出: "API key is sk-***3secret"

# 日志脱敏
sanitizer = LogSanitizer()
clean = sanitizer.sanitize("password=12345 and sk-ant-key789")
```

### LLM 调用保护 (`utils/circuit_breaker.py`)

三重防护机制，防止 LLM API 异常导致系统不可控：

| 组件 | 功能 | 关键参数 |
|------|------|----------|
| `CircuitBreaker` | 熔断器：连续失败达阈值自动熔断，超时后半开探测 | `fail_max=5`, `reset_timeout=60s` |
| `TokenBucket` | 令牌桶限流：平滑控制请求速率 | `rate=10/s`, `capacity=20` |
| `CostController` | 成本控制：追踪每日/每月 LLM 调用成本，超预算自动拒绝 | `daily_budget`, `monthly_budget` |

```python
from finhack_pro.utils import LLMProtection, get_llm_protection

# 使用统一保护器
protection = LLMProtection(
    circuit_fail_max=5,       # 连续失败5次熔断
    rate_limit=10,            # 每秒10个请求
    daily_budget=10.0,        # 每日预算$10
    monthly_budget=200.0,     # 每月预算$200
)

# 调用前检查
if protection.check_before_call():
    try:
        result = await llm.chat(...)
        protection.on_success(cost=0.05)
    except Exception:
        protection.on_failure()

# 或使用装饰器
breaker = CircuitBreaker(fail_max=3, reset_timeout=30)

@breaker.protect
async def call_llm(prompt):
    return await client.chat(prompt)
```

### 优雅降级 (`agents/coordinator.py`)

非关键 Agent 失败不会导致系统崩溃：

| 级别 | Agent | 失败处理 |
|------|-------|----------|
| **CRITICAL** | strategy_generator, risk_manager, trade_executor | 启动失败 → 系统报错退出 |
| NON_CRITICAL | market_analyzer, news_analyst, fundamental_analyst, micro_event_monitor | 启动失败 → 记录警告，系统继续运行 |

### 并发安全 (`agents/shared_memory.py`)

| 优化 | 说明 |
|------|------|
| `ShardedLock` | 分片锁（16片），按 `hash(key) % 16` 分配，减少并发竞争 |
| 原子写入 | `tempfile + os.replace` 实现崩溃安全的文件持久化 |

### 信号过滤器状态隔离 (`strategies/signal_filters.py`)

所有 7 种滤波器（异常检测、卡尔曼、自适应加权、KAMA、FRAMA、粒子滤波、Transformer）均实现**按标的独立状态**，通过 `BaseFilter._states[symbol]` 隔离，防止多标的回测时状态交叉污染。

---

## 回测引擎系统

### 设计目标

从物理层面消除**未来函数 (Look-ahead Bias)**，提供两种回测模式冷启动切换。

### 时间切片层 (`backtest/time_slice.py`)

| 组件 | 说明 |
|------|------|
| `DataBarrier` | 数据屏障：物理切片 DataFrame 到截止时间，拦截非法未来访问，抛出 `LookAheadError` |
| `PortfolioSnapshot` | 不可变组合快照：深拷贝 + SHA256 哈希校验，确保状态传递不可篡改 |
| `EngineSnapshot` | 不可变引擎完整状态快照（portfolio, bar, signals, orders, fills, data_barrier） |
| `LatencyConfig` | 延迟配置：data/compute/order/fill 四阶段延迟，自动计算总延迟 |
| `LatencySimulator` | 延迟模拟器：`get_fill_time()` 计算成交时间，`get_fill_price()` 使用成交时刻行情+滑点 |
| `TimeSliceContext` | 安全数据访问上下文：替代 `Context.data_feed`，提供 `get_history()` / `get_latest_bar()` |
| `LookAheadError` | 未来函数访问异常（含 access_time / current_time 用于调试） |

### 双模式引擎

| 模式 | 引擎 | 特点 | 适用场景 |
|------|------|------|----------|
| `VECTORIZED` | `VectorizedEngine` | 轻量级时间切片保护，性能开销 < 5% | 快速参数扫描、大规模回测 |
| `ASYNC_EVENT` | `AsyncEventEngine` | 完整延迟模拟 + 不可变快照 + 事件溯源 | 策略验证、合规审计 |

**关键设计差异**：异步引擎中 `signal_time ≠ fill_time`，信号产生后经过延迟模拟才成交，使用成交时刻的价格执行。

### 引擎工厂 (`backtest/engine_factory.py`)

```python
from finhack_pro.backtest import create_engine, run_backtest, compare_modes, BacktestMode

# 方式一：创建引擎
engine = create_engine(BacktestMode.VECTORIZED, config={"strict_mode": True})
result = engine.run(strategy, "600519.SH", data, params)

# 方式二：一键回测（自动处理同步/异步差异）
result = run_backtest(strategy, "600519.SH", data, mode="async_event")

# 方式三：双模式对比（自动诊断未来函数）
comparison = compare_modes(strategy, "600519.SH", data)
# 如果 vectorized_return > async_return * 1.05，输出未来函数警告
```

### 策略验证配置 (`strategies/strategy_validator.py`)

预定义 5 种验证配置，覆盖不同交易风格：

| 配置 | 最低交易次数 | 夏普比率 | 最大回撤 | Calmar | 适用场景 |
|------|-------------|----------|----------|--------|----------|
| `default` | 100 | ≥ 0.5 | ≤ 20% | ≥ 0.3 | 通用 |
| `conservative` | 200 | ≥ 1.0 | ≤ 10% | ≥ 0.5 | 稳健型 |
| `aggressive` | 50 | ≥ 0.3 | ≤ 30% | ≥ 0.2 | 激进型 |
| `high_frequency` | 500 | ≥ 0.8 | ≤ 15% | ≥ 0.4 | 高频 |
| `low_frequency` | 30 | ≥ 0.4 | ≤ 25% | ≥ 0.2 | 低频 |

```python
from finhack_pro.strategies import StrategyValidator

# 使用预定义配置
validator = StrategyValidator.from_profile("conservative")
result = validator.validate(performance_data)

# 或自定义配置
validator = StrategyValidator.from_config({
    "min_trades": 150,
    "min_sharpe": 0.8,
    "max_drawdown": 0.15,
})
```

---

## 性能加速模块

### NumPy 向量化引擎 (`backtest/accelerated.py`)

预提取 DataFrame 列为 NumPy 数组，预计算 BarData 对象，避免逐行 iterrows 开销：

```python
from finhack_pro.backtest import NumPyVectorizedEngine, NumPyEngineConfig

config = NumPyEngineConfig(
    initial_capital=1_000_000,
    enable_time_slice=True,
    strict_mode=True,
)
engine = NumPyVectorizedEngine(config)
result = engine.run(strategy, "600519.SH", data, {"fast": 5, "slow": 20})
```

### 多标的并行回测

使用 `asyncio.gather` + `Semaphore` 控制并发，支持同步和异步两种调用方式：

```python
from finhack_pro.backtest import run_multi_symbol_backtest, run_multi_symbol_async

# 同步调用
results = run_multi_symbol_backtest(
    strategy_factory=lambda: MyStrategy(),
    data_dict={"600519.SH": df1, "000858.SZ": df2, "601318.SH": df3},
    max_concurrent=3,
)

# 异步调用
results = await run_multi_symbol_async(
    strategy_factory=lambda: MyStrategy(),
    data_dict=data_dict,
    max_concurrent=5,
)
```

### Numba JIT 加速（可选）

热路径函数可选编译加速，无 Numba 时自动回退纯 NumPy：

```python
from finhack_pro.backtest import numba_jit_available, _calculate_drawdown_numpy

print(f"Numba可用: {numba_jit_available()}")

# 无论Numba是否安装，接口一致
max_dd, dd_curve = _calculate_drawdown_numpy(equity_array)
```

> **安装 Numba**：`pip install numba`，安装后自动启用 JIT 编译，无需修改代码。

---

## Rust 核心桥接服务

### 架构

```
Python (finhack_pro)                    Rust (finhack-bridge)
┌─────────────────┐                    ┌──────────────────┐
│ RustCoreBridge  │ ─── HTTP/JSON ──→ │ /health          │
│                 │                    │ /bridge/indicators│
│ 自动检测Rust    │ ←── 响应 ──────── │ /bridge/backtest  │
│ 不可用时回退    │                    │ /bridge/signals   │
└─────────────────┘                    └──────────────────┘
                                              ↑
                                        rayon 数据并行
```

### 三级降级策略

```
RustCoreBridge
  ├── Rust 服务可用？ → HTTP 调用 Rust（毫秒级计算）
  ├── Rust 不可用？   → Python ta 库回退（正常速度）
  └── ta 库不可用？   → 纯 NumPy 回退（基础功能）
```

### 桥接接口

| 接口 | 方法 | 说明 | Rust 内部实现 |
|------|------|------|---------------|
| `batch_calculate_indicators()` | POST | 批量技术指标（RSI/MACD/BB/ATR） | rayon 并行计算多指标 |
| `batch_backtest()` | POST | 批量回测（多策略并行） | rayon par_iter 并行策略 |
| `parallel_signal_compute()` | POST | 并行信号计算（分治-聚合） | rayon par_iter 并行标的 |

### 使用方式

```python
from finhack_pro.backtest import get_rust_bridge

bridge = get_rust_bridge()
print(f"Rust可用: {bridge.is_rust_available}")

# 批量指标计算（Rust可用时自动走Rust）
result_df = bridge.batch_calculate_indicators(data, ["rsi", "macd", "bollinger", "atr"])

# 批量回测
configs = [
    {"name": "MA_5_20", "fast_period": 5, "slow_period": 20},
    {"name": "MA_10_30", "fast_period": 10, "slow_period": 30},
]
results = bridge.batch_backtest(configs, data, initial_capital=1_000_000)

# 并行信号计算（分治-聚合）
results = bridge.parallel_signal_compute(data, symbols, strategy_factory, snapshot)
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FINHACK_BRIDGE_URL` | `http://localhost:8080` | 桥接服务地址 |
| `BRIDGE_HOST` | `0.0.0.0` | Rust 服务监听地址 |
| `BRIDGE_PORT` | `8080` | Rust 服务监听端口 |

### 性能参考（10000 bars）

| 操作 | Rust 内部计算 | 端到端（含HTTP） | Python 回退 |
|------|--------------|-----------------|-------------|
| 4 指标并行计算 | 0.6ms | ~5ms | ~41ms |
| 5 策略批量回测 | 0.27ms | ~5ms | N/A |
| 10 标的并行信号 | <0.1ms/标的 | ~5ms | ~2500ms |

> **注意**：端到端延迟包含 Python→JSON→HTTP 序列化开销。Rust 计算本身极快，瓶颈在数据传输层。未来可通过 PyO3 绑定消除此开销。

---

## 可观测性模块

### Prometheus 指标 (`utils/metrics.py`)

内置 9 个系统指标，支持 Prometheus 文本格式导出：

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `finhack_agent_calls_total` | Counter | agent | Agent 调用次数 |
| `finhack_agent_call_duration_seconds` | Histogram | agent | Agent 调用耗时 |
| `finhack_agent_errors_total` | Counter | agent | Agent 错误次数 |
| `finhack_llm_calls_total` | Counter | model, provider | LLM 调用次数 |
| `finhack_llm_tokens_total` | Counter | model, type | Token 用量 |
| `finhack_llm_cost_total` | Counter | model | LLM 调用成本 |
| `finhack_signals_total` | Counter | strategy, direction | 信号数量 |
| `finhack_memory_entries` | Gauge | type | 记忆条目数 |
| `finhack_websocket_connections` | Gauge | channel | WebSocket 连接数 |

```python
from finhack_pro.utils import get_metrics, track_agent_call, track_llm_call

metrics = get_metrics()

# 方式一：上下文管理器（自动记录次数和耗时）
with track_agent_call("market_analyzer"):
    result = await agent.analyze(...)

with track_llm_call("gpt-4o", "openai"):
    response = await client.chat(...)

# 方式二：手动记录
metrics.counter("custom_events").inc()
metrics.gauge("current_position").set(0.85)
metrics.histogram("order_size").observe(1000)

# 导出 Prometheus 格式
text = metrics.export_prometheus()
```

### WebSocket 心跳 (`webui/services.py`)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `heartbeat_interval` | 30s | 心跳发送间隔 |
| `heartbeat_timeout` | 90s | 超时断开阈值（3次未响应） |

自动检测僵尸连接并清理，防止 WebSocket 连接泄漏。

---

## 信号处理流水线

信号处理是连接智能体分析与交易决策的关键桥梁，提供从原始信号到可执行交易的完整处理链。

### 处理流程

```
原始信号 → 标准化 → 滤波管道 → 去重 → 加权投票 → L2正则化 → 置信度校准 → 仓位计算 → 聚合信号
```

### 信号聚合器 (SignalAggregator)

| 步骤 | 说明 |
|------|------|
| 信号标准化 | 统一不同来源的信号格式（base.Signal / StrategySignal） |
| 滤波管道处理 | 通过 SignalFilterPipeline 进行降噪 |
| 按标的分组 | 每个标的独立聚合 |
| 策略权重 | 支持手动指定或基于夏普比率/胜率自动计算 |
| 信号去重 | 相关系数 > 0.7 视为冗余（贪心算法保留高置信度） |
| L2正则化 | `confidence / (1 + λ * confidence²)` 防止过度自信 |
| 加权投票 | 确定最终方向（BUY/SELL/HOLD） |
| 置信度校准 | Sigmoid 温度缩放 |
| 仓位计算 | 最大单标的 30% |
| 风险因素识别 | 自动检测 6 类风险 |

### 信号滤波器 (7种)

| 滤波器 | 优先级 | 默认开启 | 性能开销 | 说明 |
|--------|--------|----------|----------|------|
| **异常检测** (AnomalyDetector) | 5 | ✅ | 低 | Z-Score/IQR/MAD 三种方法检测异常信号 |
| **卡尔曼滤波** (KalmanFilterFusion) | 10 | ✅ | 低 | 多源信号最优融合，动态噪声估计 |
| **自适应加权** (AdaptiveWeightedAverage) | 20 | ✅ | 低 | 基于历史 IC 的自适应权重分配 |
| **KAMA** (KAMAFilter) | 30 | ✅ | 低 | Kaufman 自适应移动平均，趋势/震荡自动切换 |
| **FRAMA** (FRAMAFilter) | 31 | ✅ | 低 | 分形自适应移动平均，基于分形维度的快慢切换 |
| **粒子滤波** (ParticleFilter) | 15 | ❌ | **高** | 蒙特卡洛粒子滤波，适合非线性非高斯场景 |
| **Transformer注意力** (TransformerAttentionFusion) | 50 | ❌ | **高** | Transformer 多头注意力信号融合 |

> **设计原则**：P1（卡尔曼+自适应加权）和 P2（KAMA/FRAMA+异常检测）默认开启，性能开销低；P3（Transformer+粒子滤波）默认关闭，需要时手动启用。

### 策略验证框架 (StrategyValidator)

每次策略信号生成后，自动进行 7 项验证：

| 检查项 | 默认门槛 | 说明 |
|--------|----------|------|
| 最低交易次数 | ≥ 100 | 防止样本过少导致统计不显著 |
| 夏普比率 | ≥ 0.5 | 风险调整后的收益水平 |
| 最大回撤 | ≤ 20% | 风险控制能力 |
| Calmar 比率 | ≥ 0.3 | 年化收益与最大回撤的比值 |
| Walk-Forward 分析 | WF得分 > 0.5 | 5窗口滚动验证，检测过拟合 |
| Monte Carlo 模拟 | 盈利占比 ≥ 60% | 1000次随机重采样，检验稳健性 |
| 策略相关性 | < 0.5 | 与现有策略的低相关性 |

### 使用示例

```python
from finhack_pro.strategies import (
    SignalAggregator, SignalFilterPipeline, create_default_pipeline,
    StrategyValidator, KalmanFilterFusion, KAMAFilter
)

# 创建滤波管道（默认配置：P1+P2开启，P3关闭）
pipeline = create_default_pipeline()

# 或自定义配置
pipeline = SignalFilterPipeline()
pipeline.add_filter(KalmanFilterFusion())       # 卡尔曼滤波
pipeline.add_filter(KAMAFilter(period=10))       # KAMA
# pipeline.add_filter(TransformerAttentionFusion())  # 需手动开启

# 创建聚合器
aggregator = SignalAggregator(filter_pipeline=pipeline)

# 聚合信号
result = aggregator.aggregate(signals, apply_filters=True)
print(f"方向: {result.direction}, 置信度: {result.confidence:.2%}")

# 策略验证
validator = StrategyValidator()
validation = validator.validate(strategy_performance)
print(f"验证通过: {validation.passed}, 得分: {validation.overall_score}")
```

---

## 差异化策略框架

> 核心理念：**机构做广度，个人做深度** —— 聚焦机构看不上的微观机会。

### 5种差异化策略

| 策略 | 适用场景 | 核心逻辑 |
|------|----------|----------|
| **小市值策略** (MICRO_CAP) | 小盘股放量突破 | 放量突破 + 市值/换手率约束，捕捉小盘股流动性溢价 |
| **事件驱动策略** (EVENT_DRIVEN) | 公告/停复牌/业绩预告 | 基于微观事件的快速响应，抢跑机构研报 |
| **情绪反转策略** (SENTIMENT_REVERSAL) | 极端舆情 | 极度悲观买入/极度乐观卖出，逆向投资 |
| **龙虎榜跟随** (DRAGON_TIGER_FOLLOW) | 游资/机构异动 | 跟踪知名游资席位和机构动向 |
| **另类数据交叉** (ALTERNATIVE_CROSS) | 多维度共振 | 北向资金+融资融券+大宗交易+行业热度 ≥ 3个信号共振 |

### 使用示例

```python
from finhack_pro.strategies import create_niche_strategy, NicheType

# 创建小市值策略
strategy = create_niche_strategy(NicheType.MICRO_CAP, config={
    "max_position_ratio": 0.1,    # 单标的最大仓位10%
    "max_market_cap": 100,         # 最大市值100亿
    "min_confidence": 0.6,         # 最低置信度60%
})

# 创建另类数据交叉策略
strategy = create_niche_strategy(NicheType.ALTERNATIVE_CROSS, config={
    "min_signals": 3,              # 至少3个信号共振
    "signal_weights": {
        "north_flow": 0.3,
        "margin_trading": 0.25,
        "block_trade": 0.2,
        "industry_hot": 0.15,
        "sentiment": 0.1,
    },
})
```

---

## WebUI 管理界面

FinHack Pro 内置了一个现代化的 Web 管理界面，提供可视化的系统管理和监控能力。

### 功能概览

| 页面 | 功能 |
|------|------|
| **仪表盘** | 系统概览、Agent状态、最近执行记录、快速操作 |
| **API配置** | LLM API Key管理、数据源配置、风控参数、连接测试 |
| **回测面板** | 策略选择、参数配置、实时权益曲线、回测结果展示 |
| **Agent监控** | 7个Agent实时状态、**LLM思考过程流式展示**、多空辩论可视化 |
| **记忆浏览器** | 共享记忆搜索/浏览/管理、记忆统计、类型分布 |
| **策略工坊** | AI辅助生成策略和因子、策略模板库、可视化因子编辑器 |

### 界面特色

- **深色主题**：专为量化交易场景设计的暗色界面
- **实时推送**：WebSocket 连接，回测进度和 Agent 思考过程实时更新
- **思考过程可视化**：类似 ChatGPT 的对话界面，实时展示每个 Agent 的分析推理过程
- **多空辩论展示**：多头论点（绿色）vs 空头论点（红色）对比展示
- **Markdown 渲染**：Agent 输出的结构化分析报告支持完整 Markdown 渲染
- **响应式设计**：适配桌面和平板设备

### Agent 思考过程展示

```
┌──────────────────────────────────────────────────────────┐
│  🤖 市场分析Agent                              2.3s ✓   │
│  ──────────────────────────────────────────────────────  │
│  分析 600519.SH 的技术面...                               │
│  RSI(14) = 65.3，处于中性偏强区域                         │
│  MACD：DIF上穿DEA形成金叉，多头信号明确                    │
│  布林带：价格接近上轨，短期有回调压力                       │
│  结论：短期看多，中期震荡偏强                              │
├──────────────────────────────────────────────────────────┤
│  🔍 微观事件Agent                              1.5s ✓   │
│  ──────────────────────────────────────────────────────  │
│  龙虎榜：机构净买入 2300万，游资席位活跃                   │
│  北向资金：连续3日净流入，今日+1.2亿                       │
│  融资融券：融资余额增加 3.2%，杠杆资金看多                  │
│  结论：资金面偏多，微观信号积极                            │
├──────────────────────────────────────────────────────────┤
│  ⚔️ 多空辩论                                  4.1s ✓    │
│  ──────────────────────────────────────────────────────  │
│  🟢 多头论点：                                            │
│  · 营收超预期增长，基本面改善                              │
│  · MACD金叉确认，技术面转多                                │
│  · 北向资金持续流入，机构看好                              │
│  🔴 空头论点：                                            │
│  · 估值处于历史高位(PE>35)，存在回调风险                   │
│  · 行业政策不确定性增加                                    │
│  ⚖️ 裁决：看多(置信度72%)，建议轻仓参与                    │
└──────────────────────────────────────────────────────────┘
```

### WebUI API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system/info` | 系统信息 |
| GET | `/api/config` | 获取配置 |
| PUT | `/api/config` | 更新配置 |
| POST | `/api/config/test-connection` | 测试API连接 |
| POST | `/api/backtest/run` | 启动回测 |
| GET | `/api/backtest/{id}/result` | 回测结果 |
| GET | `/api/agents/list` | Agent列表 |
| POST | `/api/agents/run-pipeline` | 运行分析流水线 |
| GET | `/api/memory/search` | 搜索记忆 |
| WS | `/ws/agents` | Agent思考流 |
| WS | `/ws/backtest` | 回测进度 |
| WS | `/ws/system` | 系统事件 |

---

## 接口文档

### Python 模块导出索引

#### `finhack_pro.utils` — 工具模块（22 个导出）

| 接口 | 类型 | 说明 |
|------|------|------|
| `SecretManager` | class | 密钥管理器（XOR 混淆存储） |
| `get_secret_manager()` | function | 全局密钥管理器单例 |
| `mask_secrets(text)` | function | 正则脱敏密钥文本 |
| `LogSanitizer` | class | 日志脱敏过滤器 |
| `sanitize_log(message)` | function | 便捷日志脱敏 |
| `CircuitBreaker` | class | 熔断器（CLOSED/OPEN/HALF_OPEN） |
| `CircuitBreakerOpenError` | exception | 熔断开启异常 |
| `TokenBucket` | class | 令牌桶限流器 |
| `CostController` | class | 成本控制器（日/月预算） |
| `LLMProtection` | class | LLM 调用保护（熔断+限流+预算） |
| `RateLimitExceededError` | exception | 限流异常 |
| `BudgetExceededError` | exception | 预算超限异常 |
| `get_llm_protection()` | function | 全局 LLM 保护器单例 |
| `MetricsCollector` | class | Prometheus 指标收集器 |
| `get_metrics()` | function | 全局指标收集器单例 |
| `track_agent_call(name)` | contextmanager | 追踪 Agent 调用 |
| `track_llm_call(model, provider)` | contextmanager | 追踪 LLM 调用 |
| `track_llm_tokens(model, prompt, completion, cost)` | function | 记录 Token 用量 |
| `track_signal_processing(strategy, count, duration)` | function | 记录信号处理 |
| `track_memory_operation(op, success)` | function | 记录记忆操作 |
| `update_memory_entries(count, type)` | function | 更新记忆条目数 |
| `update_websocket_connections(channel, count)` | function | 更新 WebSocket 连接数 |

#### `finhack_pro.backtest` — 回测引擎（22 个导出）

| 接口 | 类型 | 说明 |
|------|------|------|
| `BacktestRunner` | class | 原有回测运行器 |
| `BacktestResult` | class | 回测结果 |
| `BacktestMode` | enum | 回测模式（VECTORIZED / ASYNC_EVENT） |
| `DataBarrier` | class | 数据屏障（物理切片防未来函数） |
| `TimeSliceContext` | class | 时间切片安全上下文 |
| `PortfolioSnapshot` | dataclass | 不可变组合快照 |
| `EngineSnapshot` | dataclass | 不可变引擎快照 |
| `LatencyConfig` | dataclass | 延迟配置（4 阶段） |
| `LatencySimulator` | class | 延迟模拟器 |
| `LookAheadError` | exception | 未来函数访问异常 |
| `EngineResult` | dataclass | 引擎回测结果 |
| `create_engine(mode, config)` | function | 创建回测引擎 |
| `run_backtest(strategy, symbol, data, ...)` | function | 一键运行回测 |
| `compare_modes(strategy, symbol, data, ...)` | function | 双模式对比（诊断未来函数） |
| `NumPyVectorizedEngine` | class | NumPy 向量化引擎 |
| `NumPyEngineConfig` | dataclass | NumPy 引擎配置 |
| `run_multi_symbol_backtest(factory, data, concurrent)` | function | 多标的并行回测（同步） |
| `run_multi_symbol_async(factory, data, concurrent)` | function | 多标的并行回测（异步） |
| `MultiSymbolResult` | dataclass | 多标的回测结果 |
| `numba_jit_available()` | function | 检查 Numba 可用性 |
| `RustCoreBridge` | class | Rust 核心桥接接口 |
| `get_rust_bridge()` | function | 获取全局桥接实例 |

#### `finhack_pro.strategies` — 策略库（29 个导出）

| 接口 | 类型 | 说明 |
|------|------|------|
| `BaseStrategy` | class | 策略基类 |
| `Context` | class | 策略上下文 |
| `Signal` | class | 交易信号 |
| `SignalAggregator` | class | 信号聚合器 |
| `SignalFilterPipeline` | class | 信号滤波管线 |
| `create_default_pipeline()` | function | 创建默认滤波管线 |
| `StrategyValidator` | class | 策略验证器 |
| `StrategyValidator.from_profile(name)` | classmethod | 从预定义配置创建 |
| `StrategyValidator.from_config(config)` | classmethod | 从自定义配置创建 |
| `VALIDATION_PROFILES` | dict | 预定义验证配置（5 种） |
| `KalmanFilterFusion` | class | 卡尔曼滤波融合 |
| `AdaptiveWeightedAverage` | class | 自适应加权平均 |
| `KAMAFilter` | class | KAMA 滤波器 |
| `FRAMAFilter` | class | FRAMA 滤波器 |
| `AnomalyDetector` | class | 异常检测器 |
| `ParticleFilter` | class | 粒子滤波器 |
| `TransformerAttentionFusion` | class | Transformer 注意力融合 |
| `NicheType` | class | 差异化策略类型枚举 |
| `create_niche_strategy(type, config)` | function | 差异化策略工厂 |
| `DualThrustStrategy` | class | Dual Thrust 突破策略 |
| `MomentumStrategy` | class | 动量策略 |
| `MeanReversionStrategy` | class | 均值回归策略 |

#### `finhack_pro.agents` — Agent 系统（20 个导出）

| 接口 | 类型 | 说明 |
|------|------|------|
| `AgentCoordinator` | class | Agent 协调器 |
| `BaseAgent` | class | Agent 基类 |
| `LLMClient` | class | LLM 客户端（已集成 LLMProtection） |
| `AgentRole` | class | Agent 角色枚举 |
| `AgentMessage` | class | Agent 消息 |
| `MarketAnalyzerAgent` | class | 市场分析 Agent |
| `MicroEventAgent` | class | 微观事件 Agent |
| `StrategyGeneratorAgent` | class | 策略生成 Agent |
| `RiskManagerAgent` | class | 风险管理 Agent |
| `TradeExecutorAgent` | class | 交易执行 Agent |

### Rust 桥接服务 HTTP API

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| GET | `/health` | — | `{code, data: {status, version, rust_version, rayon_threads}}` | 健康检查 |
| POST | `/bridge/indicators` | `{data: [{open,high,low,close,volume}], indicators: ["rsi","macd","bollinger","atr"]}` | `{code, data: {rsi, macd, bb_upper, bb_middle, bb_lower, atr, computation_time_ms}}` | 批量指标计算 |
| POST | `/bridge/batch_backtest` | `{strategy_configs: [{name, fast_period, slow_period}], data: [...], initial_capital}` | `{code, data: {results: [{strategy_name, total_return, max_drawdown, sharpe_ratio, total_trades}], total_time_ms}}` | 批量回测 |
| POST | `/bridge/parallel_signals` | `{symbols_data: [{symbol, bars}], fast_period, slow_period}` | `{code, data: {results: [{symbol, total_return, sharpe_ratio, total_trades}], total_time_ms}}` | 并行信号计算 |

> 所有响应格式：`{code: 0, message: "success", data: {...}}`，`code=0` 表示成功。

---

## 部署教程

### 方式一：Python 纯模式（推荐）

只需 Python 3.10+，无需编译 Rust，5 分钟上手。

```bash
# 1. 克隆仓库
git clone https://github.com/Docking666/finhack-pro.git
cd finhack-pro/python

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key
cp ../.env.example ../.env
# 编辑 .env，填入 OPENAI_API_KEY=sk-xxx

# 4. 运行
python -m finhack_pro.agents.coordinator --symbol 600519.SH
# 或启动 WebUI
python -m finhack_pro.webui.app
```

### 方式二：完整模式（Rust + Python）

编译 Rust 核心并启动桥接服务，获得最佳计算性能。

```bash
# 1. 克隆仓库
git clone https://github.com/Docking666/finhack-pro.git
cd finhack-pro

# 2. 安装 Rust 工具链
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# 3. 编译 Rust 核心（Release 模式）
cargo build --release

# 4. 编译桥接服务
cargo build -p finhack-bridge --release

# 5. 启动桥接服务（可选，后台运行）
BRIDGE_PORT=8080 ./target/release/finhack-bridge &

# 6. 安装 Python 依赖
cd python
pip install -r requirements.txt
pip install httpx  # 桥接通信依赖

# 7. 配置并运行
cp ../.env.example ../.env
# 编辑 .env，填入 OPENAI_API_KEY
python -m finhack_pro.webui.app
```

### 方式三：国内镜像加速

如果 Rust 下载缓慢，使用国内镜像：

```bash
# 使用清华镜像安装 Rust
export RUSTUP_DIST_SERVER=https://mirrors.ustc.edu.cn/rust-static
export RUSTUP_UPDATE_ROOT=https://mirrors.ustc.edu.cn/rust-static/rustup
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 编译时也使用镜像
export RUSTUP_DIST_SERVER=https://mirrors.ustc.edu.cn/rust-static
cargo build --release
```

### 环境变量汇总

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `OPENAI_API_KEY` | 是 | — | OpenAI API Key |
| `OPENAI_API_BASE` | 否 | — | 自定义 API 地址（支持 Ollama） |
| `ANTHROPIC_API_KEY` | 否 | — | Anthropic API Key |
| `TUSHARE_TOKEN` | 否 | — | Tushare 数据源 Token |
| `FINHACK_BRIDGE_URL` | 否 | `http://localhost:8080` | Rust 桥接服务地址 |
| `BRIDGE_HOST` | 否 | `0.0.0.0` | 桥接服务监听地址 |
| `BRIDGE_PORT` | 否 | `8080` | 桥接服务监听端口 |
| `RUST_LOG` | 否 | `info` | Rust 日志级别 |

### 依赖版本要求

| 组件 | 最低版本 | 推荐版本 |
|------|----------|----------|
| Python | 3.10 | 3.11+ |
| Rust | 1.75 | 1.95+ |
| Node.js（桌面版） | 18 | 20 LTS |
| pip 依赖 | 见 `requirements.txt` | 最新稳定版 |

### 可选增强

```bash
# Numba JIT 加速（回测热路径编译优化）
pip install numba

# Prometheus 监控集成
# 将 metrics.export_prometheus() 接入 Prometheus scrape 端点

# PDF/Excel 导出
pip install reportlab openpyxl xlsxwriter
```

---

## 详细教程

### 教程一：配置数据源

#### Tushare 配置（可选）

Tushare 是A股数据的主要来源，需要注册获取 Token：

```bash
# 1. 访问 https://tushare.pro 注册账号
# 2. 在个人中心获取 Token
# 3. 设置环境变量
export TUSHARE_TOKEN=your_token
```

#### AKShare（免费备选，无需配置）

系统默认使用 AKShare 作为免费数据源，无需任何配置即可使用：

```python
from finhack_pro.data.fetcher import DataFetcher

fetcher = DataFetcher()
df = fetcher.get_daily("600519.SH", "2024-01-01", "2024-12-31")
```

### 教程二：运行回测

```python
from finhack_pro.strategies.dual_thrust import DualThrustStrategy
from finhack_pro.backtest.runner import BacktestRunner

# 创建策略
strategy = DualThrustStrategy({
    "symbols": ["600519.SH"],
    "k1": 0.5, "k2": 0.5, "lookback": 20,
})

# 运行回测
runner = BacktestRunner()
result = runner.run(
    strategy=strategy,
    start_date="2023-01-01",
    end_date="2024-12-31",
    initial_capital=1000000,
)

print(f"总收益率: {result.total_return:.2%}")
print(f"夏普比率: {result.sharpe_ratio:.2f}")
print(f"最大回撤: {result.max_drawdown:.2%}")
```

### 教程三：使用智能体系统

```python
from finhack_pro.agents.coordinator import AgentCoordinator
import asyncio

async def main():
    config = {
        "agents": {
            "market_analyzer": {"model": "gpt-4o", "temperature": 0.3},
            "news_analyst": {"model": "gpt-4o", "temperature": 0.3},
            "fundamental_analyst": {"model": "gpt-4o", "temperature": 0.2},
            "micro_event_monitor": {"model": "gpt-4o", "temperature": 0.3},
            "strategy_generator": {"model": "gpt-4o", "temperature": 0.5},
            "risk_manager": {"enabled": True},
            "trade_executor": {"enabled": True},
        },
        "shared_memory": {
            "enabled": True,
            "persist_dir": "./data/memory",
        },
        "tool_registry": {"enabled": True},
    }

    coordinator = AgentCoordinator(config)
    await coordinator.start()

    # 运行分析流水线（Phase 1 四个Agent并行执行）
    result = await coordinator.run_analysis_pipeline(
        symbol="600519.SH",
        market_data=df,
    )

    print(f"策略信号: {result['strategy_signal']}")
    print(f"风控决策: {result['risk_decision']}")
    print(f"执行报告: {result['execution_report']}")

    await coordinator.stop()

asyncio.run(main())
```

### 教程四：共享记忆系统

```python
from finhack_pro.agents.shared_memory import SharedMemory, MemoryType, MemoryImportance

memory = SharedMemory(persist_dir="./data/memory")

# 存储记忆
memory_id = await memory.store(
    agent_id="market_analyzer",
    memory_type=MemoryType.ANALYSIS_REPORT,
    content="贵州茅台技术面分析：突破2000元关口，MACD金叉",
    structured_data={"signal": "bullish", "confidence": 0.85},
    importance=MemoryImportance.HIGH,
    tags=["600519.SH", "breakout", "macd"],
)

# 检索记忆
reports = await memory.retrieve(
    memory_type=MemoryType.MICRO_EVENT,
    keywords=["龙虎榜", "北向"],
    limit=10,
)
```

### 教程五：自定义工具

```python
from finhack_pro.agents.tool_registry import BaseTool, ToolDefinition, ToolCategory, ToolParameter

class MyTool(BaseTool):
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="my_tool",
            description="我的自定义工具",
            category=ToolCategory.UTILITY,
            parameters=[
                ToolParameter("input", "string", "输入参数"),
            ],
        )

    async def execute(self, **kwargs) -> Any:
        return {"result": f"处理结果: {kwargs['input']}"}

# 注册到工具集
registry.register(MyTool())
```

### 教程六：自定义策略

```python
from finhack_pro.strategies.base import BaseStrategy, Signal, Context
import pandas as pd

class MyStrategy(BaseStrategy):
    def on_init(self, context: Context) -> None:
        self.fast_ma = 5
        self.slow_ma = 20

    def on_bar(self, context: Context, bar: pd.DataFrame) -> list:
        signals = []
        df = context.data_feed.get_bars(self.symbols[0], 30)
        df['ma5'] = df['close'].rolling(self.fast_ma).mean()
        df['ma20'] = df['close'].rolling(self.slow_ma).mean()

        # 金叉买入
        if df['ma5'].iloc[-1] > df['ma20'].iloc[-1] and \
           df['ma5'].iloc[-2] <= df['ma20'].iloc[-2]:
            signals.append(Signal(
                symbol=self.symbols[0],
                direction=1, price=bar['close'], volume=100,
            ))
        return signals
```

---

## 配置说明

### 最小配置（只需 LLM API Key）

```yaml
# config/default.yaml
agents:
  market_analyzer:
    model: "gpt-4o"          # 或其他兼容模型
    temperature: 0.3
  news_analyst:
    model: "gpt-4o"
    temperature: 0.3
  fundamental_analyst:
    model: "gpt-4o"
    temperature: 0.2
  micro_event_monitor:
    model: "gpt-4o"
    temperature: 0.3
  strategy_generator:
    model: "gpt-4o"
    temperature: 0.5
    enable_debate: true       # 启用多空辩论
  risk_manager:
    enabled: true
  trade_executor:
    enabled: true

shared_memory:
  enabled: true
  persist_dir: "./data/memory"
```

### 完整配置

```yaml
system:
  name: "FinHack Pro"
  version: "1.0.0"
  mode: "backtest"           # backtest / paper / live

data:
  storage_type: "csv"
  data_dir: "./data"
  sources:
    - name: "tushare"
      token: "${TUSHARE_TOKEN}"
      priority: 1
    - name: "akshare"        # 免费备选，无需Token
      priority: 2

risk:
  max_position_pct: 0.2      # 单标的最大仓位20%
  max_drawdown: 0.15         # 最大回撤15%
  var_limit: 0.05            # 日VaR限制5%
  max_leverage: 2.0
  daily_loss_limit: 0.03     # 日亏损限制3%

execution:
  algorithm: "twap"          # TWAP / VWAP / iceberg
  slippage_bps: 2
  commission_rate: 0.0003    # 佣金万三
  stamp_tax_rate: 0.001      # 印花税千一

backtest:
  initial_capital: 1000000
  start_date: "2023-01-01"
  end_date: "2024-12-31"
  benchmark: "000300.SH"

# 信号滤波配置
signal_filters:
  enable_high_cost: false    # 是否开启高开销滤波器（Transformer/粒子滤波）
  anomaly_method: "mad"      # 异常检测方法: zscore / iqr / mad
  kama_period: 10
  frama_period: 20
```

---

## 桌面版

FinHack Pro 提供开箱即用的桌面版应用，无需配置开发环境。

### 下载

> 桌面版通过 GitHub Actions 自动构建，前往 [Releases](https://github.com/Docking666/finhack-pro/releases) 页面下载最新版本。

| 平台 | 文件 | 说明 |
|------|------|------|
| Windows | `FinHack-Pro-*-x64-setup.exe` | Windows 64位安装包 |
| macOS (Intel) | `FinHack-Pro-*-x64.dmg` | macOS Intel芯片 |
| macOS (Apple Silicon) | `FinHack-Pro-*-arm64.dmg` | macOS M1/M2/M3芯片 |

### 自动构建

项目已配置 GitHub Actions CI/CD，每次推送代码到 main 分支时自动构建：

1. 进入仓库 **Actions** 页面
2. 选择 **Release Build** 工作流
3. 点击 **Run workflow**
4. 等待构建完成（约15-20分钟）
5. 构建产物会自动上传到 [Releases](https://github.com/Docking666/finhack-pro/releases)

### 功能特点

- 双击启动，无需命令行
- 预置茅台、平安银行等热门标的数据
- 可视化配置界面
- 一键回测和结果导出
- 7个Agent思考过程实时展示
- 策略工坊：AI辅助生成策略和因子

### 首次使用

1. 下载并安装应用
2. 启动后在"API配置"页面填入 OpenAI API Key
3. 开始体验回测和 Agent 分析

---

## 常见问题

### Q1: 必须配置 Tushare Token 吗？

**不需要。** 系统默认使用 AKShare 免费数据源，无需任何配置。Tushare 是可选的高级数据源，提供更丰富的数据。

### Q2: 必须编译 Rust 吗？

**不需要。** Python 纯模式可以独立运行所有智能体和策略功能。Rust 核心层是可选的性能增强，适合需要极致回测速度的场景。

### Q3: LLM API 费用如何？

- 每次完整分析流水线约消耗 8000-15000 tokens（7个Agent + 多空辩论）
- 使用 GPT-4o 每次分析约 $0.05-0.10
- 建议设置预算限制或使用本地模型（如 Ollama + Qwen）

### Q4: 如何使用本地 LLM 替代 OpenAI？

```yaml
agents:
  market_analyzer:
    model: "http://localhost:11434/v1/qwen2.5"  # Ollama 本地模型
    temperature: 0.3
```

或设置环境变量：
```bash
export OPENAI_API_BASE=http://localhost:11434/v1
export OPENAI_API_KEY=ollama  # Ollama 不需要真实Key
```

### Q5: 如何接入实盘交易？

目前支持模拟交易，实盘接口需要：
1. 开通券商 API（如中泰XTP、迅投QMT）
2. 在 `execution` 模块实现对应接口
3. 配置 `mode: live` 并设置风控参数

### Q6: 如何调试智能体？

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# 查看共享记忆
stats = await coordinator.get_memory_stats()
print(stats)

# 查看工具调用日志
tool_stats = coordinator.get_tool_stats()
print(tool_stats)
```

---

## 贡献指南

欢迎提交 Issue 和 PR！

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/my-feature`
3. 提交更改：`git commit -am 'Add some feature'`
4. 推送分支：`git push origin feature/my-feature`
5. 提交 Pull Request

---

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

---

## 致谢

- [FinHack](https://github.com/FinHackCN/finhack) - 原版 FinHack 框架
- [Barter](https://github.com/barter-rs/barter-rs) - Rust 量化交易框架参考
- [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) - 架构设计参考

---

## 策略工坊

策略工坊是 FinHack Pro 的低代码策略开发平台，大幅降低量化策略和因子的开发门槛。

### AI辅助生成

用自然语言描述你的交易想法，AI自动生成可运行的策略代码：

```
用户输入: "当RSI低于30且MACD金叉时买入，RSI高于70且死叉时卖出，适合A股短线交易"
AI输出:   完整的Python策略类代码，包含参数配置、入场/出场逻辑、风控规则
```

**支持的生成类型：**
- 策略生成 - 描述交易逻辑，生成完整策略代码
- 因子生成 - 描述因子逻辑，生成因子计算函数
- 支持A股/港股/美股市场
- 支持短线/中线/长线风格
- 自动代码验证和语法检查

### 策略模板库

内置6个经典策略模板，开箱即用：

| 策略 | 类型 | 难度 | 说明 |
|------|------|------|------|
| Dual Thrust 突破 | 趋势跟踪 | ⭐⭐ | 经典N日突破策略 |
| RSI 均值回归 | 均值回归 | ⭐⭐ | 超买超卖反转策略 |
| MACD 金叉死叉 | 趋势跟踪 | ⭐ | 最经典的趋势策略 |
| 布林带突破 | 波动率 | ⭐⭐ | 基于布林带通道的突破 |
| 动量轮动 | 多因子 | ⭐⭐⭐ | 多标的动量排名轮动 |
| 海龟交易法则 | 趋势跟踪 | ⭐⭐⭐ | ATR动态止损+金字塔加仓 |

### 可视化因子编辑器

无需编写代码，通过表单配置即可创建自定义因子：

1. 设置因子名称和类别
2. 添加输入参数（如周期、阈值等）
3. 输入计算公式（如 `bars[-1].close / bars[-21].close - 1`）
4. 添加过滤条件（如 `volume > avg_volume * 1.5`）
5. 一键生成Python因子代码

---

**免责声明**：本系统仅供学习和研究使用，不构成投资建议。量化交易有风险，入市需谨慎。
