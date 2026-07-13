import pytest

from eslee_bot.config import Settings, normalize_database_url


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "postgresql://user:password@database.example:5432/eslee",
            "postgresql+asyncpg://user:password@database.example:5432/eslee",
        ),
        (
            "postgres://user:password@database.example:5432/eslee?ssl=require",
            "postgresql+asyncpg://user:password@database.example:5432/eslee?ssl=require",
        ),
        (
            "postgresql+asyncpg://user:password@database.example:5432/eslee",
            "postgresql+asyncpg://user:password@database.example:5432/eslee",
        ),
        (
            "sqlite+aiosqlite:///./data/eslee_bot.db",
            "sqlite+aiosqlite:///./data/eslee_bot.db",
        ),
    ],
)
def test_database_url_normalization(source: str, expected: str) -> None:
    assert normalize_database_url(source) == expected


def test_settings_normalize_postgresql_database_url() -> None:
    settings = Settings(
        discord_token="test-token",
        database_url="postgresql://user:password@database.example/eslee",
    )
    assert settings.database_url == "postgresql+asyncpg://user:password@database.example/eslee"


def test_blank_development_guild_id_is_treated_as_unset() -> None:
    settings = Settings(discord_token="test-token", discord_dev_guild_id="")  # type: ignore[arg-type]
    assert settings.discord_dev_guild_id is None


def test_numeric_development_guild_id_is_parsed() -> None:
    settings = Settings(
        discord_token="test-token",
        discord_dev_guild_id="123456789012345678",  # type: ignore[arg-type]
    )
    assert settings.discord_dev_guild_id == 123456789012345678
