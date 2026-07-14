from unittest.mock import AsyncMock, patch

import discord
import pytest

from eslee_bot.bot import EXTENSIONS, EsleeBot
from eslee_bot.config import Settings


@pytest.mark.asyncio
async def test_invalid_daily_summary_settings_do_not_prevent_bot_construction() -> None:
    bot = EsleeBot(
        Settings(
            discord_token="test-token",
            database_url="sqlite+aiosqlite:///:memory:",
            daily_summary_enabled=True,
            _env_file=None,  # type: ignore[call-arg]
        )
    )
    try:
        assert bot.daily_summary.config.enabled is False
        assert bot.daily_summary.collector is None
        assert bot.daily_summary.report_service is None
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_extensions_commands_and_intents_load_without_discord_connection() -> None:
    bot = EsleeBot(
        Settings(
            discord_token="test-token",
            database_url="sqlite+aiosqlite:///:memory:",
            _env_file=None,  # type: ignore[call-arg]
        )
    )
    await bot.database.initialize()
    try:
        for extension in EXTENSIONS:
            await bot.load_extension(extension)

        assert [command.name for command in bot.tree.get_commands()] == [
            "공지",
            "금지어",
            "설정",
            "하루요약",
            "공지로 등록",
        ]
        assert bot.tree.get_commands(guild=discord.Object(id=123456789012345678)) == []
        assert bot.intents.guilds
        assert bot.intents.messages
        assert bot.intents.message_content
        assert not bot.intents.members
        forbidden_group = next(
            command for command in bot.tree.get_commands() if command.name == "금지어"
        )
        assert "일괄추가" in {command.name for command in forbidden_group.commands}
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_command_sync_is_global_without_a_development_guild() -> None:
    bot = EsleeBot(
        Settings(
            discord_token="test-token",
            database_url="sqlite+aiosqlite:///:memory:",
            _env_file=None,  # type: ignore[call-arg]
        )
    )
    try:
        with (
            patch.object(bot.tree, "sync", new=AsyncMock(return_value=[])) as sync,
            patch.object(bot.tree, "copy_global_to") as copy_global_to,
        ):
            await bot._sync_application_commands()

        sync.assert_awaited_once_with()
        copy_global_to.assert_not_called()
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_development_guild_sync_is_optional_and_keeps_global_sync() -> None:
    development_guild_id = 123456789012345678
    bot = EsleeBot(
        Settings(
            discord_token="test-token",
            discord_dev_guild_id=development_guild_id,
            database_url="sqlite+aiosqlite:///:memory:",
            _env_file=None,  # type: ignore[call-arg]
        )
    )
    try:
        with (
            patch.object(bot.tree, "sync", new=AsyncMock(return_value=[])) as sync,
            patch.object(bot.tree, "copy_global_to") as copy_global_to,
        ):
            await bot._sync_application_commands()

        assert sync.await_count == 2
        assert sync.await_args_list[0].args == ()
        assert sync.await_args_list[0].kwargs == {}
        development_guild = sync.await_args_list[1].kwargs["guild"]
        assert development_guild.id == development_guild_id
        copied_guild = copy_global_to.call_args.kwargs["guild"]
        assert copied_guild.id == development_guild_id
    finally:
        await bot.close()
