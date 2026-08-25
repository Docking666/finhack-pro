"""
策略工坊API路由

提供LLM辅助策略/因子生成、策略模板库、可视化因子编辑等功能。
降低自定义策略与因子的开发门槛。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from finhack_pro.webui.models import APIResponse

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


# ============================================================
# 请求/响应模型
# ============================================================

class StrategyGenerateRequest(BaseModel):
    """LLM策略生成请求"""
    description: str = Field(..., description="策略描述(自然语言)", min_length=10)
    market: str = Field("A", description="市场: A=A股, HK=港股, US=美股")
    style: str = Field("medium", description="风格: short=短线, medium=中线, long=长线")
    risk_level: str = Field("medium", description="风险等级: low, medium, high")
    instruments: str = Field("stock", description="品种: stock=股票, futures=期货, crypto=加密货币")

class FactorGenerateRequest(BaseModel):
    """LLM因子生成请求"""
    description: str = Field(..., description="因子描述(自然语言)", min_length=5)
    data_type: str = Field("daily", description="数据类型: daily=日线, minute=分钟线")
    category: str = Field("technical", description="因子类别: technical=技术, fundamental=基本面, sentiment=情绪, custom=自定义")

class StrategyValidateRequest(BaseModel):
    """策略验证请求"""
    code: str = Field(..., description="策略Python代码")
    strategy_type: str = Field("custom", description="策略类型")

class FactorValidateRequest(BaseModel):
    """因子验证请求"""
    code: str = Field(..., description="因子Python代码")
    factor_type: str = Field("technical", description="因子类型")

class VisualFactorCreateRequest(BaseModel):
    """可视化因子创建请求"""
    name: str = Field(..., description="因子名称")
    description: str = Field("", description="因子描述")
    category: str = Field("technical", description="因子类别")
    inputs: List[Dict[str, Any]] = Field(default_factory=list, description="输入参数列表")
    formula: str = Field("", description="计算公式(表达式)")
    conditions: List[Dict[str, Any]] = Field(default_factory=list, description="条件列表")
    output_type: str = Field("signal", description="输出类型: signal=信号, value=数值, score=评分")

class StrategyTestRequest(BaseModel):
    """策略快速测试请求"""
    code: str = Field(..., description="策略代码")
    symbol: str = Field("600519.SH", description="测试标的")
    start_date: str = Field("2024-01-01", description="开始日期")
    end_date: str = Field("2024-12-31", description="结束日期")
    initial_capital: float = Field(1000000, description="初始资金")


# ============================================================
# 策略模板库
# ============================================================

STRATEGY_TEMPLATES = [
    {
        "id": "dual_thrust",
        "name": "Dual Thrust 突破策略",
        "category": "趋势跟踪",
        "style": "短线",
        "difficulty": "⭐⭐",
        "description": "经典的日内/短线突破策略，基于N日最高价、最低价和收盘价计算上下轨，突破上轨做多，突破下轨做空。",
        "params": [
            {"name": "lookback", "label": "回看周期", "type": "int", "default": 20, "min": 5, "max": 60, "step": 1},
            {"name": "k1", "label": "上轨系数", "type": "float", "default": 0.5, "min": 0.1, "max": 1.0, "step": 0.05},
            {"name": "k2", "label": "下轨系数", "type": "float", "default": 0.5, "min": 0.1, "max": 1.0, "step": 0.05},
        ],
        "code": '''class DualThrustStrategy(BaseStrategy):
    """Dual Thrust 突破策略"""

    def __init__(self, lookback=20, k1=0.5, k2=0.5):
        self.lookback = lookback
        self.k1 = k1
        self.k2 = k2

    def on_bar(self, bar):
        if len(self.bars) < self.lookback:
            return

        highs = [b.high for b in self.bars[-self.lookback:]]
        lows = [b.low for b in self.bars[-self.lookback:]]
        closes = [b.close for b in self.bars[-self.lookback:]]

        hh = max(highs[:-1])
        lc = min(lows[:-1])
        hc = max(highs[:-1])
        ll = min(lows[:-1])

        range_val = max(hh - lc, hc - ll)
        upper = bar.open + self.k1 * range_val
        lower = bar.open - self.k2 * range_val

        if bar.close > upper and not self.position:
            self.buy(bar.close, size=100)
        elif bar.close < lower and self.position:
            self.sell(bar.close, size=self.position.quantity)
''',
        "tags": ["趋势", "突破", "经典"],
    },
    {
        "id": "rsi_reversal",
        "name": "RSI 均值回归策略",
        "category": "均值回归",
        "style": "中线",
        "difficulty": "⭐⭐",
        "description": "基于RSI超买超卖信号的均值回归策略，RSI低于超卖线时买入，高于超买线时卖出。",
        "params": [
            {"name": "rsi_period", "label": "RSI周期", "type": "int", "default": 14, "min": 5, "max": 30, "step": 1},
            {"name": "oversold", "label": "超卖线", "type": "float", "default": 30, "min": 10, "max": 40, "step": 1},
            {"name": "overbought", "label": "超买线", "type": "float", "default": 70, "min": 60, "max": 90, "step": 1},
        ],
        "code": '''class RSIReversalStrategy(BaseStrategy):
    """RSI 均值回归策略"""

    def __init__(self, rsi_period=14, oversold=30, overbought=70):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought

    def calculate_rsi(self, period):
        closes = [b.close for b in self.bars]
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i-1] for i in range(-period, 0)]
        gains = sum(max(d, 0) for d in deltas) / period
        losses = sum(abs(min(d, 0)) for d in deltas) / period
        if losses == 0:
            return 100.0
        rs = gains / losses
        return 100 - 100 / (1 + rs)

    def on_bar(self, bar):
        rsi = self.calculate_rsi(self.rsi_period)

        if rsi < self.oversold and not self.position:
            self.buy(bar.close, size=100)
        elif rsi > self.overbought and self.position:
            self.sell(bar.close, size=self.position.quantity)
''',
        "tags": ["均值回归", "RSI", "超买超卖"],
    },
    {
        "id": "macd_cross",
        "name": "MACD 金叉死叉策略",
        "category": "趋势跟踪",
        "style": "中线",
        "difficulty": "⭐",
        "description": "最经典的趋势跟踪策略之一，MACD快线上穿慢线(金叉)买入，下穿(死叉)卖出。",
        "params": [
            {"name": "fast_period", "label": "快线周期", "type": "int", "default": 12, "min": 5, "max": 20, "step": 1},
            {"name": "slow_period", "label": "慢线周期", "type": "int", "default": 26, "min": 15, "max": 50, "step": 1},
            {"name": "signal_period", "label": "信号线周期", "type": "int", "default": 9, "min": 3, "max": 15, "step": 1},
        ],
        "code": '''class MACDCrossStrategy(BaseStrategy):
    """MACD 金叉死叉策略"""

    def __init__(self, fast=12, slow=26, signal=9):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def calculate_ema(self, data, period):
        if len(data) < period:
            return data[-1] if data else 0
        k = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = val * k + ema * (1 - k)
        return ema

    def on_bar(self, bar):
        closes = [b.close for b in self.bars]
        if len(closes) < self.slow + self.signal:
            return

        fast_ema = self.calculate_ema(closes, self.fast)
        slow_ema = self.calculate_ema(closes, self.slow)
        macd = fast_ema - slow_ema

        macd_values = []
        for i in range(len(closes) - self.slow):
            f = self.calculate_ema(closes[:i+self.slow], self.fast)
            s = self.calculate_ema(closes[:i+self.slow], self.slow)
            macd_values.append(f - s)

        signal_line = self.calculate_ema(macd_values, self.signal)
        prev_signal = self.calculate_ema(macd_values[:-1], self.signal) if len(macd_values) > self.signal else signal_line

        if macd > signal_line and prev_macd <= prev_signal and not self.position:
            self.buy(bar.close, size=100)
        elif macd < signal_line and prev_macd >= prev_signal and self.position:
            self.sell(bar.close, size=self.position.quantity)
''',
        "tags": ["趋势", "MACD", "金叉死叉", "入门"],
    },
    {
        "id": "bollinger_breakout",
        "name": "布林带突破策略",
        "category": "波动率",
        "style": "短线",
        "difficulty": "⭐⭐",
        "description": "基于布林带通道的突破策略，价格突破上轨且成交量放大时做多，跌破下轨时做空。",
        "params": [
            {"name": "period", "label": "布林带周期", "type": "int", "default": 20, "min": 10, "max": 50, "step": 1},
            {"name": "std_dev", "label": "标准差倍数", "type": "float", "default": 2.0, "min": 1.0, "max": 3.0, "step": 0.1},
            {"name": "volume_factor", "label": "成交量放大倍数", "type": "float", "default": 1.5, "min": 1.0, "max": 3.0, "step": 0.1},
        ],
        "code": '''class BollingerBreakoutStrategy(BaseStrategy):
    """布林带突破策略"""

    def __init__(self, period=20, std_dev=2.0, volume_factor=1.5):
        self.period = period
        self.std_dev = std_dev
        self.volume_factor = volume_factor

    def on_bar(self, bar):
        if len(self.bars) < self.period:
            return

        closes = [b.close for b in self.bars[-self.period:]]
        volumes = [b.volume for b in self.bars[-self.period:]]

        sma = sum(closes) / len(closes)
        variance = sum((c - sma) ** 2 for c in closes) / len(closes)
        std = variance ** 0.5

        upper = sma + self.std_dev * std
        lower = sma - self.std_dev * std
        avg_volume = sum(volumes) / len(volumes)

        if bar.close > upper and bar.volume > avg_volume * self.volume_factor:
            if not self.position:
                self.buy(bar.close, size=100)
        elif bar.close < lower:
            if self.position:
                self.sell(bar.close, size=self.position.quantity)
''',
        "tags": ["波动率", "布林带", "突破"],
    },
    {
        "id": "momentum_rotation",
        "name": "动量轮动策略",
        "category": "多因子",
        "style": "中线",
        "difficulty": "⭐⭐⭐",
        "description": "多标的动量轮动策略，定期计算各标的的动量得分，持有动量最强的N只标的。",
        "params": [
            {"name": "momentum_period", "label": "动量计算周期", "type": "int", "default": 20, "min": 5, "max": 60, "step": 1},
            {"name": "rebalance_days", "label": "调仓周期(天)", "type": "int", "default": 5, "min": 1, "max": 20, "step": 1},
            {"name": "top_n", "label": "持有标的数", "type": "int", "default": 3, "min": 1, "max": 10, "step": 1},
        ],
        "code": '''class MomentumRotationStrategy(BaseStrategy):
    """动量轮动策略"""

    def __init__(self, momentum_period=20, rebalance_days=5, top_n=3):
        self.momentum_period = momentum_period
        self.rebalance_days = rebalance_days
        self.top_n = top_n
        self.days_since_rebalance = 0

    def calculate_momentum(self, symbol):
        bars = self.get_bars(symbol)
        if len(bars) < self.momentum_period + 1:
            return 0.0
        return (bars[-1].close / bars[-self.momentum_period-1].close - 1) * 100

    def on_bar(self, bar):
        self.days_since_rebalance += 1
        if self.days_since_rebalance < self.rebalance_days:
            return
        self.days_since_rebalance = 0

        symbols = self.universe
        momentums = [(s, self.calculate_momentum(s)) for s in symbols]
        momentums.sort(key=lambda x: x[1], reverse=True)

        targets = set(s for s, _ in momentums[:self.top_n])
        current = set(self.position_symbols)

        for s in current - targets:
            self.sell(s, size=self.get_position(s).quantity)
        for s in targets - current:
            self.buy(s, size=100)
''',
        "tags": ["多因子", "动量", "轮动", "进阶"],
    },
    {
        "id": "turtle_trading",
        "name": "海龟交易法则",
        "category": "趋势跟踪",
        "style": "长线",
        "difficulty": "⭐⭐⭐",
        "description": "传奇的海龟交易法则，基于唐奇安通道突破入场，ATR动态止损，金字塔加仓。",
        "params": [
            {"name": "entry_period", "label": "入场通道周期", "type": "int", "default": 20, "min": 10, "max": 55, "step": 1},
            {"name": "exit_period", "label": "出场通道周期", "type": "int", "default": 10, "min": 5, "max": 20, "step": 1},
            {"name": "atr_period", "label": "ATR周期", "type": "int", "default": 20, "min": 10, "max": 30, "step": 1},
            {"name": "max_units", "label": "最大加仓次数", "type": "int", "default": 4, "min": 1, "max": 6, "step": 1},
        ],
        "code": '''class TurtleTradingStrategy(BaseStrategy):
    """海龟交易法则"""

    def __init__(self, entry_period=20, exit_period=10, atr_period=20, max_units=4):
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.atr_period = atr_period
        self.max_units = max_units
        self.units = 0
        self.entry_price = 0

    def calculate_atr(self):
        bars = self.bars[-self.atr_period:]
        trs = []
        for i in range(1, len(bars)):
            tr = max(bars[i].high - bars[i].low,
                     abs(bars[i].high - bars[i-1].close),
                     abs(bars[i].low - bars[i-1].close))
            trs.append(tr)
        return sum(trs) / len(trs)

    def on_bar(self, bar):
        if len(self.bars) < self.entry_period:
            return

        highs = [b.high for b in self.bars[-self.entry_period:]]
        lows = [b.low for b in self.bars[-self.exit_period:]]
        upper = max(highs[:-1])
        lower = min(lows[:-1])
        atr = self.calculate_atr()

        if not self.position:
            if bar.close > upper:
                unit_size = max(1, int(self.capital * 0.01 / atr / 100) * 100)
                self.buy(bar.close, size=unit_size)
                self.units = 1
                self.entry_price = bar.close
        else:
            if bar.close < lower:
                self.sell(bar.close, size=self.position.quantity)
                self.units = 0
            elif self.units < self.max_units:
                add_price = self.entry_price + self.units * 0.5 * atr
                if bar.close > add_price:
                    unit_size = max(1, int(self.capital * 0.01 / atr / 100) * 100)
                    self.buy(bar.close, size=unit_size)
                    self.units += 1
''',
        "tags": ["趋势", "经典", "ATR", "通道", "进阶"],
    },
]


# 因子模板库
FACTOR_TEMPLATES = [
    {
        "id": "momentum_20d",
        "name": "20日动量因子",
        "category": "technical",
        "description": "过去20个交易日的收益率",
        "formula": "close[-1] / close[-21] - 1",
        "output_type": "value",
        "params": [{"name": "period", "label": "周期", "type": "int", "default": 20, "min": 5, "max": 120}],
        "code": "def momentum_20d(bars):\n    if len(bars) < 21:\n        return 0.0\n    return bars[-1].close / bars[-21].close - 1",
    },
    {
        "id": "volatility_20d",
        "name": "20日波动率因子",
        "category": "technical",
        "description": "过去20个交易日收益率的标准差",
        "formula": "std(daily_returns[-20:])",
        "output_type": "value",
        "params": [{"name": "period", "label": "周期", "type": "int", "default": 20, "min": 5, "max": 60}],
        "code": "def volatility_20d(bars):\n    if len(bars) < 21:\n        return 0.0\n    returns = [(bars[i].close/bars[i-1].close-1) for i in range(-20, 0)]\n    mean = sum(returns)/len(returns)\n    var = sum((r-mean)**2 for r in returns)/len(returns)\n    return var ** 0.5",
    },
    {
        "id": "volume_ratio",
        "name": "量比因子",
        "category": "technical",
        "description": "当日成交量与过去5日均量的比值",
        "formula": "volume[-1] / mean(volume[-6:-1])",
        "output_type": "value",
        "params": [{"name": "period", "label": "均量周期", "type": "int", "default": 5, "min": 3, "max": 20}],
        "code": "def volume_ratio(bars):\n    if len(bars) < 6:\n        return 1.0\n    avg_vol = sum(b.volume for b in bars[-6:-1]) / 5\n    return bars[-1].volume / avg_vol if avg_vol > 0 else 1.0",
    },
    {
        "id": "turnover_rate",
        "name": "换手率因子",
        "category": "technical",
        "description": "当日换手率(成交量/流通股本)",
        "formula": "volume / float_shares * 100",
        "output_type": "value",
        "params": [],
        "code": "def turnover_rate(bars, float_shares=0):\n    if not bars or float_shares <= 0:\n        return 0.0\n    return bars[-1].volume / float_shares * 100",
    },
    {
        "id": "pe_factor",
        "name": "PE估值因子",
        "category": "fundamental",
        "description": "市盈率(PE)的倒数，用于价值评估",
        "formula": "1 / PE",
        "output_type": "value",
        "params": [],
        "code": "def pe_factor(pe):\n    if pe and pe > 0:\n        return 1.0 / pe\n    return 0.0",
    },
    {
        "id": "price_volume_trend",
        "name": "量价趋势因子",
        "category": "technical",
        "description": "累积量价趋势指标，衡量资金流向",
        "formula": "cumsum(volume * (close - prev_close) / prev_close)",
        "output_type": "value",
        "params": [{"name": "period", "label": "计算周期", "type": "int", "default": 20, "min": 5, "max": 60}],
        "code": "def price_volume_trend(bars):\n    if len(bars) < 2:\n        return 0.0\n    pvt = 0.0\n    for i in range(1, len(bars)):\n        change = (bars[i].close - bars[i-1].close) / bars[i-1].close\n        pvt += bars[i].volume * change\n    return pvt",
    },
]


# ============================================================
# LLM策略/因子生成
# ============================================================

STRATEGY_SYSTEM_PROMPT = """你是一位资深的量化交易策略开发专家。用户会用自然语言描述他们想要的交易策略，你需要将其转化为可执行的Python策略代码。

