"""
FinHack Pro 数据模块

提供数据获取、特征工程、技术指标计算、数据验证、缓存和版本管理功能。
"""

from finhack_pro.data.cache import CacheStats, DataCache
from finhack_pro.data.collector import CollectReport, MarketDataCollector
from finhack_pro.data.features import FeatureEngineer
from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.data.free_stockdb import FreeStockDBClient, FreeStockDBError, FreeStockDBSource
from finhack_pro.data.levels import LevelScan, PriceLevel, SupportResistanceDetector, screen_near_level
from finhack_pro.data.registry import (
    ENTRY_POINT_GROUP,
    DataSourceRegistry,
    MissingSourceConfig,
    RegistryError,
    SourceSpec,
    default_registry,
)
from finhack_pro.data.sources import BaseDataSource, build_source_chain
from finhack_pro.data.technical import TechnicalIndicator
from finhack_pro.data.validator import DataAnomaly, DataQualityReport, DataValidator, ValidationResult
from finhack_pro.data.versioning import DataVersion, DataVersionManager, VersionDiff
from finhack_pro.data.warehouse import CORE_COLUMNS, IngestResult, MarketWarehouse, WarehouseStats

__all__ = [
    "DataFetcher",
    "FeatureEngineer",
    "TechnicalIndicator",
    "DataValidator",
    "ValidationResult",
    "DataAnomaly",
    "DataQualityReport",
    "DataCache",
    "CacheStats",
    "DataVersionManager",
    "DataVersion",
    "VersionDiff",
    # 本地量化仓库：与 DataCache（TTL 缓存）职责不同，见 warehouse.py 模块文档
    "MarketWarehouse",
    "MarketDataCollector",
    "IngestResult",
    "CollectReport",
    "WarehouseStats",
    "CORE_COLUMNS",
    # 支撑阻力区域检测（全市场筛选的结构信号层）
    "SupportResistanceDetector",
    "PriceLevel",
    "LevelScan",
    "screen_near_level",
    # free-stockdb 本地数据引擎适配（须先启动 stockdb.exe）
    "FreeStockDBClient",
    "FreeStockDBSource",
    "FreeStockDBError",
    # 数据源插件化：注册中心 + 契约基类 + 配置即组装
    "BaseDataSource",
    "build_source_chain",
    "DataSourceRegistry",
    "SourceSpec",
    "RegistryError",
    "MissingSourceConfig",
    "default_registry",
    "ENTRY_POINT_GROUP",
]
