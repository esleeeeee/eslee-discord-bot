from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

POSTGRESQL_ASYNCPG_SCHEME = "postgresql+asyncpg://"


def normalize_database_url(url: str) -> str:
    """Select asyncpg for PostgreSQL URLs while leaving other backends unchanged."""
    if url.startswith(POSTGRESQL_ASYNCPG_SCHEME):
        return url
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return POSTGRESQL_ASYNCPG_SCHEME + url.removeprefix(scheme)
    return url


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


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
