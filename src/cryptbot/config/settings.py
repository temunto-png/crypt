from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


# ---------------------------------------------------------------------------
# ネスト設定モデル
# ---------------------------------------------------------------------------


class CircuitBreakerSettings(BaseModel):
    """Circuit Breaker の基準率（実際の閾値は現在残高 × pct で動的計算）。
    0 や負値を設定すると安全機構が無効化されるため pydantic で拒否する。
    """

    max_trade_loss_pct: float = Field(default=0.02, gt=0, lt=1)
    daily_loss_pct: float = Field(default=0.03, gt=0, lt=1)
    weekly_loss_pct: float = Field(default=0.05, gt=0, lt=1)
    monthly_loss_pct: float = Field(default=0.10, gt=0, lt=1)
    max_drawdown_pct: float = Field(default=0.15, gt=0, lt=1)  # ピーク残高から計算
    max_consecutive_losses: int = Field(default=5, gt=0)


class CvarSettings(BaseModel):
    """CVaR 段階的対応の閾値（直近50トレードの95%ile尾部）。
    3 つの閾値は全て負数で warn > half > stop の順序を強制する。
    """

    warn_pct: float = Field(default=-0.03, lt=0)
    half_pct: float = Field(default=-0.04, lt=0)
    stop_pct: float = Field(default=-0.05, lt=0)

    @model_validator(mode="after")
    def validate_ordering(self) -> "CvarSettings":
        if not (self.warn_pct > self.half_pct > self.stop_pct):
            raise ValueError(
                f"CVaR 閾値は warn_pct > half_pct > stop_pct の順でなければなりません。"
                f" 現在: warn={self.warn_pct}, half={self.half_pct}, stop={self.stop_pct}"
            )
        return self


class KillSwitchSettings(BaseModel):
    active: bool = False


class BacktestSettings(BaseModel):
    """テスト期間保護。test_set_evaluated が true になったら再評価禁止"""

    test_start_index: int | None = None
    test_set_evaluated: bool = False
    test_set_data_hash: str | None = None


class LoggingSettings(BaseModel):
    log_dir: str = "logs"
    level: str = "DEBUG"
    rotation: str = "1 day"
    retention: str = "30 days"


_StrategyName = Literal["ma_cross", "momentum", "mean_reversion", "volatility_filter"]


class PaperSettings(BaseModel):
    """Paper trading エンジンの設定。"""

    initial_balance: float = Field(default=1_000_000.0, gt=0)
    pair: str = "btc_jpy"
    timeframe: str = "1hour"
    warmup_bars: int = Field(default=50, gt=0)
    strategy_name: _StrategyName = "ma_cross"


class ModelSettings(BaseModel):
    """ML モデルフィルターの設定。"""

    enabled: bool = False
    experiments_dir: str = "src/cryptbot/models/experiments"
    active_experiment_id: str | None = None     # None = 最新モデルを自動選択
    model_type: Literal["lgbm", "xgb"] = "lgbm"
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    # 劣化検知
    degradation_window: int = Field(default=100, gt=0)
    degradation_drop_pct: float = Field(default=20.0, gt=0.0)


# ---------------------------------------------------------------------------
# YAML 設定ソース
# ---------------------------------------------------------------------------


class YamlConfigSource(PydanticBaseSettingsSource):
    """config.yaml を読み込む pydantic-settings カスタムソース。
    環境変数よりも優先度が低い。
    """

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path) -> None:
        super().__init__(settings_cls)
        self._data: dict = {}
        if yaml_path.exists():
            with yaml_path.open(encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    self._data = loaded

    def get_field_value(self, field_name: str, field_info):  # type: ignore[override]
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict:
        return self._data


# ---------------------------------------------------------------------------
# メイン Settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        env_prefix="CRYPT_",
        extra="ignore",
    )

    # bitbank API キー（環境変数のみ。config.yaml には書かない）
    bitbank_api_key: SecretStr = SecretStr("")
    bitbank_api_secret: SecretStr = SecretStr("")

    # 取引モード
    mode: Literal["paper", "live"] = "paper"

    # 各種ネスト設定
    circuit_breaker: CircuitBreakerSettings = CircuitBreakerSettings()
    cvar: CvarSettings = CvarSettings()
    kill_switch: KillSwitchSettings = KillSwitchSettings()
    backtest: BacktestSettings = BacktestSettings()
    logging: LoggingSettings = LoggingSettings()
    paper: PaperSettings = PaperSettings()
    model: ModelSettings = ModelSettings()

    @classmethod
    def settings_customise_sources(  # type: ignore[override]
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = Path("config.yaml")
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSource(settings_cls, yaml_path),
            file_secret_settings,
        )


def load_settings(yaml_path: Path | None = None) -> Settings:
    """設定を読み込む。yaml_path を指定した場合はそのファイルを使用する。"""
    if yaml_path is not None:
        # テスト等で yaml_path を明示的に渡す場合
        _resolved_path = yaml_path

        class _Settings(Settings):
            @classmethod
            def settings_customise_sources(  # type: ignore[override]
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                return (
                    init_settings,
                    env_settings,
                    dotenv_settings,
                    YamlConfigSource(settings_cls, _resolved_path),
                    file_secret_settings,
                )

        return _Settings()
    return Settings()