## 代码规范

1. 策略类必须继承 BaseStrategy
2. 必须实现 on_bar(self, bar) 方法，每根K线调用一次
3. 可用属性:
   - self.bars: 历史K线列表，每个bar有 open/high/low/close/volume 属性
   - self.position: 当前持仓 (None表示空仓)
   - self.position.quantity: 持仓数量
   - self.capital: 当前可用资金
   - self.universe: 标的列表
4. 可用方法:
   - self.buy(price, size=100): 买入
   - self.sell(price, size=100): 卖出
   - self.get_bars(symbol): 获取指定标的历史K线
5. A股规则: T+1交易，最小交易单位100股(1手)

## 输出格式

请严格按以下JSON格式输出:
```json
{
    "name": "策略名称",
    "category": "策略分类",
    "style": "短线/中线/长线",
    "difficulty": "⭐~⭐⭐⭐",
    "description": "策略原理说明",
    "params": [
        {"name": "参数名", "label": "中文标签", "type": "int/float", "default": 默认值, "min": 最小值, "max": 最大值, "step": 步长}
    ],
    "code": "完整的Python策略类代码",
    "tags": ["标签1", "标签2"],
    "risk_notes": "风险提示"
}
```

请确保生成的代码完整、可运行、包含清晰的注释。"""


FACTOR_SYSTEM_PROMPT = """你是一位资深的量化因子研发专家。用户会用自然语言描述他们想要的因子，你需要将其转化为可执行的Python因子代码。

