"""
多标的组合回测引擎 - Multi-symbol Portfolio Backtest

提供组合级别的回测功能，支持多标的同时管理、再平衡、以及多种资产分配策略。

Features:
- 等权重分配 (Equal Weight)
- 风险平价分配 (Risk Parity / Inverse Volatility)
- 自定义权重分配 (Custom Weights)
- 日/周/月再平衡频率
- 标的相关性分析
- 公司行为调整 (分红、拆股)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


class RebalanceFreq(Enum):
    """再平衡频率"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AllocationMethod(Enum):
    """资产分配方法"""
    EQUAL = "equal"
    RISK_PARITY = "risk_parity"
    CUSTOM = "custom"


@dataclass
class PortfolioAllocation:
    """组合分配信息"""
    symbol: str
    weight: float = 0.0           # 目标权重 (0~1)
    target_pct: float = 0.0       # 目标百分比
    current_value: float = 0.0    # 当前市值
    current_pct: float = 0.0      # 当前百分比
    shares: float = 0.0           # 持有股数
    avg_cost: float = 0.0         # 平均成本


@dataclass
class PortfolioRebalanceResult:
    """再平衡结果"""
    trades: List[Dict[str, Any]] = field(default_factory=list)
    old_weights: Dict[str, float] = field(default_factory=dict)
    new_weights: Dict[str, float] = field(default_factory=dict)
    turnover: float = 0.0         # 换手率


