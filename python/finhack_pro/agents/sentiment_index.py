"""全市场情绪指数（市场温度计）— Sentiment Index

聚合全市场股吧关注度（akshare stock_comment_em，5195 只 A 股），输出"市场温度"：
- overheated  过热：平均关注度高 + 普涨（情绪亢奋，警惕回调）
- panicky     恐慌：高关注 + 普跌 或 低关注 + 普跌（避险/机会并存）
- normal      正常：不干预

用于 P2② 情绪择时：作为 risk_manager 的输入（过热降仓/恐慌预警）。
数据源失败时诚实降级为 normal（不伪造信号）。
"""

from __future__ import annotations

from typing import Any, Dict

from loguru import logger


async def compute_sentiment_index() -> Dict[str, Any]:
    """计算全市场情绪温度（真实数据，无 mock）

    Returns:
        {temperature, mean_attention, attention_p90, advancers_ratio,
         total_stocks, source}；数据失败时 temperature=normal + error 字段
    """
    try:
        import akshare as ak

        df = ak.stock_comment_em()
        if df is None or df.empty:
            logger.warning("[SentimentIndex] 无市场关注度数据，降级为 normal")
            return {"temperature": "normal", "error": "empty_data"}

        attention = df["关注指数"].astype(float)
        mean_attention = float(attention.mean())
        attention_p90 = float(attention.quantile(0.9))
        advancers_ratio = float((df["涨跌幅"].astype(float) > 0).mean())

        # 温度判定（启发式阈值，基于关注指数百分制分布）
        high_attention = mean_attention >= 60
        if high_attention and advancers_ratio >= 0.7:
            temperature = "overheated"
        elif high_attention and advancers_ratio <= 0.3:
            temperature = "panicky"
        elif not high_attention and advancers_ratio <= 0.3:
            temperature = "panicky"
        else:
            temperature = "normal"

        index = {
            "temperature": temperature,
            "mean_attention": round(mean_attention, 1),
            "attention_p90": round(attention_p90, 1),
            "advancers_ratio": round(advancers_ratio, 3),
            "total_stocks": int(len(df)),
            "source": "stock_comment_em",
        }
        logger.info(
            f"[SentimentIndex] 市场温度={temperature}: "
            f"平均关注指数={index['mean_attention']}, 上涨占比={index['advancers_ratio']:.1%}"
        )
        return index

    except Exception as e:
        logger.warning(f"[SentimentIndex] 计算失败，降级为 normal: {e}")
        return {"temperature": "normal", "error": str(e), "source": "stock_comment_em"}