## 代码规范

1. 因子函数接收 bars 参数(K线列表)和可能的额外参数
2. 每个bar有 open/high/low/close/volume/amount 属性
3. 函数返回一个数值(float)
4. 需要处理数据不足的情况(返回0或None)
5. 代码要有清晰的注释

## 输出格式

请严格按以下JSON格式输出:
```json
{
    "name": "因子名称",
    "category": "technical/fundamental/sentiment/custom",
    "description": "因子含义说明",
    "formula": "数学公式描述",
    "output_type": "value/signal/score",
    "params": [
        {"name": "参数名", "label": "中文标签", "type": "int/float", "default": 默认值, "min": 最小值, "max": 最大值}
    ],
    "code": "完整的Python因子函数代码",
    "usage_example": "使用示例"
}
```

请确保生成的代码完整、可运行、包含清晰的注释。"""


async def _call_llm(prompt: str, system: str) -> str:
    """调用LLM生成策略/因子代码"""
    try:
        from finhack_pro.config import get_config
        from finhack_pro.webui.services import ConfigService

        config = get_config()
        api_key = config.llm.openai_api_key if hasattr(config, 'llm') else ""
        base_url = config.llm.openai_base_url if hasattr(config, 'llm') else "https://api.openai.com/v1"
        model = config.llm.model if hasattr(config, 'llm') else "gpt-4o"

        if not api_key:
            raise HTTPException(status_code=400, detail="请先在API配置页面设置OpenAI API Key")

        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 4096,
                    # 强制 JSON 输出模式，降低 LLM 返回散文/裸代码的概率（尤其对"社媒特定群体"等 niche 描述）
                    "response_format": {"type": "json_object"},
                },
            )

            if resp.status_code != 200:
                error_msg = resp.text[:500]
                logger.error(f"LLM调用失败: {resp.status_code} {error_msg}")
                raise HTTPException(status_code=500, detail=f"LLM调用失败: {error_msg}")

            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LLM调用异常: {e}")
        raise HTTPException(status_code=500, detail=f"LLM调用异常: {str(e)}")


def _extract_json(text: str) -> Dict[str, Any]:
    """从LLM响应中提取JSON（宽松模式：支持代码块/BOM/平衡括号兜底）

    复用 llm_client.LLMClient._extract_json 的健壮逻辑，比旧版正则-only 版本
    能处理 LLM 返回裸代码、散文夹杂JSON、```python 代码块等边缘情况。
    """
    json_str = text.strip().lstrip("\ufeff")

    # 去掉外层代码块（兼容 ```json / ```python / ``` 等各种标记）
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        parts = json_str.split("```")
        if len(parts) >= 3:
            json_str = parts[1].strip()  # 取第一个代码块内容

    # 尝试直接解析
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 兜底：提取第一个平衡 { ... } 或 [ ... ] 块
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = json_str.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(json_str)):
            if json_str[i] == open_ch:
                depth += 1
            elif json_str[i] == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = json_str[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"无法从LLM响应中提取JSON: {text[:200]}")


