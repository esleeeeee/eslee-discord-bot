import pytest

from eslee_bot.bot import EXTENSIONS, EsleeBot
from eslee_bot.config import Settings


@pytest.mark.asyncio
async def test_extensions_commands_and_intents_load_without_discord_connection() -> None:
    bot = EsleeBot(
        Settings(discord_token="test-token", database_url="sqlite+aiosqlite:///:memory:")
    )
    await bot.database.initialize()
    try:
        for extension in EXTENSIONS:
            await bot.load_extension(extension)

        assert [command.name for command in bot.tree.get_commands()] == [
            "공지",
            "금지어",
            "설정",
            "공지로 등록",
        ]
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
