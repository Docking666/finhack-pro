"""另类数据工具（公告/舆情）测试：mock akshare 验证真实数据提取逻辑

覆盖 FetchExchangeNoticesTool（东财公告）与 FetchSentimentDataTool（股吧关注度），
验证代码过滤、字段提取、spike 检测、接口失败诚实返回。
"""

import sys
import types
from unittest.mock import patch

import pandas as pd
import pytest

from finhack_pro.agents.alternative_data_tools import (
    FetchExchangeNoticesTool,
    FetchSentimentDataTool,
)


def _install_fake_akshare(module_dict):
    """在 sys.modules 安装假 akshare，供工具内 `import akshare as ak` 使用"""
    fake = types.ModuleType("akshare")
    for name, obj in module_dict.items():
        setattr(fake, name, obj)
    fake.__version__ = "9.9.9"
    return patch.dict(sys.modules, {"akshare": fake})


class TestFetchExchangeNoticesTool:
    @pytest.mark.asyncio
    async def test_fetches_notices_by_code(self):
        """按代码查询公告，提取标题/时间/链接"""
        df = pd.DataFrame([
            {"代码": "000001", "简称": "平安银行",
             "公告标题": "平安银行:2026年半年度报告",
             "公告时间": "2026-08-26 18:00:00",
             "公告链接": "https://data.eastmoney.com/notices/detail/000001/y.html"},
            {"代码": "000001", "简称": "平安银行",
             "公告标题": "平安银行:关于召开2026年第一次临时股东大会的通知",
             "公告时间": "2026-08-27 08:30:00",
             "公告链接": "https://data.eastmoney.com/notices/detail/000001/x.html"},
        ])
        fake_ak = {"stock_zh_a_disclosure_report_cninfo": lambda **kw: df}
        with _install_fake_akshare(fake_ak):
            tool = FetchExchangeNoticesTool()
            result = await tool.execute(symbol="000001.SZ", days=7)

        assert result["total_count"] == 2
        notice = result["notices"][0]
        assert "半年度报告" in notice["title"]
        assert notice["source"] == "eastmoney_notice"
        assert notice["url"].startswith("https://")
        assert notice["id"]

    @pytest.mark.asyncio
    async def test_api_error_returns_empty_notices(self):
        """接口失败：诚实返回空公告（不伪造模拟数据）"""
        def boom(**kw):
            raise RuntimeError("网络失败")

        fake_ak = {"stock_zh_a_disclosure_report_cninfo": boom}
        with _install_fake_akshare(fake_ak):
            tool = FetchExchangeNoticesTool()
            result = await tool.execute(symbol="600519", days=7)

        assert result["notices"] == []
        assert result["total_count"] == 0


class TestFetchSentimentDataTool:
    @pytest.mark.asyncio
    async def test_populates_real_guba_metrics(self):
        """股吧关注度：关注指数/排名/排名变化真实提取，未爆发不误报 spike"""
        df = pd.DataFrame([
            {"序号": 1, "代码": "600519", "名称": "贵州茅台", "最新价": 1450.0, "涨跌幅": 0.5,
             "换手率": 0.3, "市盈率": 30.0, "主力成本": 1400.0, "机构参与度": 0.4,
             "综合得分": 70.0, "上升": 120, "目前排名": 1888, "关注指数": 65.5, "交易日": "2026-08-27"},
        ])
        fake_ak = {"stock_comment_em": lambda: df}
        with _install_fake_akshare(fake_ak):
            tool = FetchSentimentDataTool()
            result = await tool.execute(symbol="600519", days=7)

        assert result["discussion_count"] == 65
        assert result["hot_rank"] == 1888
        assert result["rank_change"] == 120
        assert result["spike_detected"] is False
        assert "关注指数=65" in result["summary"]

    @pytest.mark.asyncio
    async def test_no_spike_on_steady_high_attention(self):
        """白马股关注指数常年偏高但排名未升 → 不误报 spike（防假阳性）"""
        df = pd.DataFrame([
            {"序号": 1, "代码": "600519", "名称": "贵州茅台", "最新价": 1450.0, "涨跌幅": 0.5,
             "换手率": 0.3, "市盈率": 30.0, "主力成本": 1400.0, "机构参与度": 0.4,
             "综合得分": 70.0, "上升": -178, "目前排名": 217, "关注指数": 94.0, "交易日": "2026-08-27"},
        ])
        fake_ak = {"stock_comment_em": lambda: df}
        with _install_fake_akshare(fake_ak):
            tool = FetchSentimentDataTool()
            result = await tool.execute(symbol="600519", days=7)

        assert result["discussion_count"] == 94
        assert result["rank_change"] == -178
        assert result["spike_detected"] is False

    @pytest.mark.asyncio
    async def test_spike_detected_on_high_attention(self):
        """排名大幅上升（热度突增）→ 舆情爆发检测"""
        df = pd.DataFrame([
            {"序号": 1, "代码": "600487", "名称": "亨通光电", "最新价": 71.3, "涨跌幅": 9.98,
             "换手率": 12.0, "市盈率": 25.0, "主力成本": 60.0, "机构参与度": 0.5,
             "综合得分": 85.0, "上升": 1500, "目前排名": 1, "关注指数": 95.2, "交易日": "2026-08-27"},
        ])
        fake_ak = {"stock_comment_em": lambda: df}
        with _install_fake_akshare(fake_ak):
            tool = FetchSentimentDataTool()
            result = await tool.execute(symbol="600487", days=7)

        assert result["spike_detected"] is True
        assert result["trend"] == "rising"

    @pytest.mark.asyncio
    async def test_api_error_returns_honest_empty(self):
        """接口失败：spike 不误报、计数为 0"""
        def boom():
            raise RuntimeError("接口失败")

        fake_ak = {"stock_comment_em": boom}
        with _install_fake_akshare(fake_ak):
            tool = FetchSentimentDataTool()
            result = await tool.execute(symbol="600519", days=7)

        assert result["spike_detected"] is False
        assert result["discussion_count"] == 0