def _validate_python_code(code: str) -> tuple:
    """验证Python代码语法"""
    try:
        compile(code, '<strategy>', 'exec')
        return True, "代码语法正确"
    except SyntaxError as e:
        return False, f"语法错误: 第{e.lineno}行 - {e.msg}"


def _validate_strategy_code(code: str) -> tuple:
    """验证策略代码结构"""
    checks = []
    if "class " not in code:
        checks.append("缺少策略类定义(class)")
    if "def on_bar" not in code:
        checks.append("缺少 on_bar 方法")
    if "self.buy" not in code and "self.sell" not in code:
        checks.append("缺少交易信号(buy/sell)")
    if "BaseStrategy" not in code:
        checks.append("策略类未继承 BaseStrategy")

    syntax_ok, syntax_msg = _validate_python_code(code)
    if not syntax_ok:
        return False, syntax_msg

    if checks:
        return False, "; ".join(checks)
    return True, "策略代码验证通过"


def _validate_factor_code(code: str) -> tuple:
    """验证因子代码结构"""
    checks = []
    if "def " not in code:
        checks.append("缺少函数定义(def)")
    if "return " not in code:
        checks.append("缺少返回值(return)")

    syntax_ok, syntax_msg = _validate_python_code(code)
    if not syntax_ok:
        return False, syntax_msg

    if checks:
        return False, "; ".join(checks)
    return True, "因子代码验证通过"


