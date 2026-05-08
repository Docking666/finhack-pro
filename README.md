# FinHack Pro - 多智能体量化交易系统

[![Rust](https://img.shields.io/badge/Rust-1.75+-orange.svg)](https://www.rust-lang.org)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> 🚀 基于 **Rust + Python 混合架构** 的高性能多智能体量化交易系统，支持A股回测与实盘交易。

## 📖 目录

- [系统概述](#系统概述)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [快速开始](#快速开始)
- [WebUI 管理界面](#webui-管理界面)
- [详细教程](#详细教程)
- [API文档](#api文档)
- [配置说明](#配置说明)
- [常见问题](#常见问题)

---

## 系统概述

FinHack Pro 是一个面向A股市场的多智能体量化交易系统，采用 **Rust 核心引擎 + Python 策略层** 的混合架构设计：

- **Rust 核心层**：高性能数据处理、风控引擎、执行引擎、回测引擎
- **Python 策略层**：6个AI智能体协同工作，支持LLM驱动的多空辩论

### 六智能体协作架构

```
┌─────────────────────────────────────────────────────────────┐
│                    六智能体分析流水线                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  市场数据 → [市场分析Agent] → 技术面分析报告                   │
│     ↓                                                       │
│  新闻数据 → [新闻社媒Agent] → 情感分析报告                     │
│     ↓                                                       │
│  财务数据 → [基本面Agent]   → 基本面分析报告                   │
│     ↓                                                       │
│  三方报告 → [多空研究员]    → 多空辩论 → 策略信号              │
│     ↓                                                       │
│  策略信号 → [风险管理Agent] → 风控决策                        │
│     ↓                                                       │
│  风控通过 → [交易执行Agent] → 执行报告                        │
│                                                             │
│  ═══════════════════════════════════════════════════════   │
│  ↕ 共享记忆系统 (所有Agent读写)                              │
│  ↕ 共享工具集 (所有Agent调用)                                │
└─────────────────────────────────────────────────────────────┘
```

---

## 核心特性

### 🧠 智能体系统

| 智能体 | 职责 | 核心能力 |
|--------|------|----------|
| **市场分析Agent** | 技术面分析 | RSI/MACD/布林带/均线系统、趋势判断 |
| **新闻社媒Agent** | 舆情监控 | 新闻搜索、情感分析、事件识别 |
| **基本面Agent** | 财务分析 | PE/PB/ROE/成长性、投资评级 |
| **多空研究员** | 策略生成 | **多空辩论机制**、对抗性思考 |
| **风险管理Agent** | 风控审核 | 仓位限制、VaR、回撤控制 |
| **交易执行Agent** | 订单执行 | TWAP/VWAP、A股规则适配 |

### 🔧 共享基础设施

#### 共享记忆系统
- **10种记忆类型**：市场观察、分析报告、新闻事件、策略决策等
- **4级重要性**：LOW/MEDIUM/HIGH/CRITICAL
- **多条件检索**：按类型、时间、关键词、标签、Agent筛选
- **记忆衰减**：旧记忆自动降权，重要记忆持久化到JSONL文件

#### 共享工具集
- **统一注册中心**：所有工具统一注册、发现、调用
- **7个内置工具**：数据获取、技术指标、新闻搜索、情感分析、基本面数据、组合状态、风险指标
- **LLM Function Calling**：自动生成OpenAI/Anthropic格式的工具描述
- **权限控制**：可按Agent角色限制工具访问

### ⚡ 性能特性

- **Rust核心**：零成本抽象，Tokio异步运行时
- **内存安全**：所有权系统保证，无GC停顿
- **精确计算**：rust_decimal处理所有金融数值
- **A股规则**：涨跌停/T+1/手续费(万三+千一印花税)/滑点模拟

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    FinHack Pro 系统架构                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  🐍 Python 策略层 (AI智能体 + 策略研究)                       │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │ 市场分析Agent│ │新闻社媒Agent│ │ 基本面Agent │           │
│  │  (技术面)   │ │  (舆情)    │ │  (财务)    │           │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘           │
│         └───────────────┼───────────────┘                   │
│                         ↓                                   │
│                  ┌─────────────┐                           │
│                  │  多空研究员  │ ← 多空辩论机制            │
│                  │(策略生成Agent)│                         │
│                  └──────┬──────┘                           │
│                         ↓                                   │
│  ┌─────────────┐ ┌─────────────┐                           │
│  │ 风险管理Agent│ │ 交易执行Agent│                           │
│  └─────────────┘ └─────────────┘                           │
│                                                             │
│  ═══════════════════════════════════════════════════════   │
│  共享记忆(SharedMemory) + 共享工具集(ToolRegistry)           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  🦀 Rust 核心层 (高性能引擎)                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │finhack-core│ │finhack-bus │ │finhack-risk│ │finhack-execution│
│  │ 核心类型  │ │ 消息总线  │ │ 风控引擎  │ │ 执行引擎  │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                   │
│  │finhack-backtest│ │finhack-data│ │finhack-api │           │
│  │ 回测引擎  │ │ 数据引擎  │ │ REST API │                   │
│  └──────────┘ └──────────┘ └──────────┘                   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  🗄️ 基础设施                                                 │
│  ├── PostgreSQL  (数据持久化)                                │
│  ├── Redis      (缓存/消息队列)                              │
│  ├── InfluxDB   (时序数据)                                   │
│  └── Grafana    (监控面板)                                   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 环境要求

- **Rust**: 1.75+ (安装: https://rustup.rs)
- **Python**: 3.10+
- **PostgreSQL**: 15+ (可选，用于数据持久化)
- **Redis**: 7+ (可选，用于缓存)

### 1. 克隆仓库

```bash
git clone https://github.com/Docking666/finhack-pro.git
cd finhack-pro
```

### 2. 安装 Rust 依赖

```bash
# 编译 Rust 核心
cargo build --release

# 运行 Rust 测试
cargo test
```

### 3. 安装 Python 依赖

```bash
cd python
pip install -r requirements.txt

# 或者使用 poetry
poetry install
```

### 4. 配置环境变量

```bash
# 创建 .env 文件
cp .env.example .env

# 编辑 .env，填入你的API密钥
TUSHARE_TOKEN=your_tushare_token
OPENAI_API_KEY=your_openai_key
```

### 5. 运行回测示例

```bash
# 使用 Dual Thrust 策略回测贵州茅台
cd python
python examples/backtest_example.py
```

### 6. 运行智能体系统

```bash
# 启动六智能体分析系统
python scripts/run_agents.py --symbols 600519.SH --mode single
```

### 7. 启动 WebUI 管理界面

```bash
# 安装 WebUI 依赖
pip install fastapi uvicorn[standard] python-multipart

# 启动 WebUI 服务
cd python
python -m finhack_pro.webui.app

# 浏览器访问 http://localhost:8000
```

---

## WebUI 管理界面

FinHack Pro 内置了一个现代化的 Web 管理界面，提供可视化的系统管理和监控能力。

### 功能概览

| 页面 | 功能 |
|------|------|
| **📊 仪表盘** | 系统概览、Agent状态、最近执行记录、快速操作 |
| **⚙️ API配置** | LLM API Key管理、数据源配置、风控参数、连接测试 |
| **📈 回测面板** | 策略选择、参数配置、实时权益曲线、回测结果展示 |
| **🤖 Agent监控** | 6个Agent实时状态、**LLM思考过程流式展示**、多空辩论可视化 |
| **💾 记忆浏览器** | 共享记忆搜索/浏览/管理、记忆统计、类型分布 |

### 界面特色

- **深色主题**：专为量化交易场景设计的暗色界面
- **实时推送**：WebSocket 连接，回测进度和 Agent 思考过程实时更新
- **思考过程可视化**：类似 ChatGPT 的对话界面，实时展示每个 Agent 的分析推理过程
- **多空辩论展示**：多头论点（绿色）vs 空头论点（红色）对比展示
- **Markdown 渲染**：Agent 输出的结构化分析报告支持完整 Markdown 渲染
- **响应式设计**：适配桌面和平板设备

### Agent 思考过程展示

Agent 监控页面是 WebUI 的核心功能，提供以下展示：

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
│  📰 新闻社媒Agent                              1.8s ✓   │
│  ──────────────────────────────────────────────────────  │
│  搜索到 5 条相关新闻，情感分析：偏正面                     │
│  关键事件：Q3营收超预期15%，北向资金连续3日净流入           │
├──────────────────────────────────────────────────────────┤
│  ⚔️ 多空辩论                                  4.1s ✓    │
│  ──────────────────────────────────────────────────────  │
│  🟢 多头论点：                                            │
│  · 营收超预期增长，基本面改善                              │
│  · MACD金叉确认，技术面转多                                │
│  · 北向资金持续流入，外资看好                              │
│  🔴 空头论点：                                            │
│  · 估值处于历史高位(PE>35)，存在回调风险                   │
│  · 行业政策不确定性增加                                    │
│  ⚖️ 裁决：看多(置信度72%)，建议轻仓参与                    │
└──────────────────────────────────────────────────────────┘
```

### WebUI API

WebUI 后端基于 FastAPI，提供以下 API：

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

## 详细教程

### 教程一：配置数据源

#### 1.1 Tushare 配置

Tushare 是A股数据的主要来源，需要注册获取 Token：

```python
# config/default.yaml
data:
  sources:
    - name: "tushare"
      token: "${TUSHARE_TOKEN}"  # 从环境变量读取
      priority: 1
```

获取 Token 步骤：
1. 访问 https://tushare.pro
2. 注册账号并登录
3. 在个人中心获取 Token
4. 设置环境变量：`export TUSHARE_TOKEN=your_token`

#### 1.2 AKShare 配置（备选）

AKShare 是免费的开源数据源，无需 Token：

```python
# 自动使用 AKShare 作为备选
from finhack_pro.data.fetcher import DataFetcher

fetcher = DataFetcher()
df = fetcher.get_daily("600519.SH", "2024-01-01", "2024-12-31")
```

### 教程二：运行回测

#### 2.1 使用内置策略

```python
from finhack_pro.strategies.dual_thrust import DualThrustStrategy
from finhack_pro.backtest.runner import BacktestRunner

# 创建策略
strategy = DualThrustStrategy({
    "symbols": ["600519.SH"],
    "k1": 0.5,
    "k2": 0.5,
    "lookback": 20,
})

# 运行回测
runner = BacktestRunner()
result = runner.run(
    strategy=strategy,
    start_date="2023-01-01",
    end_date="2024-12-31",
    initial_capital=1000000,
)

# 查看结果
print(f"总收益率: {result.total_return:.2%}")
print(f"夏普比率: {result.sharpe_ratio:.2f}")
print(f"最大回撤: {result.max_drawdown:.2%}")
```

#### 2.2 自定义策略

```python
from finhack_pro.strategies.base import BaseStrategy, Signal, Context
import pandas as pd

class MyStrategy(BaseStrategy):
    def on_init(self, context: Context) -> None:
        """策略初始化"""
        self.fast_ma = 5
        self.slow_ma = 20
    
    def on_bar(self, context: Context, bar: pd.DataFrame) -> List[Signal]:
        """K线回调，生成交易信号"""
        signals = []
        
        # 计算均线
        df = context.data_feed.get_bars(self.symbols[0], 30)
        df['ma5'] = df['close'].rolling(self.fast_ma).mean()
        df['ma20'] = df['close'].rolling(self.slow_ma).mean()
        
        # 金叉买入
        if df['ma5'].iloc[-1] > df['ma20'].iloc[-1] and \
           df['ma5'].iloc[-2] <= df['ma20'].iloc[-2]:
            signals.append(Signal(
                symbol=self.symbols[0],
                direction=1,  # 买入
                price=bar['close'],
                volume=100,
            ))
        
        return signals
```

### 教程三：使用智能体系统

#### 3.1 基础用法

```python
from finhack_pro.agents.coordinator import AgentCoordinator
import asyncio

async def main():
    # 创建协调器
    config = {
        "agents": {
            "market_analyzer": {"model": "gpt-4", "temperature": 0.3},
            "news_analyst": {"model": "gpt-4", "temperature": 0.3},
            "fundamental_analyst": {"model": "gpt-4", "temperature": 0.2},
            "strategy_generator": {"model": "gpt-4", "temperature": 0.5},
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
    
    # 运行分析流水线
    result = await coordinator.run_analysis_pipeline(
        symbol="600519.SH",
        market_data=df,  # DataFrame
    )
    
    # 查看结果
    print(f"策略信号: {result['strategy_signal']}")
    print(f"风控决策: {result['risk_decision']}")
    print(f"执行报告: {result['execution_report']}")
    
    await coordinator.stop()

asyncio.run(main())
```

#### 3.2 多空辩论机制

```python
from finhack_pro.agents.strategy_generator import StrategyGeneratorAgent

# 创建多空研究员
agent = StrategyGeneratorAgent(config)

# 启用多空辩论
debate_result = await agent.debate(
    symbol="600519.SH",
    market_analysis=market_report,
    news_analysis=news_report,
    fundamental_analysis=fundamental_report,
)

print(f"多头论点: {debate_result.bull_arguments}")
print(f"空头论点: {debate_result.bear_arguments}")
print(f"辩论结论: {debate_result.consensus}")
print(f"置信度: {debate_result.confidence:.2%}")
```

### 教程四：共享记忆系统

#### 4.1 存储记忆

```python
from finhack_pro.agents.shared_memory import SharedMemory, MemoryType, MemoryImportance

memory = SharedMemory(persist_dir="./data/memory")

# 存储一条重要记忆
memory_id = await memory.store(
    agent_id="market_analyzer",
    memory_type=MemoryType.ANALYSIS_REPORT,
    content="贵州茅台技术面分析：突破2000元关口，MACD金叉",
    structured_data={"signal": "bullish", "confidence": 0.85},
    importance=MemoryImportance.HIGH,
    tags=["600519.SH", "breakout", "macd"],
)
```

#### 4.2 检索记忆

```python
# 按类型检索
reports = await memory.retrieve(
    memory_type=MemoryType.ANALYSIS_REPORT,
    keywords=["茅台", "突破"],
    limit=10,
)

# 获取Agent上下文
context = await memory.get_agent_context("market_analyzer", n=5)
print(context)  # 用于注入LLM prompt
```

### 教程五：共享工具集

#### 5.1 使用内置工具

```python
from finhack_pro.agents.tool_registry import create_default_toolkit

# 创建工具集
registry = create_default_toolkit()

# 调用工具
result = await registry.call_tool(
    "fetch_market_data",
    {"symbol": "600519.SH", "start_date": "2024-01-01", "end_date": "2024-12-31"},
    caller_agent_id="market_analyzer",
)

print(result)
```

#### 5.2 自定义工具

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
        input_data = kwargs["input"]
        return {"result": f"处理结果: {input_data}"}

# 注册工具
registry.register(MyTool())
```

---

## API文档

### REST API

启动 API 服务器：

```bash
cargo run --bin finhack-api
```

| 方法 | 路径 | 说明 | 请求体 |
|------|------|------|--------|
| GET | `/api/health` | 健康检查 | - |
| GET | `/api/system/info` | 系统信息 | - |
| POST | `/api/backtest` | 运行回测 | `{"strategy": "dual_thrust", "symbols": ["600519.SH"]}` |
| GET | `/api/portfolio` | 获取组合状态 | - |
| POST | `/api/order` | 下单 | `{"symbol": "600519.SH", "side": "buy", "volume": 100}` |
| GET | `/api/agents/status` | 智能体状态 | - |
| WS | `/ws/market` | 实时行情推送 | - |

### Python API

```python
from finhack_pro.api.client import FinHackClient

client = FinHackClient(base_url="http://localhost:8080")

# 运行回测
result = client.run_backtest(
    strategy="dual_thrust",
    symbols=["600519.SH"],
    start_date="2023-01-01",
    end_date="2024-12-31",
)

# 获取组合状态
portfolio = client.get_portfolio()
```

---

## 配置说明

### 完整配置示例

```yaml
# config/default.yaml

system:
  name: "FinHack Pro"
  version: "1.0.0"
  mode: "backtest"  # backtest/paper/live

# 数据配置
data:
  storage_type: "csv"
  data_dir: "./data"
  symbols:
    - "000001.SZ"
    - "600519.SH"
  sources:
    - name: "tushare"
      token: "${TUSHARE_TOKEN}"
      priority: 1
    - name: "akshare"
      priority: 2

# 智能体配置
agents:
  market_analyzer:
    model: "gpt-4"
    temperature: 0.3
    max_tokens: 2000
  
  news_analyst:
    model: "gpt-4"
    temperature: 0.3
    max_tokens: 2000
  
  fundamental_analyst:
    model: "gpt-4"
    temperature: 0.2
    max_tokens: 2500
  
  strategy_generator:
    model: "gpt-4"
    temperature: 0.5
    enable_debate: true  # 启用多空辩论
  
  risk_manager:
    enabled: true
  
  trade_executor:
    enabled: true

# 共享记忆配置
shared_memory:
  enabled: true
  persist_dir: "./data/memory"
  max_short_term: 1000
  decay_hours: 24

# 共享工具集配置
tool_registry:
  enabled: true

# 风控配置
risk:
  max_position_pct: 0.2      # 单标的最大仓位20%
  max_drawdown: 0.15         # 最大回撤15%
  var_limit: 0.05            # 日VaR限制5%
  max_leverage: 2.0          # 最大杠杆2倍
  daily_loss_limit: 0.03     # 日亏损限制3%

# 执行配置
execution:
  algorithm: "twap"          # TWAP/VWAP/iceberg
  slippage_bps: 2            # 滑点2个基点
  commission_rate: 0.0003    # 佣金万三
  stamp_tax_rate: 0.001      # 印花税千一

# 回测配置
backtest:
  initial_capital: 1000000   # 初始资金100万
  start_date: "2023-01-01"
  end_date: "2024-12-31"

# API配置
api:
  host: "0.0.0.0"
  port: 8080
```

---

## 常见问题

### Q1: 如何获取 Tushare Token？

1. 访问 https://tushare.pro 注册账号
2. 在个人中心获取 Token
3. 设置环境变量：`export TUSHARE_TOKEN=your_token`

### Q2: LLM API 费用如何？

- 市场分析、新闻分析、基本面分析：每次约 1000-2000 tokens
- 多空辩论：3次调用，每次约 1500-2500 tokens
- 建议设置预算限制或使用本地模型（如 Ollama）

### Q3: 如何接入实盘交易？

目前支持模拟交易，实盘接口需要：
1. 开通券商 API（如中泰XTP、迅投QMT）
2. 在 `execution` 模块实现对应接口
3. 配置 `mode: live` 并设置风控参数

### Q4: 数据存储在哪里？

- **短期数据**：内存（SharedMemory）
- **重要记忆**：`./data/memory/*.jsonl`
- **行情数据**：`./data/` 目录下的 CSV 文件
- **持久化数据**：PostgreSQL（可选）

### Q5: 如何调试智能体？

```python
# 启用详细日志
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

**免责声明**：本系统仅供学习和研究使用，不构成投资建议。量化交易有风险，入市需谨慎。
