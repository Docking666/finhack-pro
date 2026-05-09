"""
PDF导出器

使用reportlab库生成专业的回测报告PDF。
包含封面、摘要、权益曲线图、交易记录表、风险指标等。
支持中文字体。
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line
from reportlab.graphics.widgets.markers import makeMarker

# reportlab imports
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class PDFExporter:
    """PDF导出器类
    
    用于将回测结果导出为专业的PDF报告。
    """
    
    # FinHack Pro 品牌色
    BRAND_COLOR = colors.HexColor("#3B82F6")  # 蓝色
    BRAND_DARK = colors.HexColor("#1E293B")   # 深蓝灰
    SUCCESS_COLOR = colors.HexColor("#22C55E")  # 绿色
    DANGER_COLOR = colors.HexColor("#EF4444")   # 红色
    WARNING_COLOR = colors.HexColor("#F59E0B")  # 橙色
    
    def __init__(self):
        """初始化PDF导出器，注册中文字体"""
        self._register_chinese_fonts()
        self.styles = self._create_styles()
    
    def _register_chinese_fonts(self):
        """注册中文字体"""
        # 尝试注册常见的中文字体
        font_paths = [
            # Linux常见字体路径
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            # macOS字体路径
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            # Windows字体路径
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ]
        
        self.chinese_font = "Helvetica"  # 默认字体
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    font_name = "ChineseFont"
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    self.chinese_font = font_name
                    logger.info(f"成功注册中文字体: {font_path}")
                    break
                except Exception as e:
                    logger.warning(f"注册字体失败 {font_path}: {e}")
        
        if self.chinese_font == "Helvetica":
            logger.warning("未找到中文字体，PDF可能无法正确显示中文")
    
    def _create_styles(self) -> Dict[str, ParagraphStyle]:
        """创建自定义样式"""
        styles = getSampleStyleSheet()
        
        # 标题样式
        styles.add(ParagraphStyle(
            name="ChineseTitle",
            fontName=self.chinese_font,
            fontSize=24,
            leading=30,
            alignment=TA_CENTER,
            textColor=self.BRAND_DARK,
            spaceAfter=20,
        ))
        
        # 副标题样式
        styles.add(ParagraphStyle(
            name="ChineseSubtitle",
            fontName=self.chinese_font,
            fontSize=14,
            leading=18,
            alignment=TA_CENTER,
            textColor=colors.gray,
            spaceAfter=10,
        ))
        
        # 章节标题样式
        styles.add(ParagraphStyle(
            name="ChineseHeading",
            fontName=self.chinese_font,
            fontSize=16,
            leading=20,
            alignment=TA_LEFT,
            textColor=self.BRAND_COLOR,
            spaceBefore=15,
            spaceAfter=10,
        ))
        
        # 正文样式
        styles.add(ParagraphStyle(
            name="ChineseBody",
            fontName=self.chinese_font,
            fontSize=10,
            leading=14,
            alignment=TA_LEFT,
            textColor=colors.black,
            spaceAfter=6,
        ))
        
        # 表格标题样式
        styles.add(ParagraphStyle(
            name="ChineseTableHeader",
            fontName=self.chinese_font,
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=colors.white,
        ))
        
        # 数值样式
        styles.add(ParagraphStyle(
            name="ChineseNumber",
            fontName=self.chinese_font,
            fontSize=10,
            leading=14,
            alignment=TA_RIGHT,
        ))
        
        return styles
    
    def export_backtest_report(
        self,
        result: Dict[str, Any],
        output_path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """导出回测报告为PDF
        
        Args:
            result: 回测结果数据
            output_path: 输出文件路径
            params: 回测参数（可选）
        
        Returns:
            生成的PDF文件路径
        """
        # 创建PDF文档
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm,
        )
        
        # 构建文档内容
        story = []
        
        # 1. 封面
        story.extend(self._create_cover(result, params))
        story.append(PageBreak())
        
        # 2. 摘要页
        story.extend(self._create_summary(result, params))
        story.append(Spacer(1, 20))
        
        # 3. 关键指标
        story.extend(self._create_metrics_section(result))
        story.append(Spacer(1, 20))
        
        # 4. 权益曲线图
        if result.get("equity_curve"):
            story.extend(self._create_equity_curve_section(result))
            story.append(Spacer(1, 20))
        
        # 5. 交易记录表
        if result.get("trades"):
            story.extend(self._create_trades_section(result))
        
        # 6. 页脚
        story.extend(self._create_footer())
        
        # 生成PDF
        doc.build(story)
        logger.info(f"PDF报告已生成: {output_path}")
        
        return output_path
    
    def _create_cover(
        self,
        result: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
    ) -> List:
        """创建封面"""
        elements = []
        
        # 顶部间距
        elements.append(Spacer(1, 80))
        
        # 主标题
        elements.append(Paragraph(
            "FinHack Pro",
            self.styles["ChineseTitle"]
        ))
        
        elements.append(Paragraph(
            "回测报告",
            ParagraphStyle(
                name="CoverSubtitle",
                fontName=self.chinese_font,
                fontSize=18,
                leading=24,
                alignment=TA_CENTER,
                textColor=self.BRAND_COLOR,
            )
        ))
        
        elements.append(Spacer(1, 40))
        
        # 分隔线
        line = Drawing(400, 2)
        line.add(Line(0, 1, 400, 1, strokeColor=self.BRAND_COLOR, strokeWidth=2))
        elements.append(line)
        
        elements.append(Spacer(1, 40))
        
        # 基本信息
        params = params or {}
        info_data = [
            ["策略名称", params.get("strategy", "Dual Thrust")],
            ["标的代码", params.get("symbols", "N/A")],
            ["回测区间", f"{params.get('start_date', 'N/A')} ~ {params.get('end_date', 'N/A')}"],
            ["初始资金", f"¥{params.get('initial_capital', 1000000):,.0f}"],
            ["报告生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ]
        
        info_table = Table(info_data, colWidths=[120, 280])
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), self.chinese_font),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.gray),
            ("TEXTCOLOR", (1, 0), (1, -1), self.BRAND_DARK),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (1, 0), (1, -1), "LEFT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
        ]))
        elements.append(info_table)
        
        return elements
    
    def _create_summary(
        self,
        result: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
    ) -> List:
        """创建摘要部分"""
        elements = []
        
        elements.append(Paragraph(
            "📊 执行摘要",
            self.styles["ChineseHeading"]
        ))
        
        metrics = result.get("metrics", {})
        
        # 核心指标卡片
        total_return = metrics.get("total_return", 0)
        annual_return = metrics.get("annual_return", 0)
        sharpe = metrics.get("sharpe_ratio", 0)
        max_dd = metrics.get("max_drawdown", 0)
        win_rate = metrics.get("win_rate", 0)
        
        summary_text = f"""
        本次回测使用 {params.get('strategy', 'Dual Thrust')} 策略，
        对标的 {params.get('symbols', 'N/A')} 在 {params.get('start_date', '')} 至 {params.get('end_date', '')} 期间进行回测。
        
        回测期间共执行 {metrics.get('total_trades', 0)} 笔交易，
        最终实现总收益率 {total_return:.2f}%，
        年化收益率 {annual_return:.2f}%。
        
        风险指标方面，夏普比率为 {sharpe:.2f}，
        最大回撤为 {max_dd:.2f}%，
        胜率达到 {win_rate:.2f}%。
        """
        
        elements.append(Paragraph(
            summary_text.replace("\n", "<br/>").strip(),
            self.styles["ChineseBody"]
        ))
        
        return elements
    
    def _create_metrics_section(self, result: Dict[str, Any]) -> List:
        """创建指标部分"""
        elements = []
        
        elements.append(Paragraph(
            "📈 关键指标",
            self.styles["ChineseHeading"]
        ))
        
        metrics = result.get("metrics", {})
        
        # 指标表格数据
        metrics_data = [
            ["指标名称", "数值", "说明"],
            ["总收益率", f"{metrics.get('total_return', 0):.2f}%", "整个回测期间的累计收益"],
            ["年化收益率", f"{metrics.get('annual_return', 0):.2f}%", "折算为年度的收益率"],
            ["夏普比率", f"{metrics.get('sharpe_ratio', 0):.2f}", "风险调整后收益指标"],
            ["Sortino比率", f"{metrics.get('sortino_ratio', 0):.2f}", "下行风险调整后收益"],
            ["最大回撤", f"{metrics.get('max_drawdown', 0):.2f}%", "最大峰值到谷值的跌幅"],
            ["胜率", f"{metrics.get('win_rate', 0):.2f}%", "盈利交易占比"],
            ["盈亏比", f"{metrics.get('profit_loss_ratio', 0):.2f}", "平均盈利/平均亏损"],
            ["交易次数", f"{metrics.get('total_trades', 0)}", "总交易次数"],
            ["最终权益", f"¥{metrics.get('final_equity', 0):,.2f}", "期末账户权益"],
        ]
        
        # 创建表格
        table = Table(metrics_data, colWidths=[100, 100, 200])
        table.setStyle(TableStyle([
            # 表头样式
            ("FONTNAME", (0, 0), (-1, 0), self.chinese_font),
            ("FONTSIZE", (0, 0), (-1, 0), 10),
            ("BACKGROUND", (0, 0), (-1, 0), self.BRAND_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            
            # 数据行样式
            ("FONTNAME", (0, 1), (-1, -1), self.chinese_font),
            ("FONTSIZE", (0, 1), (-1, -1), 9),
            ("ALIGN", (0, 1), (0, -1), "LEFT"),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
            ("ALIGN", (2, 1), (2, -1), "LEFT"),
            
            # 颜色
            ("TEXTCOLOR", (1, 1), (1, 1), self.SUCCESS_COLOR if metrics.get("total_return", 0) >= 0 else self.DANGER_COLOR),
            ("TEXTCOLOR", (1, 2), (1, 2), self.SUCCESS_COLOR if metrics.get("annual_return", 0) >= 0 else self.DANGER_COLOR),
            ("TEXTCOLOR", (1, 5), (1, 5), self.DANGER_COLOR),
            
            # 边框
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, self.BRAND_COLOR),
            
            # 内边距
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            
            # 交替行背景
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F8FAFC")),
            ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#F8FAFC")),
            ("BACKGROUND", (0, 5), (-1, 5), colors.HexColor("#F8FAFC")),
            ("BACKGROUND", (0, 7), (-1, 7), colors.HexColor("#F8FAFC")),
            ("BACKGROUND", (0, 9), (-1, 9), colors.HexColor("#F8FAFC")),
        ]))
        
        elements.append(table)
        
        return elements
    
    def _create_equity_curve_section(self, result: Dict[str, Any]) -> List:
        """创建权益曲线部分"""
        elements = []
        
        elements.append(Paragraph(
            "📉 权益曲线",
            self.styles["ChineseHeading"]
        ))
        
        equity_curve = result.get("equity_curve", [])
        
        if not equity_curve:
            elements.append(Paragraph(
                "暂无权益曲线数据",
                self.styles["ChineseBody"]
            ))
            return elements
        
        # 创建权益曲线图
        drawing = Drawing(450, 200)
        
        # 创建折线图
        plot = LinePlot()
        plot.x = 50
        plot.y = 30
        plot.width = 380
        plot.height = 150
        
        # 准备数据
        data = []
        for i, point in enumerate(equity_curve):
            equity = point.get("equity", point.get("value", 0))
            data.append((i, equity))
        
        if data:
            plot.data = [data]
            plot.lines[0].strokeColor = self.BRAND_COLOR
            plot.lines[0].strokeWidth = 2
            
            # 设置坐标轴
            plot.xValueAxis.valueMin = 0
            plot.xValueAxis.valueMax = len(data)
            plot.xValueAxis.labelTextFormat = lambda x: ""
            
            equities = [d[1] for d in data]
            plot.yValueAxis.valueMin = min(equities) * 0.95
            plot.yValueAxis.valueMax = max(equities) * 1.05
        
        drawing.add(plot)
        
        # 添加图例
        legend = Legend()
        legend.x = 180
        legend.y = 10
        legend.dx = 8
        legend.dy = 8
        legend.fontName = self.chinese_font
        legend.fontSize = 8
        legend.boxAnchor = 'nw'
        legend.columnMaximum = 1
        legend.strokeWidth = 0.5
        legend.strokeColor = colors.black
        legend.deltax = 75
        legend.deltay = 10
        legend.autoXPadding = 5
        legend.colorNamePairs = [(self.BRAND_COLOR, "策略权益")]
        drawing.add(legend)
        
        elements.append(drawing)
        
        # 添加数据表格（简化版）
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(
            "权益曲线数据（最近10个交易日）",
            ParagraphStyle(
                name="TableTitle",
                fontName=self.chinese_font,
                fontSize=9,
                textColor=colors.gray,
            )
        ))
        
        # 显示最近10个数据点
        recent_data = equity_curve[-10:] if len(equity_curve) > 10 else equity_curve
        curve_table_data = [["日期", "权益"]]
        for point in recent_data:
            curve_table_data.append([
                point.get("date", "N/A"),
                f"¥{point.get('equity', point.get('value', 0)):,.2f}"
            ])
        
        curve_table = Table(curve_table_data, colWidths=[100, 150])
        curve_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), self.chinese_font),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), self.BRAND_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(curve_table)
        
        return elements
    
    def _create_trades_section(self, result: Dict[str, Any]) -> List:
        """创建交易记录部分"""
        elements = []
        
        elements.append(Paragraph(
            "📋 交易记录",
            self.styles["ChineseHeading"]
        ))
        
        trades = result.get("trades", [])
        
        if not trades:
            elements.append(Paragraph(
                "暂无交易记录",
                self.styles["ChineseBody"]
            ))
            return elements
        
        # 交易记录表格（最多显示前50条）
        display_trades = trades[:50]
        
        trades_data = [["日期", "标的", "方向", "价格", "数量", "盈亏"]]
        
        for trade in display_trades:
            direction = trade.get("direction", "buy")
            direction_text = "买入" if direction == "buy" else "卖出"
            pnl = trade.get("pnl", 0)
            
            trades_data.append([
                trade.get("date", "N/A"),
                trade.get("symbol", "N/A"),
                direction_text,
                f"{trade.get('price', 0):.2f}",
                str(trade.get("volume", 0)),
                f"{pnl:+.2f}" if pnl else "-",
            ])
        
        trades_table = Table(trades_data, colWidths=[80, 80, 50, 70, 60, 80])
        trades_table.setStyle(TableStyle([
            # 表头
            ("FONTNAME", (0, 0), (-1, 0), self.chinese_font),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BACKGROUND", (0, 0), (-1, 0), self.BRAND_DARK),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            
            # 数据行
            ("FONTNAME", (0, 1), (-1, -1), self.chinese_font),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("ALIGN", (0, 1), (1, -1), "LEFT"),
            ("ALIGN", (2, 1), (2, -1), "CENTER"),
            ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
            
            # 边框
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("LINEBELOW", (0, 0), (-1, 0), 1, self.BRAND_DARK),
            
            # 内边距
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        
        # 为买卖方向添加颜色
        for i, trade in enumerate(display_trades, start=1):
            direction = trade.get("direction", "buy")
            if direction == "buy":
                trades_table.setStyle(TableStyle([
                    ("TEXTCOLOR", (2, i), (2, i), self.SUCCESS_COLOR),
                ]))
            else:
                trades_table.setStyle(TableStyle([
                    ("TEXTCOLOR", (2, i), (2, i), self.DANGER_COLOR),
                ]))
            
            # 盈亏颜色
            pnl = trade.get("pnl", 0)
            if pnl > 0:
                trades_table.setStyle(TableStyle([
                    ("TEXTCOLOR", (5, i), (5, i), self.SUCCESS_COLOR),
                ]))
            elif pnl < 0:
                trades_table.setStyle(TableStyle([
                    ("TEXTCOLOR", (5, i), (5, i), self.DANGER_COLOR),
                ]))
        
        elements.append(trades_table)
        
        if len(trades) > 50:
            elements.append(Spacer(1, 10))
            elements.append(Paragraph(
                f"注：仅显示前50条交易记录，共{len(trades)}条",
                ParagraphStyle(
                    name="Note",
                    fontName=self.chinese_font,
                    fontSize=8,
                    textColor=colors.gray,
                )
            ))
        
        return elements
    
    def _create_footer(self) -> List:
        """创建页脚"""
        elements = []
        
        elements.append(Spacer(1, 30))
        
        # 分隔线
        line = Drawing(400, 2)
        line.add(Line(0, 1, 400, 1, strokeColor=colors.lightgrey, strokeWidth=0.5))
        elements.append(line)
        
        elements.append(Spacer(1, 10))
        
        # 版权信息
        elements.append(Paragraph(
            f"© {datetime.now().year} FinHack Pro - 多智能体量化交易系统",
            ParagraphStyle(
                name="Footer",
                fontName=self.chinese_font,
                fontSize=8,
                alignment=TA_CENTER,
                textColor=colors.gray,
            )
        ))
        
        elements.append(Paragraph(
            "本报告仅供参考，不构成投资建议",
            ParagraphStyle(
                name="Disclaimer",
                fontName=self.chinese_font,
                fontSize=7,
                alignment=TA_CENTER,
                textColor=colors.lightgrey,
            )
        ))
        
        return elements
    
    def export_to_bytes(
        self,
        result: Dict[str, Any],
        params: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """导出回测报告为字节流
        
        Args:
            result: 回测结果数据
            params: 回测参数（可选）
        
        Returns:
            PDF文件的字节流
        """
        buffer = io.BytesIO()
        
        # 创建PDF文档
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2*cm,
            leftMargin=2*cm,
            topMargin=2*cm,
            bottomMargin=2*cm,
        )
        
        # 构建文档内容
        story = []
        story.extend(self._create_cover(result, params))
        story.append(PageBreak())
        story.extend(self._create_summary(result, params))
        story.append(Spacer(1, 20))
        story.extend(self._create_metrics_section(result))
        story.append(Spacer(1, 20))
        
        if result.get("equity_curve"):
            story.extend(self._create_equity_curve_section(result))
            story.append(Spacer(1, 20))
        
        if result.get("trades"):
            story.extend(self._create_trades_section(result))
        
        story.extend(self._create_footer())
        
        # 生成PDF
        doc.build(story)
        
        # 获取字节流
        buffer.seek(0)
        return buffer.getvalue()
