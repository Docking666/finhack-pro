//! 数据加载器
//!
//! 从CSV文件加载行情数据，支持:
//! - 标准CSV格式: date,open,high,low,close,volume,amount
//! - 前复权/后复权
//! - 自动类型转换

use chrono::{NaiveDate, NaiveDateTime, TimeZone, Utc};
use finhack_core::types::Bar;
use rust_decimal::Decimal;
use std::path::{Path, PathBuf};
use tracing::{debug, warn};

/// 复权方式
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AdjustType {
    /// 不复权
    None,
    /// 前复权
    Forward,
    /// 后复权
    Backward,
}

/// 数据加载器trait
pub trait DataLoader {
    /// 加载指定标的的Bar数据
    fn load_bars(&self, symbol: &str) -> anyhow::Result<Vec<Bar>>;
}

/// CSV数据加载器
///
/// 从CSV文件加载行情数据
///
/// CSV格式要求:
/// - 必须包含列: date, open, high, low, close, volume
/// - 可选列: amount, adj_factor
/// - 日期格式: YYYY-MM-DD
pub struct CsvDataLoader {
    /// 数据目录
    data_dir: String,
    /// 复权方式
    adjust_type: AdjustType,
}

impl CsvDataLoader {
    /// 创建新的CSV数据加载器
    pub fn new(data_dir: impl Into<String>) -> Self {
        Self {
            data_dir: data_dir.into(),
            adjust_type: AdjustType::None,
        }
    }

    /// 设置复权方式
    pub fn with_adjust_type(mut self, adjust_type: AdjustType) -> Self {
        self.adjust_type = adjust_type;
        self
    }

    /// 获取CSV文件路径
    fn get_csv_path(&self, symbol: &str) -> PathBuf {
        // 支持多种文件命名方式
        let data_dir = Path::new(&self.data_dir);
        let filename = format!("{}.csv", symbol);
        data_dir.join(&filename)
    }

    /// 解析日期字符串
    fn parse_date(&self, date_str: &str) -> anyhow::Result<chrono::DateTime<Utc>> {
        // 尝试多种日期格式
        if let Ok(d) = NaiveDate::parse_from_str(date_str, "%Y-%m-%d") {
            let dt = d.and_hms_opt(0, 0, 0).unwrap();
            return Ok(Utc.from_utc_datetime(&dt));
        }
        if let Ok(d) = NaiveDate::parse_from_str(date_str, "%Y/%m/%d") {
            let dt = d.and_hms_opt(0, 0, 0).unwrap();
            return Ok(Utc.from_utc_datetime(&dt));
        }
        if let Ok(dt) = NaiveDateTime::parse_from_str(date_str, "%Y-%m-%d %H:%M:%S") {
            return Ok(Utc.from_utc_datetime(&dt));
        }
        anyhow::bail!("无法解析日期: {}", date_str)
    }

    /// 解析Decimal值
    fn parse_decimal(&self, value: &str) -> Decimal {
        Decimal::from_str_exact(value).unwrap_or(Decimal::ZERO)
    }

    /// 应用复权
    fn apply_adjust(&self, bar: &mut Bar, adj_factor: Decimal) {
        match self.adjust_type {
            AdjustType::None => {}
            AdjustType::Forward => {
                // 前复权: 价格除以复权因子
                if adj_factor != Decimal::ZERO && adj_factor != Decimal::ONE {
                    bar.open /= adj_factor;
                    bar.high /= adj_factor;
                    bar.low /= adj_factor;
                    bar.close /= adj_factor;
                }
            }
            AdjustType::Backward => {
                // 后复权: 价格乘以复权因子
                if adj_factor != Decimal::ZERO && adj_factor != Decimal::ONE {
                    bar.open *= adj_factor;
                    bar.high *= adj_factor;
                    bar.low *= adj_factor;
                    bar.close *= adj_factor;
                }
            }
        }
    }
}


