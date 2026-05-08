//! 数据存储抽象
//!
//! 定义统一的数据存储接口，支持多种存储后端:
//! - CSV文件
//! - Parquet文件
//! - PostgreSQL数据库
//! - Redis缓存

use finhack_core::types::Bar;
use std::collections::HashMap;

/// 存储类型
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StorageType {
    /// CSV文件存储
    Csv,
    /// Parquet文件存储
    Parquet,
    /// PostgreSQL数据库
    Postgres,
    /// Redis缓存
    Redis,
}

impl std::fmt::Display for StorageType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            StorageType::Csv => write!(f, "csv"),
            StorageType::Parquet => write!(f, "parquet"),
            StorageType::Postgres => write!(f, "postgres"),
            StorageType::Redis => write!(f, "redis"),
        }
    }
}

impl std::str::FromStr for StorageType {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "csv" => Ok(StorageType::Csv),
            "parquet" => Ok(StorageType::Parquet),
            "postgres" | "postgresql" => Ok(StorageType::Postgres),
            "redis" => Ok(StorageType::Redis),
            _ => Err(format!("未知的存储类型: {}", s)),
        }
    }
}

/// 数据查询条件
#[derive(Debug, Clone)]
pub struct DataQuery {
    /// 标的代码
    pub symbol: String,
    /// 开始时间(可选)
    pub start_time: Option<String>,
    /// 结束时间(可选)
    pub end_time: Option<String>,
    /// 限制返回数量(可选)
    pub limit: Option<usize>,
}

impl DataQuery {
    /// 创建新的查询
    pub fn new(symbol: impl Into<String>) -> Self {
        Self {
            symbol: symbol.into(),
            start_time: None,
            end_time: None,
            limit: None,
        }
    }

    /// 设置时间范围
    pub fn with_time_range(mut self, start: impl Into<String>, end: impl Into<String>) -> Self {
        self.start_time = Some(start.into());
        self.end_time = Some(end.into());
        self
    }

    /// 设置返回数量限制
    pub fn with_limit(mut self, limit: usize) -> Self {
        self.limit = Some(limit);
        self
    }
}

/// 数据存储trait
///
/// 定义统一的数据读写接口
pub trait DataStorage: Send + Sync {
    /// 保存Bar数据
    fn save_bars(&self, bars: &[Bar]) -> anyhow::Result<()>;

    /// 加载Bar数据
    fn load_bars(&self, query: &DataQuery) -> anyhow::Result<Vec<Bar>>;

    /// 删除指定标的的数据
    fn delete_bars(&self, symbol: &str) -> anyhow::Result<()>;

    /// 获取可用标的列表
    fn list_symbols(&self) -> anyhow::Result<Vec<String>>;

    /// 获取存储类型
    fn storage_type(&self) -> StorageType;
}

/// 内存数据存储(用于测试)
pub struct InMemoryStorage {
    /// 存储的数据: symbol -> Vec<Bar>
    data: std::sync::Mutex<HashMap<String, Vec<Bar>>>,
}

impl InMemoryStorage {
    /// 创建新的内存存储
    pub fn new() -> Self {
        Self {
            data: std::sync::Mutex::new(HashMap::new()),
        }
    }
}

impl Default for InMemoryStorage {
    fn default() -> Self {
        Self::new()
    }
}

impl DataStorage for InMemoryStorage {
    fn save_bars(&self, bars: &[Bar]) -> anyhow::Result<()> {
        let mut data = self.data.lock().unwrap();
        for bar in bars {
            data.entry(bar.symbol.clone())
                .or_insert_with(Vec::new)
                .push(bar.clone());
        }
        // 排序
        for bars in data.values_mut() {
            bars.sort_by_key(|b| b.timestamp);
        }
        Ok(())
    }

    fn load_bars(&self, query: &DataQuery) -> anyhow::Result<Vec<Bar>> {
        let data = self.data.lock().unwrap();
        let bars = data.get(&query.symbol).cloned().unwrap_or_default();

        // 应用时间过滤
        let filtered = bars;
        // 简化处理: 返回全部或限制数量
        let result = if let Some(limit) = query.limit {
            filtered.into_iter().take(limit).collect()
        } else {
            filtered
        };

        Ok(result)
    }

    fn delete_bars(&self, symbol: &str) -> anyhow::Result<()> {
        let mut data = self.data.lock().unwrap();
        data.remove(symbol);
        Ok(())
    }

    fn list_symbols(&self) -> anyhow::Result<Vec<String>> {
        let data = self.data.lock().unwrap();
        Ok(data.keys().cloned().collect())
    }

    fn storage_type(&self) -> StorageType {
        StorageType::Csv // 标记为Csv，实际是内存
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    fn create_test_bar(symbol: &str, day_offset: i64) -> Bar {
        use rust_decimal::Decimal;
        Bar::new(
            symbol,
            Utc::now() - chrono::Duration::days(day_offset),
            Decimal::from(10),
            Decimal::from(11),
            Decimal::from(9),
            Decimal::from(10),
            1000000,
            Decimal::from(10000000),
        )
    }

    #[test]
    fn test_in_memory_storage() {
        let storage = InMemoryStorage::new();

        let bars = vec![
            create_test_bar("000001.SZ", 3),
            create_test_bar("000001.SZ", 2),
            create_test_bar("000001.SZ", 1),
        ];

        storage.save_bars(&bars).unwrap();

        let query = DataQuery::new("000001.SZ");
        let loaded = storage.load_bars(&query).unwrap();
        assert_eq!(loaded.len(), 3);

        let symbols = storage.list_symbols().unwrap();
        assert_eq!(symbols.len(), 1);
        assert_eq!(symbols[0], "000001.SZ");

        storage.delete_bars("000001.SZ").unwrap();
        let loaded_after = storage.load_bars(&query).unwrap();
        assert_eq!(loaded_after.len(), 0);
    }

    #[test]
    fn test_data_query() {
        let query = DataQuery::new("000001.SZ")
            .with_time_range("2024-01-01", "2024-12-31")
            .with_limit(100);

        assert_eq!(query.symbol, "000001.SZ");
        assert_eq!(query.start_time, Some("2024-01-01".to_string()));
        assert_eq!(query.end_time, Some("2024-12-31".to_string()));
        assert_eq!(query.limit, Some(100));
    }

    #[test]
    fn test_storage_type_parsing() {
        assert_eq!("csv".parse::<StorageType>().unwrap(), StorageType::Csv);
        assert_eq!("parquet".parse::<StorageType>().unwrap(), StorageType::Parquet);
        assert_eq!("postgres".parse::<StorageType>().unwrap(), StorageType::Postgres);
        assert_eq!("redis".parse::<StorageType>().unwrap(), StorageType::Redis);
    }
}
