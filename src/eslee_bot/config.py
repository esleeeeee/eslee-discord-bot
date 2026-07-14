from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

POSTGRESQL_ASYNCPG_SCHEME = "postgresql+asyncpg://"


@dataclass(frozen=True, slots=True)
class DailySummaryConfig:
    requested_enabled: bool
    guild_id: int | None
    source_channel_id: int | None
    report_channel_id: int | None
    gemini_api_key: str | None = field(repr=False)
    ai_model: str = "gemini-3.5-flash"
    timezone_name: str = "Asia/Seoul"
    timezone: ZoneInfo | None = None
    run_time_text: str = "06:01"
    run_time: time | None = None
    raw_retention_days: int = 3
    min_total_messages: int = 10
    min_participants: int = 2
    min_user_messages: int = 3
    max_users: int = 20
    validation_errors: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.requested_enabled and not self.validation_errors


def _parse_snowflake(value: int | str | None, name: str, errors: list[str]) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        errors.append(f"{name} is missing")
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors.append(f"{name} must be a positive Discord ID")
        return None
    if parsed <= 0:
        errors.append(f"{name} must be a positive Discord ID")
        return None
    return parsed


def _normalize_asyncpg_ssl_query(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "sslmode" for key, _ in query):
        return url

    has_asyncpg_ssl = any(key == "ssl" for key, _ in query)
    converted_sslmode = False
    normalized_query: list[tuple[str, str]] = []
    for key, value in query:
        if key != "sslmode":
            normalized_query.append((key, value))
            continue
        if not has_asyncpg_ssl and not converted_sslmode:
            normalized_query.append(("ssl", value))
            converted_sslmode = True

    return urlunsplit(parts._replace(query=urlencode(normalized_query)))


def normalize_database_url(url: str) -> str:
    """Select asyncpg and translate libpq SSL options for PostgreSQL URLs."""
    normalized = url
    if not normalized.startswith(POSTGRESQL_ASYNCPG_SCHEME):
        for scheme in ("postgresql://", "postgres://"):
            if normalized.startswith(scheme):
                normalized = POSTGRESQL_ASYNCPG_SCHEME + normalized.removeprefix(scheme)
                break
    if not normalized.startswith(POSTGRESQL_ASYNCPG_SCHEME):
        return normalized
    return _normalize_asyncpg_ssl_query(normalized)


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    discord_token: str = Field(min_length=1)
    discord_dev_guild_id: int | None = None
    database_url: str = "sqlite+aiosqlite:///./data/eslee_bot.db"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    scheduler_poll_seconds: int = Field(default=60, ge=10, le=300)
    daily_summary_enabled: bool = False
    daily_summary_guild_id: int | str | None = None
    daily_summary_source_channel_id: int | str | None = None
    daily_summary_report_channel_id: int | str | None = None
    gemini_api_key: str | None = None
    daily_summary_ai_model: str = "gemini-3.5-flash"
    daily_summary_timezone: str = "Asia/Seoul"
    daily_summary_run_time: str = "06:01"
    daily_summary_raw_retention_days: int = Field(default=3, ge=1, le=30)
    daily_summary_min_total_messages: int = Field(default=10, ge=1)
    daily_summary_min_participants: int = Field(default=2, ge=1)
    daily_summary_min_user_messages: int = Field(default=3, ge=1)
    daily_summary_max_users: int = Field(default=20, ge=1, le=100)

    @field_validator("database_url", mode="before")
    @classmethod
    def use_async_postgresql_driver(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_database_url(value)
        return value

    @field_validator("discord_dev_guild_id", mode="before")
    @classmethod
    def empty_development_guild_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def get_daily_summary_config(self) -> DailySummaryConfig:
        errors: list[str] = []
        guild_id = _parse_snowflake(
            self.daily_summary_guild_id,
            "DAILY_SUMMARY_GUILD_ID",
            errors,
        )
        source_channel_id = _parse_snowflake(
            self.daily_summary_source_channel_id,
            "DAILY_SUMMARY_SOURCE_CHANNEL_ID",
            errors,
        )
        report_channel_id = _parse_snowflake(
            self.daily_summary_report_channel_id,
            "DAILY_SUMMARY_REPORT_CHANNEL_ID",
            errors,
        )
        api_key = (self.gemini_api_key or "").strip() or None
        if api_key is None:
            errors.append("GEMINI_API_KEY is missing")
        model = self.daily_summary_ai_model.strip()
        if not model:
            errors.append("DAILY_SUMMARY_AI_MODEL is blank")

        timezone: ZoneInfo | None = None
        try:
            timezone = ZoneInfo(self.daily_summary_timezone)
        except (ValueError, ZoneInfoNotFoundError):
            errors.append("DAILY_SUMMARY_TIMEZONE is invalid")

        parsed_run_time: time | None = None
        try:
            if len(self.daily_summary_run_time) != 5:
                raise ValueError
            parsed_run_time = time.fromisoformat(self.daily_summary_run_time)
        except ValueError:
            errors.append("DAILY_SUMMARY_RUN_TIME must use HH:MM")

        return DailySummaryConfig(
            requested_enabled=self.daily_summary_enabled,
            guild_id=guild_id,
            source_channel_id=source_channel_id,
            report_channel_id=report_channel_id,
            gemini_api_key=api_key,
            ai_model=model,
            timezone_name=self.daily_summary_timezone,
            timezone=timezone,
            run_time_text=self.daily_summary_run_time,
            run_time=parsed_run_time,
            raw_retention_days=self.daily_summary_raw_retention_days,
            min_total_messages=self.daily_summary_min_total_messages,
            min_participants=self.daily_summary_min_participants,
            min_user_messages=self.daily_summary_min_user_messages,
            max_users=self.daily_summary_max_users,
            validation_errors=tuple(errors) if self.daily_summary_enabled else (),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