impl DataLoader for CsvDataLoader {
    fn load_bars(&self, symbol: &str) -> anyhow::Result<Vec<Bar>> {
        let csv_path = self.get_csv_path(symbol);

        if !csv_path.exists() {
            anyhow::bail!("CSV文件不存在: {}", csv_path.display());
        }

        debug!(path = %csv_path.display(), "开始加载CSV数据");

        let content = std::fs::read_to_string(&csv_path)?;
        let mut reader = csv::Reader::from_reader(content.as_bytes());

        let mut bars = Vec::new();
        let headers = reader.headers()?.clone();

        // 获取列索引
        let date_idx = Self::find_column(&headers, "date")?;
        let open_idx = Self::find_column(&headers, "open")?;
        let high_idx = Self::find_column(&headers, "high")?;
        let low_idx = Self::find_column(&headers, "low")?;
        let close_idx = Self::find_column(&headers, "close")?;
        let volume_idx = Self::find_column(&headers, "volume")?;
        let amount_idx = Self::find_column_optional(&headers, "amount");
        let adj_idx = Self::find_column_optional(&headers, "adj_factor");

        for result in reader.records() {
            let record = match result {
                Ok(r) => r,
                Err(e) => {
                    warn!(error = %e, "CSV行解析失败，跳过");
                    continue;
                }
            };

            // 解析日期
            let timestamp = match self.parse_date(&record[date_idx]) {
                Ok(t) => t,
                Err(e) => {
                    warn!(date = %record[date_idx], error = %e, "日期解析失败，跳过");
                    continue;
                }
            };

            // 解析OHLCV
            let open = self.parse_decimal(&record[open_idx]);
            let high = self.parse_decimal(&record[high_idx]);
            let low = self.parse_decimal(&record[low_idx]);
            let close = self.parse_decimal(&record[close_idx]);
            let volume: i64 = record[volume_idx].parse().unwrap_or(0);
            let amount = amount_idx
                .map(|idx| self.parse_decimal(&record[idx]))
                .unwrap_or(Decimal::ZERO);
            let adj_factor = adj_idx
                .map(|idx| self.parse_decimal(&record[idx]))
                .unwrap_or(Decimal::ONE);

            let mut bar = Bar {
                symbol: symbol.to_string(),
                timestamp,
                open,
                high,
                low,
                close,
                volume,
                amount,
                adj_factor,
            };

            // 应用复权
            self.apply_adjust(&mut bar, adj_factor);

            bars.push(bar);
        }

        // 按时间排序
        bars.sort_by_key(|b| b.timestamp);

        debug!(symbol = %symbol, bars = bars.len(), "CSV数据加载完成");
        Ok(bars)
    }
}

impl CsvDataLoader {
    /// 查找列索引
    fn find_column(headers: &csv::StringRecord, name: &str) -> anyhow::Result<usize> {
        headers
            .iter()
            .position(|h| h.to_lowercase() == name.to_lowercase())
            .ok_or_else(|| anyhow::anyhow!("CSV文件缺少必需列: {}", name))
    }

    /// 查找可选列索引
    fn find_column_optional(headers: &csv::StringRecord, name: &str) -> Option<usize> {
        headers
            .iter()
            .position(|h| h.to_lowercase() == name.to_lowercase())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn create_test_csv(dir: &std::path::Path, symbol: &str) {
        std::fs::create_dir_all(dir).unwrap();
        let path = dir.join(format!("{}.csv", symbol));
        let mut file = std::fs::File::create(path).unwrap();
        writeln!(
            file,
            "date,open,high,low,close,volume,amount"
        ).unwrap();
        writeln!(
            file,
            "2024-01-02,10.00,10.50,9.80,10.20,1000000,10200000"
        ).unwrap();
        writeln!(
            file,
            "2024-01-03,10.20,10.80,10.10,10.50,1200000,12600000"
        ).unwrap();
        writeln!(
            file,
            "2024-01-04,10.50,10.60,10.20,10.30,800000,8240000"
        ).unwrap();
    }

    #[test]
    fn test_load_csv() {
        let dir = std::env::temp_dir().join("finhack_test_data");
        create_test_csv(&dir, "000001.SZ");

        let loader = CsvDataLoader::new(dir.to_string_lossy().to_string());
        let bars = loader.load_bars("000001.SZ").unwrap();

        assert_eq!(bars.len(), 3);
        assert_eq!(bars[0].symbol, "000001.SZ");
        assert_eq!(bars[0].close, Decimal::from_str_exact("10.20").unwrap());
    }

    #[test]
    fn test_csv_not_found() {
        let loader = CsvDataLoader::new("/nonexistent/path".to_string());
        let result = loader.load_bars("000001.SZ");
        assert!(result.is_err());
    }
}
