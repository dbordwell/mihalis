"""Configuration loader: secrets from env, tunables from config.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    """Loaded from environment (or .env in dev)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "karen-finder/0.1"

    youtube_api_key: str = ""

    telegram_bot_token: str = ""
    telegram_consumer_chat_id: str = ""
    telegram_operator_chat_id: str = ""

    database_url: str = "sqlite:///./karen_finder.db"
    log_level: str = "INFO"
    tz: str = "America/New_York"

    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_endpoint: str = ""


class SubredditCfg(BaseModel):
    name: str
    weight: float = 1.0


class RuntimeCfg(BaseModel):
    poll_interval_minutes: int = 10
    metric_repoll_max_age_h: int = 48
    min_age_hours: float = 1.0
    max_age_hours: float = 72
    min_score: int = 50
    min_seconds: int = 40
    max_seconds: int = 180
    block_nsfw: bool = True
    baseline_warmup_days: int = 7


class RankerWeights(BaseModel):
    score_velocity_z: float = 1.0
    comment_velocity_z: float = 0.8
    acceleration_z: float = 0.6
    keyword_boost: float = 2.0
    subreddit_weight: float = 1.0
    saturation_penalty: float = 1.5


class RankerCaps(BaseModel):
    max_keyword_boost: float = 3.0


class DigestCfg(BaseModel):
    morning_local: str = "09:00"
    evening_local: str = "19:00"
    size: int = 10
    send_empty_if_nothing_qualifies: bool = False


class BreakingCfg(BaseModel):
    enabled: bool = True
    z_score_threshold: float = 4.0
    max_age_hours: float = 6
    daily_cap: int = 3
    quiet_hours_start_local: str = "00:00"
    quiet_hours_end_local: str = "06:00"


class SaturationCfg(BaseModel):
    mode: str = "shadow"  # shadow | active
    lookback_days: int = 30
    title_similarity_threshold: float = 0.6
    drop_if_views_above: int = 100_000
    drop_if_channel_subs_above: int = 50_000
    downrank_per_1k_views: float = 0.001
    top_n_to_check: int = 30


class OperatorCfg(BaseModel):
    digest_local: str = "08:30"
    size: int = 30


class WatchdogCfg(BaseModel):
    heartbeat_max_age_minutes: int = 25
    smoke_check_local: str = "11:00"


class AppConfig(BaseModel):
    runtime: RuntimeCfg = Field(default_factory=RuntimeCfg)
    subreddits: list[SubredditCfg] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    ranker_weights: RankerWeights = Field(default_factory=RankerWeights)
    ranker_caps: RankerCaps = Field(default_factory=RankerCaps)
    digest: DigestCfg = Field(default_factory=DigestCfg)
    breaking_alerts: BreakingCfg = Field(default_factory=BreakingCfg)
    saturation: SaturationCfg = Field(default_factory=SaturationCfg)
    operator: OperatorCfg = Field(default_factory=OperatorCfg)
    watchdog: WatchdogCfg = Field(default_factory=WatchdogCfg)


@lru_cache(maxsize=1)
def load_config(path: str | Path = "config.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        return AppConfig()
    data = yaml.safe_load(p.read_text()) or {}
    return AppConfig(**data)


@lru_cache(maxsize=1)
def secrets() -> Secrets:
    return Secrets()
