"""
FinHack Pro 数据模块

提供数据获取、特征工程、技术指标计算、数据验证、缓存和版本管理功能。
"""

from finhack_pro.data.cache import CacheStats, DataCache
from finhack_pro.data.features import FeatureEngineer
from finhack_pro.data.fetcher import DataFetcher
from finhack_pro.data.technical import TechnicalIndicator
from finhack_pro.data.validator import DataAnomaly, DataQualityReport, DataValidator, ValidationResult
from finhack_pro.data.versioning import DataVersion, DataVersionManager, VersionDiff

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
]