# ============================================================
# API端点
# ============================================================

@router.get("/templates", response_model=APIResponse)
async def get_strategy_templates(
    category: Optional[str] = Query(None, description="按分类筛选"),
    style: Optional[str] = Query(None, description="按风格筛选"),
    difficulty: Optional[str] = Query(None, description="按难度筛选"),
):
    """获取策略模板列表"""
    templates = STRATEGY_TEMPLATES
    if category:
        templates = [t for t in templates if t["category"] == category]
    if style:
        templates = [t for t in templates if t["style"] == style]
    if difficulty:
        templates = [t for t in templates if t.get("difficulty", "") == difficulty]

    return APIResponse(data=[
        {k: v for k, v in t.items() if k != "code"}
        for t in templates
    ])


@router.get("/templates/{template_id}", response_model=APIResponse)
async def get_strategy_template(template_id: str):
    """获取策略模板详情(含代码)"""
    for t in STRATEGY_TEMPLATES:
        if t["id"] == template_id:
            return APIResponse(data=t)
    raise HTTPException(status_code=404, detail=f"策略模板不存在: {template_id}")


@router.get("/templates/{template_id}/code", response_model=APIResponse)
async def get_template_code(template_id: str):
    """获取策略模板代码"""
    for t in STRATEGY_TEMPLATES:
        if t["id"] == template_id:
            return APIResponse(data={"code": t["code"], "name": t["name"]})
    raise HTTPException(status_code=404, detail=f"策略模板不存在: {template_id}")


