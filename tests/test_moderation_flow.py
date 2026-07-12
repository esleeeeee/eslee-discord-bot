from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from eslee_bot.cogs.moderation import ModerationCog
from eslee_bot.database import Database
from eslee_bot.database.models import ModerationViolation
from eslee_bot.database.repositories import ForbiddenWordRepository
from eslee_bot.utils.text import normalize_forbidden_word


class FakeBot:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_channel(self, channel_id: int):
        return None


def make_message(*, dm_fails: bool = False, bot_author: bool = False):
    author = SimpleNamespace(
        id=2,
        bot=bot_author,
        mention="<@2>",
        name="tester",
        display_name="테스터",
        send=AsyncMock(side_effect=AttributeError if dm_fails else None),
    )
    channel = SimpleNamespace(id=3, send=AsyncMock())
    return SimpleNamespace(
        guild=SimpleNamespace(id=1),
        webhook_id=None,
        author=author,
        content="청사과와 바나나",
        channel=channel,
        delete=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_moderation_deletes_warns_and_persists_one_violation() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.session_factory() as session:
            repository = ForbiddenWordRepository(session)
            await repository.add(1, "사과", normalize_forbidden_word("사과"), 99)
            await repository.add(1, "바나나", normalize_forbidden_word("바나나"), 99)
        message = make_message()
        cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]

        await cog._moderate_message(message)

        message.delete.assert_awaited_once()
        message.author.send.assert_awaited_once()
        message.channel.send.assert_not_awaited()
        async with database.session_factory() as session:
            records = list(await session.scalars(select(ModerationViolation)))
            assert len(records) == 1
            assert "사과" in records[0].matched_words
            assert "바나나" in records[0].matched_words
            assert not hasattr(records[0], "message_content")
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_failed_dm_uses_five_second_channel_fallback() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        message = make_message(dm_fails=True)
        cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]

        await cog._warn_user(message, ["사과", "바나나"])

        message.channel.send.assert_awaited_once()
        assert message.channel.send.await_args.kwargs["delete_after"] == 5
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_bot_messages_are_ignored() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        message = make_message(bot_author=True)
        cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]

        await cog._moderate_message(message)

        message.delete.assert_not_awaited()
        message.author.send.assert_not_awaited()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_regular_member_can_list_forbidden_words() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.session_factory() as session:
            await ForbiddenWordRepository(session).add(1, "사과", "사과", 99)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]

        await cog.list_forbidden_words.callback(cog, interaction)

        interaction.response.send_message.assert_awaited_once()
        kwargs = interaction.response.send_message.await_args.kwargs
        assert kwargs["ephemeral"] is True
        assert "사과" in kwargs["embed"].description
    finally:
        await database.close()
