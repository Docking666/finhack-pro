//! FinHack Pro - 数据引擎模块
//!
//! 提供数据加载、存储和Feed功能:
//! - CSV/Parquet数据加载
//! - 数据Feed(回测用)
//! - 数据存储抽象

pub mod loader;
pub mod feed;
pub mod storage;

pub use loader::{CsvDataLoader, DataLoader};
pub use feed::DataFeed;
pub use storage::DataStorage;
