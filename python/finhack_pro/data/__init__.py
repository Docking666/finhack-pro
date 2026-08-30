"""
FinHack Pro 数据模块

提供数据获取、特征工程、技术指标计算、数据验证、缓存和版本管理功能。
"""

from finhack_pro.data.cache import CacheStats, DataCache
from finhack_pro.data.collector import CollectReport, MarketDataCollector
from finhack_pro.data.features import FeatureEngineer
from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.data.levels import LevelScan, PriceLevel, SupportResistanceDetector, screen_near_level
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
]
