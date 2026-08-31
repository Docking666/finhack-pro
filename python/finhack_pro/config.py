"""
FinHack Pro 配置加载模块

支持从YAML文件和环境变量加载配置，支持多环境切换(backtest/paper/live)。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    """LLM配置"""
    provider: str = "openai"  # openai / anthropic
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    model: str = "gpt-4o"
    temperature: float = 0.3
    max_tokens: int = 4096
    timeout: int = 60
    max_retries: int = 3


class AgentLLMConfig(BaseModel):
    """单 Agent 的 LLM 配置覆盖项

    所有字段可选；留空(None/空串)表示跟随全局 LLM 配置。
    使用 model_dump(exclude_none=True) 序列化后仅保留用户覆盖字段，
    避免默认值冲掉全局配置。
    """
    model_config = {"extra": "ignore"}

    provider: Optional[str] = None
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[int] = None
    max_retries: Optional[int] = None

    @field_validator(
        "provider", "openai_api_key", "openai_base_url",
        "anthropic_api_key", "model",
        mode="before",
    )
    @classmethod
    def _normalize_empty_str(cls, v: Any) -> Any:
        """空串归一为 None（字段级别，序列化/校验均生效）"""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


# 服务商预置表：选中后自动填充 base_url 与推荐模型
# key 为 provider 标识（OpenAI 兼容服务商在 LLM 调用层统一按 openai 处理，
# 预置表仅负责 base_url 与 model 建议）
PROVIDER_PRESETS: Dict[str, Dict[str, str]] = {
    "orca": {
        "label": "OrcaRouter",
        "base_url": "https://api.orcarouter.ai/v1",
        "default_model": "orcarouter/auto",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
    },
    "zhipu": {
        "label": "智谱AI",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4-plus",
    },
}


class DataConfig(BaseModel):
    """数据源配置（可插拔多源架构，v2.3.6）

    source / tushare_token / cache_dir 为兼容旧配置；
    sources 提供显式源优先级列表（如 [akshare_tx, baostock, tushare]），
    custom_source 提供用户自定义源（如 "my_module.MyDataSource"）。
    """
    source: str = "akshare"  # akshare / tushare（legacy，未提供 sources 时映射为多源链）
    tushare_token: str = ""
    cache_dir: str = "data/cache"
    # 本地量化仓库（永久事实库）。与 cache_dir 的 TTL 缓存职责不同：
    # 全市场扫描与回测可复现都依赖它，勿指向 cache_dir。
    warehouse_dir: str = "data/warehouse"
    warehouse_backend: str = "auto"  # auto / parquet / csv（parquet 需可选依赖 pyarrow）
    # free-stockdb 本地引擎（数据源名 free_stockdb）。默认只连本机；
    # 勿指向公共服务器——批量拉取触发风控后会返回随机 mock 数据。
    free_stockdb_host: str = "127.0.0.1"
    free_stockdb_port: int = 7899
    default_start: str = "2020-01-01"
    default_end: str = "2024-12-31"
    akshare_hist_api: str = "tx"  # akshare 日线端点：tx=腾讯证券(默认，绕开东财封锁) / em=东方财富
    sources: List[str] = []       # 显式数据源优先级列表
    custom_source: str = ""       # 用户自定义数据源类（须继承 BaseDataSource）


class BacktestConfig(BaseModel):
    """回测配置"""
    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.0003  # 佣金费率 万三
    stamp_tax_rate: float = 0.001  # 印花税 千一(仅卖出)
    slippage: float = 0.001  # 滑点
    benchmark: str = "000300.SH"  # 沪深300作为基准


class RiskConfig(BaseModel):
    """风控配置

    注意两类"置信度"语义不同，勿混用：
      - signal_confidence_threshold: 策略信号置信度门槛（信号质量阈值，决策层）。
        LLM 生成的信号 confidence 低于该值即拒绝，默认 0.6。
      - var_confidence: VaR 计算的统计置信水平（风险度量参数，回测/风控层）。
        用于在险价值估算，默认 0.95。
    """
    max_position_pct: float = 0.3  # 单只股票最大仓位
    max_total_position_pct: float = 0.8  # 总仓位上限
    max_drawdown_pct: float = 0.15  # 最大回撤限制
    max_daily_loss_pct: float = 0.05  # 单日最大亏损
    var_confidence: float = 0.95  # VaR 统计置信水平（风险度量，非信号门槛）
    signal_confidence_threshold: float = 0.6  # 信号置信度门槛（信号质量，决策阈值）
    stop_loss_pct: float = 0.05  # 默认止损
    take_profit_pct: float = 0.10  # 默认止盈
    initial_capital: float = 1_000_000  # 组合初始资金（风控评估的初始组合起点：该资金+空仓，真实初始状态）


class RustCoreConfig(BaseModel):
    """Rust核心API配置"""
    api_url: str = "http://localhost:8080"
    ws_url: str = "ws://localhost:8081"
    api_key: str = ""
    timeout: int = 30


class WorkshopConfig(BaseModel):
    """创意工坊云端配置

    默认留空：未配置云端地址时 WorkshopCloud 构造会抛出明确错误。
    可通过环境变量 FINHACK_WORKSHOP__BASE_URL 或 YAML workshop.base_url 配置。
    """
    base_url: str = ""
    timeout: int = 30
    workshop_dir: str = "data/workshop"


class AgentConfig(BaseModel):
    """Agent系统配置"""
    market_analyzer_model: str = "gpt-4o"
    strategy_generator_model: str = "gpt-4o"
    risk_manager_model: str = "gpt-4o"
    trade_executor_model: str = "gpt-4o"
    analysis_interval: int = 300  # 分析间隔(秒)
    max_concurrent_symbols: int = 10


class FinhackProConfig(BaseSettings):
    """FinHack Pro 总配置

    支持从YAML文件和环境变量加载。
    环境变量前缀: FINHACK_
    """
    model_config = SettingsConfigDict(
        env_prefix="FINHACK_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",  # 忽略未知字段，避免YAML解析错误
    )

    # 运行环境: backtest / paper / live
    environment: str = "backtest"

    # 日志级别
    log_level: str = "INFO"
    log_file: str = "logs/finhack_pro.log"

    # 各子模块配置
    llm: LLMConfig = Field(default_factory=LLMConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    rust_core: RustCoreConfig = Field(default_factory=RustCoreConfig)
    workshop: WorkshopConfig = Field(default_factory=WorkshopConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    # per-Agent LLM 配置覆盖（coordinator 读取 config["agents"]，键名必须一致）
    # 示例:
    #   agents:
    #     market_analyzer:
    #       model: "deepseek-chat"
    #       openai_api_key: "sk-xxx"
    #       openai_base_url: "https://api.deepseek.com/v1"
    agents: Dict[str, AgentLLMConfig] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "FinhackProConfig":
        """从YAML文件加载配置"""
        path = Path(path)
        if not path.exists():
            logger.warning(f"配置文件不存在: {path}, 使用默认配置")
            return cls()

        with open(path, "r", encoding="utf-8") as f:
            raw: Dict[str, Any] = yaml.safe_load(f) or {}

        # 支持多环境配置
        env = os.getenv("FINHACK_ENVIRONMENT", raw.get("environment", "backtest"))
        env_config = raw.pop(env, {})
        raw.update(env_config)

        return cls(**raw)

    def save_to_yaml(self, path: str | Path) -> None:
        """保存配置到YAML文件"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                self.model_dump(exclude_none=True),
                f,
                default_flow_style=False,
                allow_unicode=True,
            )