@router.get("/factors/templates", response_model=APIResponse)
async def get_factor_templates(
    category: Optional[str] = Query(None, description="按分类筛选"),
):
    """获取因子模板列表"""
    templates = FACTOR_TEMPLATES
    if category:
        templates = [t for t in templates if t["category"] == category]
    return APIResponse(data=templates)


@router.get("/factors/templates/{factor_id}", response_model=APIResponse)
async def get_factor_template(factor_id: str):
    """获取因子模板详情"""
    for f in FACTOR_TEMPLATES:
        if f["id"] == factor_id:
            return APIResponse(data=f)
    raise HTTPException(status_code=404, detail=f"因子模板不存在: {factor_id}")


@router.post("/generate", response_model=APIResponse)
async def generate_strategy(request: StrategyGenerateRequest):
    """LLM生成策略代码"""
    prompt = f"""请根据以下需求生成一个完整的量化交易策略:

## 策略描述
{request.description}

## 市场环境
- 市场: {"A股(中国)" if request.market == "A" else "港股" if request.market == "HK" else "美股"}
- 交易风格: {"短线(1-5天)" if request.style == "short" else "中线(1-4周)" if request.style == "medium" else "长线(1-6月)"}
- 风险偏好: {"低风险(稳健)" if request.risk_level == "low" else "中等风险(平衡)" if request.risk_level == "medium" else "高风险(激进)"}
- 交易品种: {"股票" if request.instruments == "stock" else "期货" if request.instruments == "futures" else "加密货币"}

请生成完整的策略代码，包含详细的注释和参数说明。"""

    try:
        response = await _call_llm(prompt, STRATEGY_SYSTEM_PROMPT)
        result = _extract_json(response)
        # 验证代码语法
        code = result.get("code", "")
        valid, msg = _validate_strategy_code(code)
        result["validation"] = {"valid": valid, "message": msg}

        # 生成策略 ID（供分享到创意工坊使用）
        strategy_id = f"gen_{uuid.uuid4().hex[:10]}"
        result["strategy_id"] = strategy_id
        result["name"] = result.get("name") or request.description[:20]

        # 若验证通过，保存到本地生成目录（供分享/安装）
        if valid:
            from finhack_pro.workshop import PackageManager, StrategyManifest
            gen_dir = Path("data/generated_strategies") / strategy_id
            gen_dir.mkdir(parents=True, exist_ok=True)
            (gen_dir / "strategy.py").write_text(code, encoding="utf-8")
            manifest = StrategyManifest.from_dict({
                "id": strategy_id,
                "name": result["name"],
                "version": "0.1.0",
                "author": "workshop",
                "description": request.description,
                "type": "strategy",
                "entry": "strategy.py",
                "entry_class": result.get("class_name", ""),
                "params_schema": result.get("params_schema") or StrategyManifest.default_params_schema(),
            })
            (gen_dir / "manifest.yaml").write_text(manifest.to_yaml(), encoding="utf-8")
            result["saved_path"] = str(gen_dir)

        return APIResponse(message="策略生成成功", data=result)
    except ValueError as e:
        return APIResponse(
            success=False,
            message="策略生成失败：LLM 返回格式无法解析，请重试或调整描述",
            data={"raw_response": response, "validation": {"valid": False, "message": str(e)}},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"策略生成失败: {str(e)}")


