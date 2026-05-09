"""
回测报告可视化 - Backtest Report Visualization

生成专业的回测报告，支持 HTML 和 PDF 输出，包含权益曲线、回撤图、
月度收益热力图、交易分布图等可视化图表。

Features:
- 自包含 HTML 报告（内嵌 base64 图片）
- 亮色/暗色主题
- 权益曲线、回撤、月度收益、交易分析
- 绩效指标表格
- PDF 导出（可选，依赖 weasyprint）
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)

# 使用非交互式后端
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LinearSegmentedColormap


@dataclass
class ReportConfig:
    """报告配置"""
    title: str = "Portfolio Backtest Report"
    theme: str = "dark"              # light / dark
    include_trades: bool = True
    max_trades_display: int = 50
    currency: str = "USD"
    benchmark_label: str = "Benchmark"


class BacktestReport:
    """回测报告生成器

    生成专业的回测分析报告，支持多种可视化图表和导出格式。

    Usage:
        report = BacktestReport(result, output_dir="reports")
        html_path = report.generate_html_report()
        summary = report.generate_summary()
    """

    def __init__(
        self,
        result: Union[Any, dict],
        output_dir: str = "reports",
        config: Optional[ReportConfig] = None,
    ) -> None:
        """初始化报告生成器

        Args:
            result: 回测结果 (PortfolioBacktestResult 或字典)
            output_dir: 输出目录
            config: 报告配置
        """
        self.result = result
        self.output_dir = output_dir
        self.config = config or ReportConfig()
        self._equity_curve: Optional[pd.DataFrame] = None
        self._trades: List[Dict[str, Any]] = []
        self._metrics: Dict[str, Any] = {}
        self._parse_result()

    def _parse_result(self) -> None:
        """解析回测结果"""
        if isinstance(self.result, dict):
            self._metrics = self.result.get('metrics', {})
            self._trades = self.result.get('trades', [])
            equity_data = self.result.get('equity_curve')
            if isinstance(equity_data, pd.DataFrame):
                self._equity_curve = equity_data
            elif isinstance(equity_data, list) and equity_data:
                self._equity_curve = pd.DataFrame(equity_data)
                if 'date' in self._equity_curve.columns:
                    self._equity_curve.set_index('date', inplace=True)
        else:
            # PortfolioBacktestResult 对象
            if hasattr(self.result, 'equity_curve'):
                self._equity_curve = self.result.equity_curve
            if hasattr(self.result, 'trades'):
                self._trades = self.result.trades
            if hasattr(self.result, 'metrics'):
                m = self.result.metrics
                if m is not None:
                    if hasattr(m, '__dataclass_fields__'):
                        self._metrics = {
                            k: getattr(m, k) for k in m.__dataclass_fields__
                        }
                    else:
                        self._metrics = dict(m) if isinstance(m, dict) else {}

    def generate_summary(self) -> dict:
        """生成文本摘要

        Returns:
            包含关键指标的摘要字典
        """
        summary = {
            'title': self.config.title,
            'metrics': self._metrics,
            'total_trades': len(self._trades),
        }

        if self._equity_curve is not None and not self._equity_curve.empty:
            equity = self._equity_curve['equity'] if 'equity' in self._equity_curve.columns else self._equity_curve.iloc[:, 0]
            summary['start_date'] = str(self._equity_curve.index[0])
            summary['end_date'] = str(self._equity_curve.index[-1])
            summary['initial_equity'] = float(equity.iloc[0])
            summary['final_equity'] = float(equity.iloc[-1])
            summary['total_return'] = float(
                (equity.iloc[-1] - equity.iloc[0]) / equity.iloc[0]
                if equity.iloc[0] > 0 else 0
            )

        return summary

    def generate_html_report(self) -> str:
        """生成自包含 HTML 报告

        Returns:
            HTML 报告文件路径
        """
        import os
        os.makedirs(self.output_dir, exist_ok=True)

        html_content = self._build_html()
        output_path = os.path.join(self.output_dir, "backtest_report.html")

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        logger.info(f"[BacktestReport] HTML 报告已生成: {output_path}")
        return output_path

    def export_to_pdf(self, output_path: str = "") -> str:
        """导出为 PDF

        Args:
            output_path: 输出文件路径

        Returns:
            PDF 文件路径，如果 weasyprint 不可用则返回空字符串
        """
        try:
            from weasyprint import HTML
        except ImportError:
            logger.warning(
                "[BacktestReport] weasyprint 未安装，无法导出 PDF。"
                "请运行: pip install weasyprint"
            )
            return ""

        import os
        html_content = self._build_html()

        if not output_path:
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, "backtest_report.pdf")

        HTML(string=html_content).write_pdf(output_path)
        logger.info(f"[BacktestReport] PDF 报告已导出: {output_path}")
        return output_path

    def _build_html(self) -> str:
        """构建完整的 HTML 报告"""
        is_dark = self.config.theme == "dark"

        # 主题颜色
        if is_dark:
            bg_color = "#1a1a2e"
            card_bg = "#16213e"
            text_color = "#e0e0e0"
            accent_color = "#0f3460"
            highlight_color = "#e94560"
            table_header_bg = "#0f3460"
            table_row_alt = "#1a1a3e"
            border_color = "#333366"
        else:
            bg_color = "#f5f5f5"
            card_bg = "#ffffff"
            text_color = "#333333"
            accent_color = "#2196F3"
            highlight_color = "#FF5722"
            table_header_bg = "#2196F3"
            table_row_alt = "#f0f4f8"
            border_color = "#dddddd"

        # 生成图表
        equity_chart_b64 = self._render_equity_chart()
        drawdown_chart_b64 = self._render_drawdown_chart()
        monthly_heatmap_b64 = self._render_monthly_returns_heatmap()
        trade_dist_b64 = self._render_trade_distribution()

        # 构建指标表格
        metrics_html = self._render_metrics_table()

        # 构建交易列表
        trades_html = ""
        if self.config.include_trades:
            trades_html = self._render_trade_list()

        # 构建单个标的绩效
        individual_html = ""
        if hasattr(self.result, 'individual_results') and self.result.individual_results:
            individual_html = self._render_individual_table()

        # 相关性矩阵
        corr_html = ""
        if hasattr(self.result, 'correlation_matrix') and self.result.correlation_matrix is not None:
            corr_html = self._render_correlation_table()

        html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.config.title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                         'Helvetica Neue', Arial, sans-serif;
            background-color: {bg_color};
            color: {text_color};
            line-height: 1.6;
            padding: 20px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            padding: 30px 0;
            border-bottom: 2px solid {border_color};
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 2em;
            color: {accent_color};
            margin-bottom: 10px;
        }}
        .header .subtitle {{
            font-size: 1.1em;
            opacity: 0.7;
        }}
        .card {{
            background-color: {card_bg};
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid {border_color};
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .card h2 {{
            font-size: 1.4em;
            color: {accent_color};
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid {border_color};
        }}
        .chart-container {{
            text-align: center;
            margin: 16px 0;
        }}
        .chart-container img {{
            max-width: 100%;
            height: auto;
            border-radius: 8px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 16px 0;
        }}
        th {{
            background-color: {table_header_bg};
            color: white;
            padding: 12px 16px;
            text-align: left;
            font-weight: 600;
        }}
        td {{
            padding: 10px 16px;
            border-bottom: 1px solid {border_color};
        }}
        tr:nth-child(even) {{
            background-color: {table_row_alt};
        }}
        tr:hover {{
            background-color: {accent_color}22;
        }}
        .metric-value {{
            font-weight: 700;
            font-size: 1.1em;
        }}
        .positive {{ color: #4CAF50; }}
        .negative {{ color: {highlight_color}; }}
        .grid-2 {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
        }}
        @media (max-width: 768px) {{
            .grid-2 {{ grid-template-columns: 1fr; }}
        }}
        .footer {{
            text-align: center;
            padding: 20px 0;
            margin-top: 30px;
            border-top: 1px solid {border_color};
            opacity: 0.6;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{self.config.title}</h1>
            <div class="subtitle">
                Generated by FinHack Pro | {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
        </div>

        <!-- Key Metrics -->
        <div class="card">
            <h2>Key Metrics</h2>
            {metrics_html}
        </div>

        <!-- Charts -->
        <div class="grid-2">
            <div class="card">
                <h2>Equity Curve</h2>
                <div class="chart-container">
                    {equity_chart_b64}
                </div>
            </div>
            <div class="card">
                <h2>Drawdown</h2>
                <div class="chart-container">
                    {drawdown_chart_b64}
                </div>
            </div>
        </div>

        <div class="grid-2">
            <div class="card">
                <h2>Monthly Returns</h2>
                <div class="chart-container">
                    {monthly_heatmap_b64}
                </div>
            </div>
            <div class="card">
                <h2>Trade Distribution</h2>
                <div class="chart-container">
                    {trade_dist_b64}
                </div>
            </div>
        </div>

        {individual_html}

        {corr_html}

        <!-- Trade List -->
        {trades_html}

        <div class="footer">
            FinHack Pro Backtest Report | Confidential
        </div>
    </div>
</body>
</html>"""
        return html

    def _fig_to_base64(self, fig: plt.Figure) -> str:
        """将 matplotlib Figure 转为 base64 编码的 HTML img 标签"""
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=120, bbox_inches='tight',
                    facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        img_b64 = base64.b64encode(buf.read()).decode('utf-8')
        return f'<img src="data:image/png;base64,{img_b64}" alt="chart">'

    def _get_theme_colors(self) -> Dict[str, str]:
        """获取当前主题的图表颜色"""
        if self.config.theme == "dark":
            return {
                'bg': '#1a1a2e',
                'fg': '#e0e0e0',
                'grid': '#333366',
                'line': '#4FC3F7',
                'fill': '#4FC3F7',
                'negative': '#e94560',
                'positive': '#4CAF50',
            }
        else:
            return {
                'bg': '#ffffff',
                'fg': '#333333',
                'grid': '#cccccc',
                'line': '#2196F3',
                'fill': '#2196F3',
                'negative': '#FF5722',
                'positive': '#4CAF50',
            }

    def _render_equity_chart(self, benchmark_curve=None) -> str:
        """渲染权益曲线图

        Args:
            benchmark_curve: 基准曲线 (可选)

        Returns:
            base64 编码的 HTML img 标签
        """
        if self._equity_curve is None or self._equity_curve.empty:
            return '<p>No equity data available</p>'

        colors = self._get_theme_colors()
        equity = self._equity_curve['equity'] if 'equity' in self._equity_curve.columns else self._equity_curve.iloc[:, 0]

        fig, ax = plt.subplots(figsize=(8, 4), facecolor=colors['bg'])
        ax.set_facecolor(colors['bg'])

        dates = equity.index
        if not isinstance(dates, pd.DatetimeIndex):
            try:
                dates = pd.to_datetime(dates)
            except Exception:
                dates = range(len(equity))

        ax.plot(dates, equity.values, color=colors['line'], linewidth=1.5, label='Portfolio')
        ax.fill_between(dates, equity.values, alpha=0.15, color=colors['fill'])

        if benchmark_curve is not None:
            ax.plot(dates, benchmark_curve.values, color='#FF9800',
                    linewidth=1.0, linestyle='--', label='Benchmark', alpha=0.7)
            ax.legend(facecolor=colors['bg'], edgecolor=colors['grid'],
                      labelcolor=colors['fg'])

        ax.set_title('Equity Curve', color=colors['fg'], fontsize=12, fontweight='bold')
        ax.set_xlabel('Date', color=colors['fg'])
        ax.set_ylabel(f'Equity ({self.config.currency})', color=colors['fg'])
        ax.tick_params(colors=colors['fg'])
        ax.grid(True, alpha=0.3, color=colors['grid'])
        for spine in ax.spines.values():
            spine.set_color(colors['grid'])

        fig.autofmt_xdate()
        fig.tight_layout()

        return self._fig_to_base64(fig)

    def _render_drawdown_chart(self) -> str:
        """渲染回撤图

        Returns:
            base64 编码的 HTML img 标签
        """
        if self._equity_curve is None or self._equity_curve.empty:
            return '<p>No drawdown data available</p>'

        colors = self._get_theme_colors()
        equity = self._equity_curve['equity'] if 'equity' in self._equity_curve.columns else self._equity_curve.iloc[:, 0]

        peak = equity.cummax()
        drawdown = (equity - peak) / peak * 100

        fig, ax = plt.subplots(figsize=(8, 4), facecolor=colors['bg'])
        ax.set_facecolor(colors['bg'])

        dates = drawdown.index
        if not isinstance(dates, pd.DatetimeIndex):
            try:
                dates = pd.to_datetime(dates)
            except Exception:
                dates = range(len(drawdown))

        ax.fill_between(dates, drawdown.values, 0, alpha=0.4, color=colors['negative'])
        ax.plot(dates, drawdown.values, color=colors['negative'], linewidth=1.0)

        ax.set_title('Drawdown (%)', color=colors['fg'], fontsize=12, fontweight='bold')
        ax.set_xlabel('Date', color=colors['fg'])
        ax.set_ylabel('Drawdown (%)', color=colors['fg'])
        ax.tick_params(colors=colors['fg'])
        ax.grid(True, alpha=0.3, color=colors['grid'])
        for spine in ax.spines.values():
            spine.set_color(colors['grid'])

        fig.autofmt_xdate()
        fig.tight_layout()

        return self._fig_to_base64(fig)

    def _render_monthly_returns_heatmap(self) -> str:
        """渲染月度收益热力图

        Returns:
            base64 编码的 HTML img 标签
        """
        if self._equity_curve is None or self._equity_curve.empty:
            return '<p>No monthly returns data available</p>'

        colors = self._get_theme_colors()
        equity = self._equity_curve['equity'] if 'equity' in self._equity_curve.columns else self._equity_curve.iloc[:, 0]

        # 计算月度收益率
        monthly = equity.resample('ME').last()
        monthly_returns = monthly.pct_change().dropna() * 100

        if monthly_returns.empty:
            return '<p>Insufficient data for monthly returns</p>'

        # 构建年月表格
        monthly_returns.index = pd.to_datetime(monthly_returns.index)
        pivot = pd.DataFrame({
            'year': monthly_returns.index.year,
            'month': monthly_returns.index.month,
            'return': monthly_returns.values,
        })
        pivot_table = pivot.pivot(index='year', columns='month', values='return')

        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        # Only use columns that exist in the pivot table
        existing_months = [month_names[m - 1] for m in pivot_table.columns]
        pivot_table.columns = existing_months

        fig, ax = plt.subplots(figsize=(10, max(4, len(pivot_table) * 0.6)),
                               facecolor=colors['bg'])
        ax.set_facecolor(colors['bg'])

        # 自定义颜色映射
        cmap_colors = [colors['negative'], '#333333', colors['positive']]
        cmap = LinearSegmentedColormap.from_list('custom', cmap_colors, N=256)

        data = pivot_table.values
        im = ax.imshow(data, cmap=cmap, aspect='auto', interpolation='nearest')

        # 添加数值标注
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                val = data[i, j]
                if not np.isnan(val):
                    text_color = 'white' if abs(val) > np.nanmax(np.abs(data)) * 0.6 else colors['fg']
                    ax.text(j, i, f'{val:.1f}%', ha='center', va='center',
                            color=text_color, fontsize=8)

        ax.set_xticks(range(len(pivot_table.columns)))
        ax.set_xticklabels(pivot_table.columns, color=colors['fg'])
        ax.set_yticks(range(len(pivot_table.index)))
        ax.set_yticklabels(pivot_table.index.astype(int), color=colors['fg'])
        ax.set_title('Monthly Returns (%)', color=colors['fg'], fontsize=12, fontweight='bold')
        ax.tick_params(colors=colors['fg'])

        fig.tight_layout()

        return self._fig_to_base64(fig)

    def _render_trade_distribution(self) -> str:
        """渲染交易盈亏分布图

        Returns:
            base64 编码的 HTML img 标签
        """
        if not self._trades:
            return '<p>No trade data available</p>'

        colors = self._get_theme_colors()

        # 提取交易价值
        trade_values = []
        for t in self._trades:
            val = t.get('value', t.get('pnl', 0))
            if val is not None:
                trade_values.append(val)

        if not trade_values:
            return '<p>No trade value data available</p>'

        fig, ax = plt.subplots(figsize=(8, 4), facecolor=colors['bg'])
        ax.set_facecolor(colors['bg'])

        n, bins, patches = ax.hist(
            trade_values, bins=min(30, max(5, len(trade_values) // 5)),
            edgecolor=colors['bg'], linewidth=0.5, alpha=0.8
        )

        # 根据正负着色
        for patch, left_edge in zip(patches, bins[:-1]):
            if left_edge >= 0:
                patch.set_facecolor(colors['positive'])
            else:
                patch.set_facecolor(colors['negative'])

        ax.axvline(x=0, color=colors['fg'], linestyle='--', alpha=0.5, linewidth=0.8)

        ax.set_title('Trade Value Distribution', color=colors['fg'],
                     fontsize=12, fontweight='bold')
        ax.set_xlabel('Trade Value', color=colors['fg'])
        ax.set_ylabel('Frequency', color=colors['fg'])
        ax.tick_params(colors=colors['fg'])
        ax.grid(True, alpha=0.3, color=colors['grid'])
        for spine in ax.spines.values():
            spine.set_color(colors['grid'])

        fig.tight_layout()

        return self._fig_to_base64(fig)

    def _render_metrics_table(self) -> str:
        """渲染绩效指标表格

        Returns:
            HTML 表格字符串
        """
        if not self._metrics:
            return '<p>No metrics available</p>'

        # 指标显示名称映射
        display_names = {
            'total_return': ('Total Return', '{:.2%}'),
            'annual_return': ('Annual Return', '{:.2%}'),
            'sharpe_ratio': ('Sharpe Ratio', '{:.2f}'),
            'sortino_ratio': ('Sortino Ratio', '{:.2f}'),
            'calmar_ratio': ('Calmar Ratio', '{:.2f}'),
            'max_drawdown': ('Max Drawdown', '{:.2%}'),
            'volatility': ('Volatility', '{:.2%}'),
            'win_rate': ('Win Rate', '{:.2%}'),
            'profit_loss_ratio': ('Profit/Loss Ratio', '{:.2f}'),
            'total_trades': ('Total Trades', '{:d}'),
            'turnover': ('Turnover', '{:.2f}'),
        }

        rows = []
        for key, (name, fmt) in display_names.items():
            value = self._metrics.get(key)
            if value is None:
                continue
            try:
                formatted = fmt.format(value)
            except (ValueError, TypeError):
                formatted = str(value)

            # 正负颜色
            if key in ('total_return', 'annual_return', 'win_rate'):
                css_class = 'positive' if value > 0 else 'negative' if value < 0 else ''
            elif key == 'max_drawdown':
                css_class = 'negative' if value > 0 else ''
            elif key == 'sharpe_ratio':
                css_class = 'positive' if value > 1 else 'negative' if value < 0 else ''
            else:
                css_class = ''

            rows.append(
                f'<tr><td>{name}</td>'
                f'<td class="metric-value {css_class}">{formatted}</td></tr>'
            )

        # 两列布局
        mid = (len(rows) + 1) // 2
        left_rows = rows[:mid]
        right_rows = rows[mid:]

        left_html = ''.join(left_rows)
        right_html = ''.join(right_rows)

        return f"""
        <div class="grid-2">
            <table>
                <thead><tr><th>Metric</th><th>Value</th></tr></thead>
                <tbody>{left_html}</tbody>
            </table>
            <table>
                <thead><tr><th>Metric</th><th>Value</th></tr></thead>
                <tbody>{right_html}</tbody>
            </table>
        </div>"""

    def _render_trade_list(self) -> str:
        """渲染交易列表表格

        Returns:
            HTML 表格字符串
        """
        if not self._trades:
            return ''

        display_trades = self._trades[:self.config.max_trades_display]
        rows = []
        for i, t in enumerate(display_trades):
            date = t.get('date', '')
            symbol = t.get('symbol', '')
            side = t.get('side', t.get('action', ''))
            shares = t.get('shares', t.get('volume', ''))
            price = t.get('price', '')
            value = t.get('value', t.get('pnl', ''))

            side_class = 'positive' if side == 'buy' else 'negative' if side == 'sell' else ''

            shares_str = f'{shares:,.2f}' if isinstance(shares, (int, float)) else str(shares)
            price_str = f'{price:,.4f}' if isinstance(price, (int, float)) else str(price)
            value_str = f'{value:,.2f}' if isinstance(value, (int, float)) else str(value)

            rows.append(
                f'<tr>'
                f'<td>{date}</td>'
                f'<td>{symbol}</td>'
                f'<td class="{side_class}">{side.upper()}</td>'
                f'<td>{shares_str}</td>'
                f'<td>{price_str}</td>'
                f'<td>{value_str}</td>'
                f'</tr>'
            )

        total_note = ''
        if len(self._trades) > self.config.max_trades_display:
            total_note = (
                f'<p style="text-align:center;opacity:0.6;margin-top:8px;">'
                f'Showing {self.config.max_trades_display} of {len(self._trades)} trades'
                f'</p>'
            )

        return f"""
        <div class="card">
            <h2>Trade List ({len(self._trades)} trades)</h2>
            <table>
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Shares</th>
                        <th>Price</th>
                        <th>Value</th>
                    </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
            {total_note}
        </div>"""

    def _render_individual_table(self) -> str:
        """渲染单个标的绩效表格

        Returns:
            HTML 表格字符串
        """
        if not hasattr(self.result, 'individual_results') or not self.result.individual_results:
            return ''

        rows = []
        for r in self.result.individual_results:
            ret_class = 'positive' if r.total_return > 0 else 'negative' if r.total_return < 0 else ''
            rows.append(
                f'<tr>'
                f'<td>{r.symbol}</td>'
                f'<td class="{ret_class}">{r.total_return:.2%}</td>'
                f'<td>{r.annual_return:.2%}</td>'
                f'<td>{r.volatility:.2%}</td>'
                f'<td>{r.sharpe_ratio:.2f}</td>'
                f'<td>{r.max_drawdown:.2%}</td>'
                f'</tr>'
            )

        return f"""
        <div class="card">
            <h2>Individual Symbol Performance</h2>
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Total Return</th>
                        <th>Annual Return</th>
                        <th>Volatility</th>
                        <th>Sharpe Ratio</th>
                        <th>Max Drawdown</th>
                    </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>"""

    def _render_correlation_table(self) -> str:
        """渲染相关性矩阵表格

        Returns:
            HTML 表格字符串
        """
        corr = self.result.correlation_matrix
        if corr is None or corr.empty:
            return ''

        symbols = corr.columns.tolist()
        header_cells = '<th></th>' + ''.join(f'<th>{s}</th>' for s in symbols)
        rows = [f'<tr>{header_cells}</tr>']

        for symbol in symbols:
            cells = f'<td><strong>{symbol}</strong></td>'
            for other in symbols:
                val = corr.loc[symbol, other]
                if np.isnan(val):
                    cells += '<td>-</td>'
                else:
                    # 根据相关性值着色
                    abs_val = abs(val)
                    if abs_val >= 0.7:
                        css_class = 'negative'
                    elif abs_val >= 0.4:
                        css_class = ''
                    else:
                        css_class = 'positive'
                    cells += f'<td class="{css_class}">{val:.2f}</td>'
            rows.append(f'<tr>{cells}</tr>')

        return f"""
        <div class="card">
            <h2>Correlation Matrix</h2>
            <table>
                <thead>{rows[0]}</thead>
                <tbody>{"".join(rows[1:])}</tbody>
            </table>
        </div>"""