# 全局配置单例
_global_config: Optional[FinhackProConfig] = None


def get_config(config_path: Optional[str] = None, force_reload: bool = False) -> FinhackProConfig:
    """获取全局配置实例

    Args:
        config_path: 配置文件路径；为None时依次尝试 FINHACK_CONFIG 环境变量、
                     默认配置文件（cwd/config/default.yaml），
                     仍无则使用纯默认配置（不读任何 YAML）
        force_reload: 是否强制重新加载配置

    Returns:
        FinhackProConfig实例
    """
    global _global_config
    if _global_config is None or force_reload:
        path = config_path or os.environ.get("FINHACK_CONFIG")
        if not path:
            # 默认配置文件：与 ConfigService._resolve_default_config_path 保持一致，
            # 避免 WorkshopCloud 等模块在无 FINHACK_CONFIG 时读不到 workshop.base_url 等配置
            cwd_path = Path.cwd() / "config" / "default.yaml"
            if cwd_path.exists():
                path = str(cwd_path)
        if path:
            # from_yaml 内部处理文件不存在（warning + 纯默认）
            _global_config = FinhackProConfig.from_yaml(path)
        else:
            _global_config = FinhackProConfig()
    return _global_config


def set_global_config(config: FinhackProConfig) -> None:
    """设置全局配置单例（供 update_config 等场景同步使用）"""
    global _global_config
    _global_config = config


def reset_config() -> None:
    """重置全局配置(主要用于测试)"""
    global _global_config
    _global_config = None
