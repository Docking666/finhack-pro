"""
另类数据工具集 - Alternative Data Tools

提供免费可获取的另类数据源接入，包括：
- 舆情数据（东方财富股吧、雪球热股）
- 龙虎榜数据
- 交易所公告
- 行业热度指数
- 供应链事件

适合普通投资者挖掘机构忽视的微观信号。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from finhack_pro.agents.tool_registry import (
    BaseTool,
    ToolCategory,
    ToolDefinition,
    ToolParameter,
)
from finhack_pro.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# 龙虎榜数据工具
# ============================================================

class FetchDragonTigerTool(BaseTool):
    """获取龙虎榜数据
    
    使用akshare获取龙虎榜数据，分析游资和机构动向。
    """
    
    def __init__(self):
        super().__init__()
        
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_dragon_tiger",
            description="获取A股龙虎榜数据，分析游资和机构的买卖动向，适合挖掘主力资金意图",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("symbol", "string", "股票代码，如 600519"),
                ToolParameter("days", "integer", "查询最近N天的龙虎榜", required=False, default=30),
            ],
            examples=[
                "fetch_dragon_tiger(symbol='600519', days=30)",
            ],
        )
    
    async def execute(self, **kwargs) -> Any:
        symbol = kwargs.get("symbol", "")
        days = kwargs.get("days", 30)
        
        try:
            import akshare as ak
            
            # 获取龙虎榜数据
            df = ak.stock_lhb_detail_em(
                start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
                end_date=datetime.now().strftime("%Y%m%d"),
            )
            
            if df is None or df.empty:
                return {"symbol": symbol, "records": [], "message": "未找到龙虎榜数据"}
            
            # 筛选指定股票
            if symbol:
                df = df[df["代码"].str.contains(symbol.replace(".SH", "").replace(".SZ", ""))]
            
            records = []
            for _, row in df.head(20).iterrows():
                record = {
                    "date": str(row.get("日期", "")),
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "close_price": float(row.get("收盘价", 0)),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "turnover_rate": float(row.get("换手率", 0)),
                    "net_buy": float(row.get("龙虎榜净买额", 0)),
                    "total_buy": float(row.get("龙虎榜买入额", 0)),
                    "total_sell": float(row.get("龙虎榜卖出额", 0)),
                    "reason": str(row.get("上榜原因", "")),
                }
                records.append(record)
            
            return {
                "symbol": symbol,
                "records": records,
                "total_count": len(records),
                "message": f"找到{len(records)}条龙虎榜记录",
            }
            
        except ImportError:
            return {"symbol": symbol, "records": [], "error": "akshare未安装"}
        except Exception as e:
            return {"symbol": symbol, "records": [], "error": str(e)}


class FetchExchangeNoticesTool(BaseTool):
    """获取交易所公告
    
    获取上交所、深交所公告，包括停复牌、风险提示、业绩预告等。
    """
    
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_exchange_notices",
            description="获取交易所公告数据，包括停复牌、风险提示、业绩预告、重大事项等",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("symbol", "string", "股票代码"),
                ToolParameter("days", "integer", "查询最近N天的公告", required=False, default=7),
                ToolParameter("notice_type", "string", "公告类型: all/suspend/risk/earnings/major", 
                             required=False, default="all"),
            ],
            examples=[
                "fetch_exchange_notices(symbol='600519', days=7, notice_type='all')",
            ],
        )
    
    async def execute(self, **kwargs) -> Any:
        symbol = kwargs.get("symbol", "")
        days = kwargs.get("days", 7)
        notice_type = kwargs.get("notice_type", "all")
        
        try:
            import akshare as ak
            
            notices = []
            
            # 获取公告数据
            # 注意：akshare的公告接口可能需要调整
            try:
                # 尝试获取个股公告
                df = ak.stock_notice_report(symbol=symbol.replace(".SH", "").replace(".SZ", ""))
                if df is not None and not df.empty:
                    for _, row in df.head(20).iterrows():
                        notice = {
                            "id": hashlib.md5(str(row.get("标题", "")).encode()).hexdigest()[:8],
                            "title": str(row.get("标题", "")),
                            "type": str(row.get("类型", "公告")),
                            "publish_time": str(row.get("发布时间", "")),
                            "content": str(row.get("内容", ""))[:500],
                            "source": "exchange",
                        }
                        notices.append(notice)
            except Exception:
                # 如果个股接口失败，返回模拟数据结构
                notices = [
                    {
                        "id": "notice_001",
                        "title": f"{symbol} 公告数据接口待配置",
                        "type": "system",
                        "publish_time": datetime.now().isoformat(),
                        "content": "请配置tushare token或akshare以获取完整公告数据",
                        "source": "system",
                    }
                ]
            
            return {
                "symbol": symbol,
                "notices": notices,
                "total_count": len(notices),
                "days": days,
                "notice_type": notice_type,
            }
            
        except ImportError:
            return {"symbol": symbol, "notices": [], "error": "akshare未安装"}
        except Exception as e:
            return {"symbol": symbol, "notices": [], "error": str(e)}


class FetchSentimentDataTool(BaseTool):
    """获取舆情数据
    
    获取股吧、雪球等社交媒体的舆情数据，分析市场情绪。
    """
    
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_sentiment_data",
            description="获取股票相关舆情数据，包括股吧热度、讨论量、情感倾向等",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("symbol", "string", "股票代码"),
                ToolParameter("days", "integer", "查询最近N天的舆情", required=False, default=7),
            ],
            examples=[
                "fetch_sentiment_data(symbol='600519', days=7)",
            ],
        )
    
    async def execute(self, **kwargs) -> Any:
        symbol = kwargs.get("symbol", "")
        days = kwargs.get("days", 7)
        
        try:
            import akshare as ak
            
            sentiment_data = {
                "symbol": symbol,
                "hot_rank": None,
                "discussion_count": 0,
                "sentiment_score": 0.5,
                "spike_detected": False,
                "trend": "stable",
                "summary": "",
                "posts": [],
            }
            
            # 尝试获取股吧热度
            try:
                # 东方财富股吧热度
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    code = symbol.replace(".SH", "").replace(".SZ", "")
                    row = df[df["代码"] == code]
                    if not row.empty:
                        # 获取相关数据
                        sentiment_data["hot_rank"] = int(row.iloc[0].get("排名", 0))
                        sentiment_data["summary"] = f"当前热度排名: {sentiment_data['hot_rank']}"
            except Exception:
                pass
            
            # 检测舆情爆发
            if sentiment_data["discussion_count"] > 1000:
                sentiment_data["spike_detected"] = True
                sentiment_data["trend"] = "rising"
            
            return sentiment_data
            
        except ImportError:
            return {"symbol": symbol, "error": "akshare未安装", "spike_detected": False}
        except Exception as e:
            return {"symbol": symbol, "error": str(e), "spike_detected": False}


class FetchIndustryHotTool(BaseTool):
    """获取行业热度数据
    
    获取行业板块涨跌、资金流向、热度排名等数据。
    """
    
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_industry_hot",
            description="获取行业板块热度数据，包括涨跌幅、资金流向、热度排名等",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("industry", "string", "行业名称(可选)，如 '白酒', '新能源'", required=False),
                ToolParameter("top_n", "integer", "返回前N个热门行业", required=False, default=10),
            ],
            examples=[
                "fetch_industry_hot(top_n=10)",
                "fetch_industry_hot(industry='白酒')",
            ],
        )
    
    async def execute(self, **kwargs) -> Any:
        industry = kwargs.get("industry", "")
        top_n = kwargs.get("top_n", 10)
        
        try:
            import akshare as ak
            
            # 获取行业板块数据
            df = ak.stock_board_industry_name_em()
            
            if df is None or df.empty:
                return {"industries": [], "error": "未获取到行业数据"}
            
            industries = []
            for _, row in df.head(top_n).iterrows():
                ind_data = {
                    "name": str(row.get("板块名称", "")),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "total_amount": float(row.get("总市值", 0)),
                    "net_inflow": float(row.get("主力净流入", 0)),
                    "leading_stock": str(row.get("领涨股票", "")),
                    "leading_change": float(row.get("领涨股票涨跌幅", 0)),
                }
                industries.append(ind_data)
                
                # 如果指定了行业名称，筛选匹配
                if industry and industry in ind_data["name"]:
                    industries = [ind_data]
                    break
            
            return {
                "industries": industries,
                "total_count": len(industries),
                "query_time": datetime.now().isoformat(),
            }
            
        except ImportError:
            return {"industries": [], "error": "akshare未安装"}
        except Exception as e:
            return {"industries": [], "error": str(e)}


class FetchBlockTradeTool(BaseTool):
    """获取大宗交易数据
    
    获取大宗交易明细，分析机构和大股东的交易动向。
    """
    
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_block_trade",
            description="获取大宗交易数据，分析机构和大股东的交易动向",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("symbol", "string", "股票代码(可选)", required=False),
                ToolParameter("days", "integer", "查询最近N天的大宗交易", required=False, default=30),
            ],
            examples=[
                "fetch_block_trade(days=30)",
                "fetch_block_trade(symbol='600519', days=30)",
            ],
        )
    
    async def execute(self, **kwargs) -> Any:
        symbol = kwargs.get("symbol", "")
        days = kwargs.get("days", 30)
        
        try:
            import akshare as ak
            
            # 获取大宗交易数据
            df = ak.stock_dzjy_mrmx(symbol=symbol.replace(".SH", "").replace(".SZ", "") if symbol else "")
            
            if df is None or df.empty:
                return {"trades": [], "message": "未找到大宗交易数据"}
            
            trades = []
            for _, row in df.head(30).iterrows():
                trade = {
                    "date": str(row.get("交易日期", "")),
                    "code": str(row.get("证券代码", "")),
                    "name": str(row.get("证券简称", "")),
                    "price": float(row.get("成交价", 0)),
                    "volume": float(row.get("成交量", 0)),
                    "amount": float(row.get("成交金额", 0)),
                    "buyer": str(row.get("买方营业部", "")),
                    "seller": str(row.get("卖方营业部", "")),
                    "premium_rate": float(row.get("溢价率", 0)) if "溢价率" in row else 0,
                }
                trades.append(trade)
            
            return {
                "symbol": symbol,
                "trades": trades,
                "total_count": len(trades),
                "days": days,
            }
            
        except ImportError:
            return {"trades": [], "error": "akshare未安装"}
        except Exception as e:
            return {"trades": [], "error": str(e)}


class FetchNorthFlowTool(BaseTool):
    """获取北向资金数据
    
    获取北向资金（沪股通+深股通）的流入流出数据。
    """
    
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_north_flow",
            description="获取北向资金流入流出数据，分析外资动向",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("days", "integer", "查询最近N天的北向资金", required=False, default=30),
            ],
            examples=[
                "fetch_north_flow(days=30)",
            ],
        )
    
    async def execute(self, **kwargs) -> Any:
        days = kwargs.get("days", 30)
        
        try:
            import akshare as ak
            
            # 获取北向资金数据
            df = ak.stock_hsgt_north_net_flow_in_em()
            
            if df is None or df.empty:
                return {"flows": [], "message": "未获取到北向资金数据"}
            
            flows = []
            for _, row in df.head(days).iterrows():
                flow = {
                    "date": str(row.get("日期", "")),
                    "net_flow": float(row.get("当日净流入", 0)),
                    "sh_flow": float(row.get("沪股通", 0)) if "沪股通" in row else 0,
                    "sz_flow": float(row.get("深股通", 0)) if "深股通" in row else 0,
                    "total_flow": float(row.get("当日资金流入", 0)) if "当日资金流入" in row else 0,
                }
                flows.append(flow)
            
            # 计算趋势
            if len(flows) >= 5:
                recent_5d_sum = sum(f["net_flow"] for f in flows[:5])
                trend = "inflow" if recent_5d_sum > 0 else "outflow"
            else:
                trend = "unknown"
            
            return {
                "flows": flows,
                "total_count": len(flows),
                "trend": trend,
                "days": days,
            }
            
        except ImportError:
            return {"flows": [], "error": "akshare未安装"}
        except Exception as e:
            return {"flows": [], "error": str(e)}


class FetchMarginTradingTool(BaseTool):
    """获取融资融券数据
    
    获取融资融券余额变化，分析杠杆资金动向。
    """
    
    def define(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_margin_trading",
            description="获取融资融券数据，分析杠杆资金动向",
            category=ToolCategory.NEWS_SENTIMENT,
            parameters=[
                ToolParameter("symbol", "string", "股票代码(可选)", required=False),
                ToolParameter("days", "integer", "查询最近N天的数据", required=False, default=30),
            ],
            examples=[
                "fetch_margin_trading(days=30)",
            ],
        )
    
    async def execute(self, **kwargs) -> Any:
        symbol = kwargs.get("symbol", "")
        days = kwargs.get("days", 30)
        
        try:
            import akshare as ak
            
            # 获取融资融券汇总数据
            df = ak.stock_margin_underlying_info_sz_sh(date=datetime.now().strftime("%Y%m%d"))
            
            if df is None or df.empty:
                return {"margin_data": [], "message": "未获取到融资融券数据"}
            
            margin_data = []
            for _, row in df.head(50).iterrows():
                data = {
                    "code": str(row.get("证券代码", "")),
                    "name": str(row.get("证券简称", "")),
                    "margin_balance": float(row.get("融资余额", 0)) if "融资余额" in row else 0,
                    "short_balance": float(row.get("融券余额", 0)) if "融券余额" in row else 0,
                    "total_balance": float(row.get("融资融券余额", 0)) if "融资融券余额" in row else 0,
                }
                margin_data.append(data)
            
            return {
                "symbol": symbol,
                "margin_data": margin_data,
                "total_count": len(margin_data),
                "days": days,
            }
            
        except ImportError:
            return {"margin_data": [], "error": "akshare未安装"}
        except Exception as e:
            return {"margin_data": [], "error": str(e)}


# ============================================================
# 工具注册函数
# ============================================================

def register_alternative_data_tools(registry) -> None:
    """注册所有另类数据工具到工具注册中心
    
    Args:
        registry: ToolRegistry实例
    """
    tools = [
        FetchDragonTigerTool(),
        FetchExchangeNoticesTool(),
        FetchSentimentDataTool(),
        FetchIndustryHotTool(),
        FetchBlockTradeTool(),
        FetchNorthFlowTool(),
        FetchMarginTradingTool(),
    ]
    
    for tool in tools:
        registry.register(tool)
        
    logger.info(f"已注册 {len(tools)} 个另类数据工具")


def create_alternative_data_toolkit() -> List[BaseTool]:
    """创建另类数据工具集列表
    
    Returns:
        工具实例列表
    """
    return [
        FetchDragonTigerTool(),
        FetchExchangeNoticesTool(),
        FetchSentimentDataTool(),
        FetchIndustryHotTool(),
        FetchBlockTradeTool(),
        FetchNorthFlowTool(),
        FetchMarginTradingTool(),
    ]
