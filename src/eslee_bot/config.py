from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

POSTGRESQL_ASYNCPG_SCHEME = "postgresql+asyncpg://"


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
