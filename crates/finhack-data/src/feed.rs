//! 数据Feed - 回测数据供给
//!
//! 提供按时间顺序供给Bar数据的接口，用于回测引擎驱动

use finhack_core::types::Bar;
use std::collections::HashMap;
use tracing::debug;

/// 数据Feed事件
#[derive(Debug, Clone)]
pub enum FeedEvent {
    /// 新的Bar数据
    NewBar(Bar),
    /// 交易日开始
    TradingDayStart(String), // 日期字符串
    /// 交易日结束
    TradingDayEnd(String),
    /// 数据结束
    EndOfData,
}

/// 数据Feed
///
/// 管理多个标的的行情数据，按时间顺序提供数据
pub struct DataFeed {
    /// 所有标的的Bar数据: symbol -> Vec<Bar>
    data: HashMap<String, Vec<Bar>>,
    /// 当前时间索引
    current_index: usize,
    /// 统一时间线
    timeline: Vec<FeedEvent>,
}

impl DataFeed {
    /// 创建新的数据Feed
    pub fn new() -> Self {
        Self {
            data: HashMap::new(),
            current_index: 0,
            timeline: Vec::new(),
        }
    }

    /// 添加标的的行情数据
    pub fn add_data(&mut self, symbol: impl Into<String>, bars: Vec<Bar>) {
        let symbol = symbol.into();
        debug!(symbol = %symbol, bars = bars.len(), "数据Feed: 添加行情数据");
        self.data.insert(symbol, bars);
    }

    /// 构建时间线
    ///
    /// 将所有标的的Bar按时间排序，生成统一的事件序列
    pub fn build_timeline(&mut self) {
        // 收集所有Bar并按时间排序
        let mut all_bars: Vec<&Bar> = Vec::new();
        for bars in self.data.values() {
            for bar in bars {
                all_bars.push(bar);
            }
        }
        all_bars.sort_by_key(|b| b.timestamp);

        // 生成事件序列
        self.timeline.clear();
        let mut last_date: String = String::new();

        for bar in all_bars {
            let current_date = bar.timestamp.format("%Y-%m-%d").to_string();

            // 如果日期变化，插入交易日事件
            if current_date != last_date {
                if !last_date.is_empty() {
                    self.timeline.push(FeedEvent::TradingDayEnd(last_date));
                }
                self.timeline
                    .push(FeedEvent::TradingDayStart(current_date.clone()));
                last_date = current_date;
            }

            self.timeline.push(FeedEvent::NewBar(bar.clone()));
        }

        if !last_date.is_empty() {
            self.timeline.push(FeedEvent::TradingDayEnd(last_date));
        }
        self.timeline.push(FeedEvent::EndOfData);

        debug!(events = self.timeline.len(), "数据Feed: 时间线已构建");
    }

    /// 获取下一个事件
    pub fn next(&mut self) -> Option<FeedEvent> {
        if self.current_index < self.timeline.len() {
            let event = self.timeline[self.current_index].clone();
            self.current_index += 1;
            Some(event)
        } else {
            None
        }
    }

    /// 是否还有更多事件
    pub fn has_more(&self) -> bool {
        self.current_index < self.timeline.len()
    }

    /// 重置Feed到起始位置
    pub fn reset(&mut self) {
        self.current_index = 0;
    }

    /// 获取总事件数
    pub fn event_count(&self) -> usize {
        self.timeline.len()
    }

    /// 获取已处理事件数
    pub fn processed_count(&self) -> usize {
        self.current_index
    }

    /// 获取所有标的列表
    pub fn symbols(&self) -> Vec<&str> {
        self.data.keys().map(|s| s.as_str()).collect()
    }

    /// 获取指定标的的Bar数量
    pub fn bar_count(&self, symbol: &str) -> usize {
        self.data.get(symbol).map(|b| b.len()).unwrap_or(0)
    }
}

impl Default for DataFeed {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration, Utc};

    fn create_test_bars(symbol: &str, count: usize) -> Vec<Bar> {
        use rust_decimal::Decimal;
        (0..count)
            .map(|i| {
                Bar::new(
                    symbol,
                    Utc::now() - Duration::days((count - i) as i64),
                    Decimal::from(10 + i as i32),
                    Decimal::from(11 + i as i32),
                    Decimal::from(9 + i as i32),
                    Decimal::from(10 + i as i32),
                    1000000,
                    Decimal::from(10000000 + i as i64 * 100000),
                )
            })
            .collect()
    }

    #[test]
    fn test_data_feed() {
        let mut feed = DataFeed::new();
        feed.add_data("000001.SZ", create_test_bars("000001.SZ", 5));
        feed.add_data("600519.SH", create_test_bars("600519.SH", 3));
        feed.build_timeline();

        assert!(feed.has_more());
        let mut bar_count = 0;
        while let Some(event) = feed.next() {
            if let FeedEvent::NewBar(_) = event {
                bar_count += 1;
            }
        }
        assert_eq!(bar_count, 8); // 5 + 3
        assert!(!feed.has_more());
    }

    #[test]
    fn test_reset() {
        let mut feed = DataFeed::new();
        feed.add_data("000001.SZ", create_test_bars("000001.SZ", 3));
        feed.build_timeline();

        let _ = feed.next();
        let _ = feed.next();
        assert_eq!(feed.processed_count(), 2);

        feed.reset();
        assert_eq!(feed.processed_count(), 0);
        assert!(feed.has_more());
    }
}