@router.post("/factors/generate", response_model=APIResponse)
async def generate_factor(request: FactorGenerateRequest):
    """LLM生成因子代码"""
    category_names = {
        "technical": "技术因子",
        "fundamental": "基本面因子",
        "sentiment": "情绪因子",
        "custom": "自定义因子",
    }
    prompt = f"""请根据以下需求生成一个完整的量化因子:

## 因子描述
{request.description}

## 因子类型
- 类别: {category_names.get(request.category, request.category)}
- 数据频率: {"日线" if request.data_type == "daily" else "分钟线"}

请生成完整的因子代码，包含详细的注释和使用示例。"""

    try:
        response = await _call_llm(prompt, FACTOR_SYSTEM_PROMPT)
        result = _extract_json(response)
        code = result.get("code", "")
        valid, msg = _validate_factor_code(code)
        result["validation"] = {"valid": valid, "message": msg}
        return APIResponse(message="因子生成成功", data=result)
    except ValueError as e:
        return APIResponse(
            success=False,
            message="因子生成失败：LLM 返回格式无法解析，请重试或调整描述",
            data={"raw_response": response, "validation": {"valid": False, "message": str(e)}},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"因子生成失败: {str(e)}")


@router.post("/validate", response_model=APIResponse)
async def validate_strategy(request: StrategyValidateRequest):
    """验证策略代码"""
    valid, msg = _validate_strategy_code(request.code)
    return APIResponse(data={"valid": valid, "message": msg})


@router.post("/factors/validate", response_model=APIResponse)
async def validate_factor(request: FactorValidateRequest):
    """验证因子代码"""
    valid, msg = _validate_factor_code(request.code)
    return APIResponse(data={"valid": valid, "message": msg})


@router.post("/visual-factor/create", response_model=APIResponse)
async def create_visual_factor(request: VisualFactorCreateRequest):
    """通过可视化配置创建因子代码"""
    # 根据配置生成因子代码
    params_str = ", ".join(
        f"{p['name']}={p.get('default', 0)}" for p in request.inputs
    )

    # 构建因子函数
    lines = [
        f'def {request.name}(bars{", " + params_str if params_str else ""}):',
        f'    """{request.description}"""',
        '    if len(bars) < 2:',
        '        return 0.0',
        '',
    ]

    # 添加条件逻辑
    for i, cond in enumerate(request.conditions):
        op = cond.get("operator", ">")
        value = cond.get("value", 0)
        action = cond.get("action", "filter")
        lines.append(f'    # 条件{i+1}: {cond.get("field", "close")} {op} {value}')
        if action == "filter":
            lines.append(f'    if bars[-1].{cond.get("field", "close")} {op} {value}:')
            lines.append('        return None  # 不满足条件')
            lines.append('')

    # 添加计算公式
    if request.formula:
        lines.append(f'    # 计算公式: {request.formula}')
        lines.append(f'    result = {request.formula}')
        lines.append('    return result')
    else:
        lines.append('    return bars[-1].close')

    code = "\n".join(lines)

    valid, msg = _validate_factor_code(code)
    return APIResponse(data={
        "name": request.name,
        "code": code,
        "validation": {"valid": valid, "message": msg},
    })