@dataclass
class PortfolioMetrics:
    """组合级别绩效指标"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    volatility: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    turnover: float = 0.0


@dataclass
class PortfolioBacktestConfig:
    """组合回测配置"""
    symbols: List[str] = field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_capital: float = 1_000_000.0
    rebalance_freq: str = "monthly"       # daily / weekly / monthly
    allocation_method: str = "equal"      # equal / risk_parity / custom
    custom_weights: Dict[str, float] = field(default_factory=dict)
    commission_rate: float = 0.0003
    slippage: float = 0.001
    risk_free_rate: float = 0.03          # 无风险利率 (年化)


@dataclass
class IndividualResult:
    """单个标的的回测结果"""
    symbol: str = ""
    total_return: float = 0.0
    annual_return: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    trades: int = 0


@dataclass
class PortfolioBacktestResult:
    """组合回测结果"""
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Optional[PortfolioMetrics] = None
    individual_results: List[IndividualResult] = field(default_factory=list)
    rebalance_history: List[Dict[str, Any]] = field(default_factory=list)
    config: Optional[PortfolioBacktestConfig] = None
    correlation_matrix: Optional[pd.DataFrame] = None


class PortfolioEngine:
    """多标的组合回测引擎

    支持多标的组合回测，提供等权重、风险平价、自定义权重等分配策略。

    Usage:
        config = PortfolioBacktestConfig(
            symbols=["AAPL", "MSFT", "GOOG"],
            initial_capital=1_000_000,
            rebalance_freq="monthly",
            allocation_method="equal",
        )
        engine = PortfolioEngine(config)
        data = {
            "AAPL": df_aapl,
            "MSFT": df_msft,
            "GOOG": df_goog,
        }
        result = engine.run(data)
    """

    def __init__(self, config: PortfolioBacktestConfig) -> None:
        """初始化组合回测引擎

        Args:
            config: 组合回测配置
        """
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        """验证配置参数"""
        if not self.config.symbols:
            raise ValueError("symbols 不能为空")

        if self.config.allocation_method == AllocationMethod.CUSTOM.value:
            if not self.config.custom_weights:
                raise ValueError("自定义权重模式下 custom_weights 不能为空")
            total_weight = sum(self.config.custom_weights.values())
            if abs(total_weight - 1.0) > 0.01:
                raise ValueError(
                    f"自定义权重总和必须为1.0，当前为 {total_weight:.4f}"
                )

        valid_freq = [f.value for f in RebalanceFreq]
        if self.config.rebalance_freq not in valid_freq:
            raise ValueError(
                f"无效的再平衡频率: {self.config.rebalance_freq}，"
                f"可选: {valid_freq}"
            )

        valid_methods = [m.value for m in AllocationMethod]
        if self.config.allocation_method not in valid_methods:
            raise ValueError(
                f"无效的分配方法: {self.config.allocation_method}，"
                f"可选: {valid_methods}"
            )

    def run(
        self,
        data: Dict[str, pd.DataFrame],
        adjustment_factors: Optional[Dict[str, pd.Series]] = None,
    ) -> PortfolioBacktestResult:
        """运行组合回测

        Args:
            data: 标的数据字典 {symbol: DataFrame}
                  DataFrame 需包含列: date, open, high, low, close, volume
            adjustment_factors: 调整因子字典 {symbol: pd.Series}
                用于处理分红、拆股等公司行为

        Returns:
            PortfolioBacktestResult 组合回测结果
        """
        logger.info(
            f"[PortfolioEngine] 开始组合回测: "
            f"标的={self.config.symbols}, "
            f"初始资金={self.config.initial_capital:,.0f}, "
            f"再平衡={self.config.rebalance_freq}, "
            f"分配方法={self.config.allocation_method}"
        )

        # 预处理数据
        aligned_data = self._align_data(data)
        if aligned_data.empty:
            logger.error("[PortfolioEngine] 对齐后的数据为空")
            return PortfolioBacktestResult(config=self.config)

        dates = aligned_data.index
        symbols = self.config.symbols
        capital = self.config.initial_capital
        commission_rate = self.config.commission_rate
        slippage = self.config.slippage

        # 初始化状态
        cash = capital
        positions: Dict[str, float] = {s: 0.0 for s in symbols}  # 持有股数
        avg_costs: Dict[str, float] = {s: 0.0 for s in symbols}  # 平均成本
        all_trades: List[Dict[str, Any]] = []
        rebalance_history: List[Dict[str, Any]] = []
        equity_records: List[Dict[str, Any]] = []

        # 计算收益率用于风险平价
        returns_data: Dict[str, pd.Series] = {}
        for symbol in symbols:
            if symbol in aligned_data.columns.get_level_values(0):
                close_col = (symbol, 'close')
                if close_col in aligned_data.columns:
                    returns_data[symbol] = aligned_data[close_col].pct_change().dropna()

        # 计算相关性矩阵
        corr_matrix = self._calculate_correlation(returns_data)

        # 初始分配
        target_allocations = self._compute_target_allocations(
            symbols, capital, returns_data
        )

        # 初始建仓
        prices = {
            s: aligned_data.iloc[0][(s, 'close')]
            for s in symbols
            if (s, 'close') in aligned_data.columns
        }
        initial_rebalance = self._rebalance(
            {s: 0.0 for s in symbols},  # 当前持有为0
            target_allocations,
            prices,
            cash,
            commission_rate,
            slippage,
        )
        cash = initial_rebalance['remaining_cash']
        for trade in initial_rebalance['trades']:
            symbol = trade['symbol']
            if trade['side'] == 'buy':
                positions[symbol] = trade['shares']
                avg_costs[symbol] = trade['price']
            all_trades.append(trade)

        rebalance_history.append({
            'date': str(dates[0]),
            'trades': len(initial_rebalance['trades']),
            'turnover': initial_rebalance['turnover'],
        })

        # 确定再平衡日期
        rebalance_dates = self._get_rebalance_dates(dates)

        # 逐日回测
        prev_date = dates[0]
        for i in range(1, len(dates)):
            current_date = dates[i]

            # 应用调整因子
            if adjustment_factors:
                for symbol in symbols:
                    if symbol in adjustment_factors and positions[symbol] > 0:
                        factor_series = adjustment_factors[symbol]
                        if current_date in factor_series.index:
                            factor = factor_series.loc[current_date]
                            positions[symbol] *= factor
                            avg_costs[symbol] /= factor

            # 计算组合市值
            portfolio_value = cash
            current_prices = {}
            for symbol in symbols:
                if (symbol, 'close') in aligned_data.columns:
                    price = aligned_data.iloc[i][(symbol, 'close')]
                    current_prices[symbol] = price
                    portfolio_value += positions[symbol] * price

            # 记录权益
            equity_records.append({
                'date': current_date,
                'equity': portfolio_value,
                'cash': cash,
                'position_value': portfolio_value - cash,
            })

            # 检查是否需要再平衡
            if current_date in rebalance_dates:
                target_allocations = self._compute_target_allocations(
                    symbols, portfolio_value, returns_data
                )

                current_values = {}
                for symbol in symbols:
                    if symbol in current_prices:
                        current_values[symbol] = (
                            positions[symbol] * current_prices[symbol]
                        )

                rebalance_result = self._rebalance(
                    current_values,
                    target_allocations,
                    current_prices,
                    cash,
                    commission_rate,
                    slippage,
                )
                cash = rebalance_result['remaining_cash']
                for trade in rebalance_result['trades']:
                    symbol = trade['symbol']
                    if trade['side'] == 'buy':
                        old_shares = positions[symbol]
                        old_cost = avg_costs[symbol]
                        new_shares = trade['shares']
                        new_cost = trade['price']
                        total_shares = old_shares + new_shares
                        if total_shares > 0:
                            avg_costs[symbol] = (
                                (old_shares * old_cost + new_shares * new_cost)
                                / total_shares
                            )
                        positions[symbol] = total_shares
                    elif trade['side'] == 'sell':
                        positions[symbol] -= trade['shares']
                        if positions[symbol] < 1e-8:
                            positions[symbol] = 0.0
                            avg_costs[symbol] = 0.0
                    all_trades.append(trade)

                rebalance_history.append({
                    'date': str(current_date),
                    'trades': len(rebalance_result['trades']),
                    'turnover': rebalance_result['turnover'],
                })

            prev_date = current_date

        # 构建权益曲线 DataFrame
        equity_df = pd.DataFrame(equity_records)
        if not equity_df.empty:
            equity_df.set_index('date', inplace=True)

        # 计算绩效指标
        metrics = self._calculate_portfolio_metrics(equity_df, all_trades)

        # 计算单个标的绩效
        individual_results = self._calculate_individual_results(
            aligned_data, symbols
        )

        result = PortfolioBacktestResult(
            equity_curve=equity_df,
            trades=all_trades,
            metrics=metrics,
            individual_results=individual_results,
            rebalance_history=rebalance_history,
            config=self.config,
            correlation_matrix=corr_matrix,
        )

        logger.info(
            f"[PortfolioEngine] 回测完成: "
            f"总收益率={metrics.total_return:.2%}, "
            f"夏普比率={metrics.sharpe_ratio:.2f}, "
            f"最大回撤={metrics.max_drawdown:.2%}, "
            f"交易次数={metrics.total_trades}"
        )

        return result

    def _align_data(
        self, data: Dict[str, pd.DataFrame]
    ) -> pd.DataFrame:
        """对齐多标的数据到统一日期索引

        Args:
            data: 标的数据字典

        Returns:
            对齐后的 MultiIndex DataFrame
        """
        dfs = {}
        for symbol, df in data.items():
            if df.empty:
                continue
            df_copy = df.copy()
            if 'date' in df_copy.columns:
                df_copy['date'] = pd.to_datetime(df_copy['date'])
                df_copy.set_index('date', inplace=True)

            required_cols = ['open', 'high', 'low', 'close', 'volume']
            for col in required_cols:
                if col not in df_copy.columns:
                    df_copy[col] = np.nan

            df_copy = df_copy[required_cols]
            df_copy.columns = pd.MultiIndex.from_product(
                [[symbol], required_cols]
            )
            dfs[symbol] = df_copy

        if not dfs:
            return pd.DataFrame()

        aligned = pd.concat(dfs.values(), axis=1)
        aligned.sort_index(inplace=True)

        # 按配置的日期范围过滤
        if self.config.start_date:
            start = pd.to_datetime(self.config.start_date)
            aligned = aligned[aligned.index >= start]
        if self.config.end_date:
            end = pd.to_datetime(self.config.end_date)
            aligned = aligned[aligned.index <= end]

        # 去除全为NaN的行
        aligned.dropna(how='all', inplace=True)

        return aligned

    def _get_rebalance_dates(self, dates: pd.DatetimeIndex) -> set:
        """获取再平衡日期集合

        Args:
            dates: 所有交易日

        Returns:
            需要再平衡的日期集合
        """
        rebalance_dates = set()
        freq = self.config.rebalance_freq

        if freq == RebalanceFreq.DAILY.value:
            # 每天都再平衡（跳过第一天，因为初始建仓已处理）
            rebalance_dates = set(dates[1:])
        elif freq == RebalanceFreq.WEEKLY.value:
            # 每周一再平衡
            for d in dates:
                if d.weekday() == 0:
                    rebalance_dates.add(d)
        elif freq == RebalanceFreq.MONTHLY.value:
            # 每月第一个交易日再平衡
            seen_months = set()
            for d in dates:
                month_key = (d.year, d.month)
                if month_key not in seen_months:
                    seen_months.add(month_key)
                    rebalance_dates.add(d)

        return rebalance_dates

    def _compute_target_allocations(
        self,
        symbols: List[str],
        capital: float,
        returns_data: Dict[str, pd.Series],
    ) -> Dict[str, float]:
        """计算目标分配金额

        Args:
            symbols: 标的列表
            capital: 总资金
            returns_data: 收益率数据

        Returns:
            {symbol: target_value} 目标市值字典
        """
        method = self.config.allocation_method

        if method == AllocationMethod.EQUAL.value:
            return self._equal_weight_allocation(symbols, capital)
        elif method == AllocationMethod.RISK_PARITY.value:
            return self._risk_parity_allocation(returns_data, capital)
        elif method == AllocationMethod.CUSTOM.value:
            return {
                s: self.config.custom_weights.get(s, 0.0) * capital
                for s in symbols
            }
        else:
            return self._equal_weight_allocation(symbols, capital)

    def _equal_weight_allocation(
        self, symbols: List[str], capital: float
    ) -> Dict[str, float]:
        """等权重分配

        Args:
            symbols: 标的列表
            capital: 总资金

        Returns:
            {symbol: target_value}
        """
        n = len(symbols)
        weight = 1.0 / n
        return {s: capital * weight for s in symbols}

    def _risk_parity_allocation(
        self,
        returns: Dict[str, pd.Series],
        capital: float,
    ) -> Dict[str, float]:
        """风险平价分配（逆波动率）

        每个标的分配的权重与其波动率的倒数成正比。

        Args:
            returns: 收益率字典 {symbol: pd.Series}
            capital: 总资金

        Returns:
            {symbol: target_value}
        """
        if not returns:
            n = len(self.config.symbols)
            weight = 1.0 / n
            return {s: capital * weight for s in self.config.symbols}

        volatilities = {}
        for symbol, ret_series in returns.items():
            if len(ret_series) > 1:
                vol = ret_series.std()
                if vol > 0:
                    volatilities[symbol] = vol
                else:
                    volatilities[symbol] = 1e-8
            else:
                volatilities[symbol] = 1e-8

        # 逆波动率权重
        inv_vols = {s: 1.0 / v for s, v in volatilities.items()}
        total_inv_vol = sum(inv_vols.values())

        if total_inv_vol == 0:
            n = len(self.config.symbols)
            weight = 1.0 / n
            return {s: capital * weight for s in self.config.symbols}

        allocations = {}
        for symbol in self.config.symbols:
            if symbol in inv_vols:
                allocations[symbol] = capital * inv_vols[symbol] / total_inv_vol
            else:
                allocations[symbol] = 0.0

        return allocations

    def _rebalance(
        self,
        current_values: Dict[str, float],
        target_values: Dict[str, float],
        prices: Dict[str, float],
        cash: float,
        commission_rate: float,
        slippage: float,
    ) -> Dict[str, Any]:
        """执行再平衡

        Args:
            current_values: 当前持仓市值 {symbol: value}
            target_values: 目标市值 {symbol: value}
            prices: 当前价格 {symbol: price}
            cash: 可用现金
            commission_rate: 佣金费率
            slippage: 滑点

        Returns:
            包含 trades, turnover, remaining_cash 的字典
        """
        trades: List[Dict[str, Any]] = []
        total_turnover = 0.0

        # 先计算所有卖出交易，释放现金
        sell_trades = []
        for symbol, target_val in target_values.items():
            current_val = current_values.get(symbol, 0.0)
            price = prices.get(symbol, 0.0)
            if price <= 0:
                continue

            diff = current_val - target_val
            if diff > 0:
                # 需要卖出
                shares_to_sell = diff / price
                sell_price = price * (1 - slippage)
                proceeds = shares_to_sell * sell_price
                commission = max(proceeds * commission_rate, 0.0)
                net_proceeds = proceeds - commission

                sell_trades.append({
                    'symbol': symbol,
                    'side': 'sell',
                    'shares': shares_to_sell,
                    'price': sell_price,
                    'value': proceeds,
                    'commission': commission,
                    'net_value': net_proceeds,
                })
                total_turnover += diff

        # 执行卖出
        for trade in sell_trades:
            cash += trade['net_value']
            trades.append({
                'symbol': trade['symbol'],
                'side': 'sell',
                'shares': round(trade['shares'], 6),
                'price': round(trade['price'], 4),
                'value': round(trade['value'], 2),
                'commission': round(trade['commission'], 2),
            })

        # 计算买入交易
        for symbol, target_val in target_values.items():
            current_val = current_values.get(symbol, 0.0)
            price = prices.get(symbol, 0.0)
            if price <= 0:
                continue

            diff = target_val - current_val
            if diff > 0:
                # 需要买入
                buy_price = price * (1 + slippage)
                shares_to_buy = diff / buy_price
                cost = shares_to_buy * buy_price
                commission = max(cost * commission_rate, 0.0)
                total_cost = cost + commission

                if total_cost <= cash:
                    cash -= total_cost
                    trades.append({
                        'symbol': symbol,
                        'side': 'buy',
                        'shares': round(shares_to_buy, 6),
                        'price': round(buy_price, 4),
                        'value': round(cost, 2),
                        'commission': round(commission, 2),
                    })
                    total_turnover += diff

        return {
            'trades': trades,
            'turnover': total_turnover,
            'remaining_cash': cash,
        }

    def _calculate_portfolio_metrics(
        self,
        equity_curve: pd.DataFrame,
        trades: List[Dict[str, Any]],
    ) -> PortfolioMetrics:
        """计算组合级别绩效指标

        Args:
            equity_curve: 权益曲线 DataFrame
            trades: 交易记录

        Returns:
            PortfolioMetrics
        """
        metrics = PortfolioMetrics()

        if equity_curve.empty:
            return metrics

        equity = equity_curve['equity']
        initial = equity.iloc[0] if len(equity) > 0 else self.config.initial_capital
        final = equity.iloc[-1] if len(equity) > 0 else initial

        # 总收益率
        metrics.total_return = (final - initial) / initial if initial > 0 else 0.0

        # 年化收益率
        if len(equity) > 1:
            years = (equity.index[-1] - equity.index[0]).days / 365.25
            if years > 0:
                metrics.annual_return = (
                    (1 + metrics.total_return) ** (1 / years) - 1
                )
            else:
                metrics.annual_return = metrics.total_return
        else:
            metrics.annual_return = metrics.total_return

        # 日收益率
        daily_returns = equity.pct_change().dropna()

        # 波动率
        if len(daily_returns) > 1:
            metrics.volatility = daily_returns.std() * np.sqrt(252)
        else:
            metrics.volatility = 0.0

        # 夏普比率
        rf_daily = self.config.risk_free_rate / 252
        if len(daily_returns) > 1 and metrics.volatility > 0:
            excess_returns = daily_returns - rf_daily
            metrics.sharpe_ratio = (
                excess_returns.mean() / daily_returns.std() * np.sqrt(252)
            )
        else:
            metrics.sharpe_ratio = 0.0

        # 最大回撤
        peak = equity.cummax()
        drawdown = (equity - peak) / peak
        metrics.max_drawdown = abs(drawdown.min()) if len(drawdown) > 0 else 0.0

        # Calmar 比率
        if metrics.max_drawdown > 0:
            metrics.calmar_ratio = metrics.annual_return / metrics.max_drawdown
        else:
            metrics.calmar_ratio = 0.0

        # Sortino 比率
        if len(daily_returns) > 1:
            downside_returns = daily_returns[daily_returns < 0]
            if len(downside_returns) > 0 and downside_returns.std() > 0:
                downside_vol = downside_returns.std() * np.sqrt(252)
                metrics.sortino_ratio = (
                    (daily_returns.mean() - rf_daily) / downside_vol * np.sqrt(252)
                )
            else:
                metrics.sortino_ratio = 0.0
        else:
            metrics.sortino_ratio = 0.0

        # 交易统计
        metrics.total_trades = len(trades)

        # 胜率和盈亏比
        sell_trades = [t for t in trades if t.get('side') == 'sell']
        # 简化处理：使用交易价值变化来判断盈亏
        # 在组合回测中，盈亏需要追踪每笔交易的完整买入卖出对
        # 这里使用简化逻辑
        if sell_trades:
            profitable = sum(
                1 for t in sell_trades
                if t.get('value', 0) > 0
            )
            metrics.win_rate = profitable / len(sell_trades)
        else:
            metrics.win_rate = 0.0

        # 换手率
        if len(equity) > 0:
            total_trade_value = sum(
                abs(t.get('value', 0)) for t in trades
            )
            avg_equity = equity.mean()
            metrics.turnover = (
                total_trade_value / avg_equity if avg_equity > 0 else 0.0
            )

        return metrics

    def _calculate_individual_results(
        self,
        aligned_data: pd.DataFrame,
        symbols: List[str],
    ) -> List[IndividualResult]:
        """计算单个标的的绩效

        Args:
            aligned_data: 对齐后的数据
            symbols: 标的列表

        Returns:
            IndividualResult 列表
        """
        results = []
        for symbol in symbols:
            close_col = (symbol, 'close')
            if close_col not in aligned_data.columns:
                continue

            prices = aligned_data[close_col].dropna()
            if len(prices) < 2:
                results.append(IndividualResult(symbol=symbol))
                continue

            total_ret = (prices.iloc[-1] / prices.iloc[0]) - 1

            years = (prices.index[-1] - prices.index[0]).days / 365.25
            annual_ret = (
                (1 + total_ret) ** (1 / max(years, 0.01)) - 1
                if years > 0 else total_ret
            )

            daily_ret = prices.pct_change().dropna()
            vol = daily_ret.std() * np.sqrt(252) if len(daily_ret) > 1 else 0.0

            rf_daily = self.config.risk_free_rate / 252
            sharpe = (
                (daily_ret.mean() - rf_daily) / daily_ret.std() * np.sqrt(252)
                if len(daily_ret) > 1 and daily_ret.std() > 0 else 0.0
            )

            peak = prices.cummax()
            dd = (prices - peak) / peak
            max_dd = abs(dd.min()) if len(dd) > 0 else 0.0

            results.append(IndividualResult(
                symbol=symbol,
                total_return=total_ret,
                annual_return=annual_ret,
                volatility=vol,
                sharpe_ratio=sharpe,
                max_drawdown=max_dd,
                trades=0,
            ))

        return results

    def _calculate_correlation(
        self, returns_data: Dict[str, pd.Series]
    ) -> pd.DataFrame:
        """计算标的相关性矩阵

        Args:
            returns_data: 收益率字典

        Returns:
            相关性矩阵 DataFrame
        """
        if not returns_data:
            return pd.DataFrame()

        df = pd.DataFrame(returns_data)
        if df.empty or df.shape[1] < 2:
            return pd.DataFrame()

        return df.corr()

    @staticmethod
    def analyze_correlation(
        returns_data: Dict[str, pd.Series],
        threshold: float = 0.7,
    ) -> Dict[str, Any]:
        """分析标的相关性，识别高相关标的对

        Args:
            returns_data: 收益率字典
            threshold: 高相关阈值

        Returns:
            包含相关性矩阵和高相关标的对的分析结果
        """
        if not returns_data:
            return {'correlation_matrix': pd.DataFrame(), 'high_corr_pairs': []}

        df = pd.DataFrame(returns_data)
        corr = df.corr()

        high_corr_pairs = []
        symbols = corr.columns.tolist()
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                corr_val = corr.iloc[i, j]
                if not np.isnan(corr_val) and abs(corr_val) >= threshold:
                    high_corr_pairs.append({
                        'symbol_1': symbols[i],
                        'symbol_2': symbols[j],
                        'correlation': round(corr_val, 4),
                    })

        return {
            'correlation_matrix': corr,
            'high_corr_pairs': high_corr_pairs,
        }
