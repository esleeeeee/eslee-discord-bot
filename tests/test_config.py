import pytest
from pydantic import ValidationError

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
            "postgresql://user:password@database.example:5432/eslee?sslmode=require",
            "postgresql+asyncpg://user:password@database.example:5432/eslee?ssl=require",
        ),
        (
            "postgresql://user:password@database.example:5432/eslee"
            "?sslmode=verify-full&target_session_attrs=read-write",
            "postgresql+asyncpg://user:password@database.example:5432/eslee"
            "?ssl=verify-full&target_session_attrs=read-write",
        ),
        (
            "postgresql+asyncpg://user:password@database.example:5432/eslee"
            "?sslmode=require&ssl=verify-full",
            "postgresql+asyncpg://user:password@database.example:5432/eslee?ssl=verify-full",
        ),
        (
            "postgresql+asyncpg://user:password@database.example:5432/eslee",
            "postgresql+asyncpg://user:password@database.example:5432/eslee",
        ),
        (
            "sqlite+aiosqlite:///./data/eslee_bot.db",
            "sqlite+aiosqlite:///./data/eslee_bot.db",
        ),
        (
            "sqlite+aiosqlite:///./data/eslee_bot.db?sslmode=require",
            "sqlite+aiosqlite:///./data/eslee_bot.db?sslmode=require",
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


def test_settings_do_not_require_a_guild_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_DEV_GUILD_ID", raising=False)
    settings = Settings(discord_token="test-token", _env_file=None)  # type: ignore[call-arg]
    assert settings.discord_dev_guild_id is None


def test_onekey_api_is_disabled_when_both_settings_are_absent() -> None:
    settings = Settings(discord_token="test-token", _env_file=None)  # type: ignore[call-arg]

    assert settings.onekey_api_enabled is False


def test_onekey_api_settings_are_parsed_without_exposing_token() -> None:
    token = "secure-test-token-value"
    settings = Settings(
        discord_token="test-token",
        onekey_discord_user_id="123456789012345678",  # type: ignore[arg-type]
        onekey_api_token=token,
        _env_file=None,  # type: ignore[call-arg]
    )

    assert settings.onekey_api_enabled is True
    assert settings.onekey_discord_user_id == 123456789012345678
    assert settings.onekey_api_token is not None
    assert settings.onekey_api_token.get_secret_value() == token
    assert token not in repr(settings)


@pytest.mark.parametrize(
    "values",
    [
        {"onekey_discord_user_id": "123456789012345678"},
        {"onekey_api_token": "secure-test-token-value"},
        {
            "onekey_discord_user_id": "not-a-discord-id",
            "onekey_api_token": "secure-test-token-value",
        },
    ],
)
def test_incomplete_or_invalid_onekey_api_settings_fail_validation(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        Settings(discord_token="test-token", _env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize("variable", ["DISCORD_GUILD_ID", "GUILD_ID", "TEST_GUILD_ID"])
def test_other_guild_id_environment_names_are_not_used(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "123456789012345678")
    monkeypatch.delenv("DISCORD_DEV_GUILD_ID", raising=False)
    settings = Settings(discord_token="test-token", _env_file=None)  # type: ignore[call-arg]
    assert settings.discord_dev_guild_id is None


def test_blank_development_guild_id_is_treated_as_unset() -> None:
    settings = Settings(discord_token="test-token", discord_dev_guild_id="")  # type: ignore[arg-type]
    assert settings.discord_dev_guild_id is None


def test_numeric_development_guild_id_is_parsed() -> None:
    settings = Settings(
        discord_token="test-token",
        discord_dev_guild_id="123456789012345678",  # type: ignore[arg-type]
    )
    assert settings.discord_dev_guild_id == 123456789012345678


def test_disabled_daily_summary_does_not_require_its_secrets() -> None:
    settings = Settings(discord_token="test-token", _env_file=None)  # type: ignore[call-arg]

    config = settings.get_daily_summary_config()

    assert config.requested_enabled is False
    assert config.enabled is False
    assert config.validation_errors == ()


def test_invalid_daily_summary_config_disables_only_that_feature() -> None:
    settings = Settings(
        discord_token="test-token",
        daily_summary_enabled=True,
        _env_file=None,  # type: ignore[call-arg]
    )

    config = settings.get_daily_summary_config()

    assert config.enabled is False
    assert "DAILY_SUMMARY_GUILD_ID is missing" in config.validation_errors
    assert "GEMINI_API_KEY is missing" in config.validation_errors


def test_valid_daily_summary_config_is_parsed_without_exposing_secret() -> None:
    settings = Settings(
        discord_token="test-token",
        daily_summary_enabled=True,
        daily_summary_guild_id="100",
        daily_summary_source_channel_id="200",
        daily_summary_report_channel_id="300",
        gemini_api_key="secret-value",
        _env_file=None,  # type: ignore[call-arg]
    )

    config = settings.get_daily_summary_config()

    assert config.enabled is True
    assert config.guild_id == 100
    assert config.source_channel_id == 200
    assert config.report_channel_id == 300
    assert config.timezone is not None
    assert config.run_time is not None
    assert config.run_time_text == "06:01"
    assert "secret-value" not in repr(config)


@pytest.mark.parametrize(
    ("timezone", "run_time"),
    [("Invalid/Zone", "06:01"), ("Asia/Seoul", "25:99")],
)
def test_invalid_daily_summary_clock_config_disables_feature(timezone: str, run_time: str) -> None:
    settings = Settings(
        discord_token="test-token",
        daily_summary_enabled=True,
        daily_summary_guild_id="100",
        daily_summary_source_channel_id="200",
        daily_summary_report_channel_id="300",
        gemini_api_key="secret-value",
        daily_summary_timezone=timezone,
        daily_summary_run_time=run_time,
        _env_file=None,  # type: ignore[call-arg]
    )

    assert settings.get_daily_summary_config().enabled is False