@router.post("/test", response_model=APIResponse)
async def test_strategy(request: StrategyTestRequest):
    """快速测试策略（真实回测：真实行情 + 回测引擎）"""
    try:
        # 1. 结构校验（现有）
        valid, msg = _validate_strategy_code(request.code)
        if not valid:
            return APIResponse(data={"valid": False, "message": msg, "metrics": None})

        symbol = request.symbol or "600519.SH"
        start_date = request.start_date or "2024-01-01"
        end_date = request.end_date or "2024-12-31"
        initial_capital = request.initial_capital or 100000.0

        # 2. 真实数据获取（异步放线程池，避免阻塞事件循环）
        import asyncio

        def _fetch():
            from finhack_pro.config import get_config
            from finhack_pro.data.fetcher import DataFetcher
            cfg = get_config()
            fetcher = DataFetcher(
                source=cfg.data.source,
                tushare_token=cfg.data.tushare_token,
                cache_dir=cfg.data.cache_dir,
                sources=cfg.data.sources or None,
                custom_source=cfg.data.custom_source,
            )
            df = fetcher.get_daily(symbol=symbol, start_date=start_date, end_date=end_date)
            return df

        try:
            data = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        except Exception as e:
            return APIResponse(data={
                "valid": False,
                "message": f"数据获取失败: {e}（请检查网络/标的代码/日期区间）",
                "metrics": None,
            })
        if data is None or data.empty:
            return APIResponse(data={
                "valid": False,
                "message": f"无法获取 {symbol} 的数据（请检查网络/标的代码/日期区间）",
                "metrics": None,
            })

        # 3. 适配器（内部含 AST 安全扫描，危险代码抛 StrategySecurityError）
        from finhack_pro.workshop.strategy_adapter import (
            StrategySecurityError,
            WorkshopStrategyAdapter,
        )

        try:
            adapter = WorkshopStrategyAdapter(request.code, symbol=symbol)
            # 预加载触发安全扫描（提前拒绝，避免进回测）
            adapter._load()
        except StrategySecurityError as e:
            return APIResponse(data={"valid": False, "message": str(e), "metrics": None})

        # 4. 真实回测
        from finhack_pro.backtest.runner import BacktestRunner

        def _run():
            runner = BacktestRunner()
            return runner.run(
                strategy=adapter,
                symbol=symbol,
                data=data,
                initial_capital=initial_capital,
                commission_rate=0.0003,
                stamp_tax_rate=0.001,
                slippage=0.001,
                params=request.params if hasattr(request, "params") else None,
            )

        try:
            result = await asyncio.get_event_loop().run_in_executor(None, _run)
        except Exception as e:
            return APIResponse(data={
                "valid": False,
                "message": f"回测执行失败: {e}",
                "metrics": None,
            })

        # 5. 组装响应（与旧结构兼容，数值转 Python 原生类型防 JSON 序列化失败）
        metrics = {
            "total_return": float(round(result.total_return * 100, 2)) if result.total_return else 0.0,
            "sharpe_ratio": float(round(result.sharpe_ratio, 2)) if result.sharpe_ratio else 0.0,
            "max_drawdown": float(round(result.max_drawdown, 2)) if result.max_drawdown else 0.0,
            "win_rate": float(round(result.win_rate * 100, 2)) if result.win_rate else 0.0,
            "total_trades": int(result.total_trades or 0),
            "final_equity": float(round(result.final_capital, 2)) if result.final_capital else 0.0,
        }
        equity_curve = result.equity_curve if hasattr(result, "equity_curve") else []
        trades = result.trades if hasattr(result, "trades") else []

        return APIResponse(data={
            "valid": True,
            "message": "策略测试完成（真实回测）",
            "metrics": metrics,
            "equity_curve": equity_curve[::5],  # 降采样
            "trades": trades[:20],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"策略测试失败: {str(e)}")


@router.get("/categories", response_model=APIResponse)
async def get_categories():
    """获取策略分类列表"""
    categories = list(set(t["category"] for t in STRATEGY_TEMPLATES))
    styles = list(set(t["style"] for t in STRATEGY_TEMPLATES))
    return APIResponse(data={"categories": categories, "styles": styles, "difficulties": ["⭐", "⭐⭐", "⭐⭐⭐"]})


@router.get("/factor-categories", response_model=APIResponse)
async def get_factor_categories():
    """获取因子分类列表"""
    categories = list(set(f["category"] for f in FACTOR_TEMPLATES))
    category_names = {
        "technical": "技术因子",
        "fundamental": "基本面因子",
        "sentiment": "情绪因子",
        "custom": "自定义因子",
    }
    return APIResponse(data={
        "categories": [{"id": c, "name": category_names.get(c, c)} for c in categories]
    })
