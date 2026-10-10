from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from eslee_bot.cogs.moderation import ClearForbiddenWordsView, ModerationCog
from eslee_bot.database import Database
from eslee_bot.database.repositories import ForbiddenWordRepository

OWNER_ID = 10
ADMIN_ID = 20
MEMBER_ID = 30


class FakeBot:
    def __init__(self, database: Database) -> None:
        self.database = database


def make_interaction(*, user_id: int = OWNER_ID, administrator: bool = False, guild_id: int = 1):
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.guild_permissions = SimpleNamespace(administrator=administrator)
    response = MagicMock()
    response.is_done = MagicMock(return_value=False)
    response.send_message = AsyncMock()
    response.edit_message = AsyncMock()
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id, owner_id=OWNER_ID),
        user=user,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        original_response=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


async def seed(database: Database) -> None:
    async with database.session_factory() as session:
        repository = ForbiddenWordRepository(session)
        await repository.add_many(1, [("사과", "사과"), ("바나나", "바나나")], 99)
        await repository.add(2, "포도", "포도", 99)


async def words(database: Database, guild_id: int) -> list[str]:
    async with database.session_factory() as session:
        entries = await ForbiddenWordRepository(session).list_for_guild(guild_id)
    return [entry.word for entry in entries]


@pytest.fixture
async def database():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


async def test_delete_all_for_guild_only_touches_that_guild(database: Database) -> None:
    await seed(database)
    async with database.session_factory() as session:
        assert await ForbiddenWordRepository(session).delete_all_for_guild(1) == 2
    assert await words(database, 1) == []
    assert await words(database, 2) == ["포도"]


async def test_command_asks_for_confirmation_without_deleting(database: Database) -> None:
    await seed(database)
    cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]
    interaction = make_interaction()

    await cog.clear_forbidden_words.callback(cog, interaction)  # type: ignore[arg-type]

    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], ClearForbiddenWordsView)
    assert "2개" in interaction.response.send_message.await_args.args[0]
    assert len(await words(database, 1)) == 2


async def test_command_reports_when_nothing_is_registered(database: Database) -> None:
    cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]
    interaction = make_interaction()

    await cog.clear_forbidden_words.callback(cog, interaction)  # type: ignore[arg-type]

    interaction.response.send_message.assert_awaited_once_with(
        "등록된 금지어가 없습니다.", ephemeral=True
    )


async def test_command_rejects_non_managers(database: Database) -> None:
    await seed(database)
    cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]
    interaction = make_interaction(user_id=MEMBER_ID)

    await cog.clear_forbidden_words.callback(cog, interaction)  # type: ignore[arg-type]

    assert "권한" in interaction.response.send_message.await_args.args[0]
    assert len(await words(database, 1)) == 2


async def test_confirm_button_deletes_every_word(database: Database) -> None:
    await seed(database)
    cog = ModerationCog(FakeBot(database))  # type: ignore[arg-type]
    interaction = make_interaction(user_id=ADMIN_ID, administrator=True)
    await cog.clear_forbidden_words.callback(cog, interaction)  # type: ignore[arg-type]
    view: ClearForbiddenWordsView = interaction.response.send_message.await_args.kwargs["view"]

    click = make_interaction(user_id=ADMIN_ID, administrator=True)
    await view.confirm.callback(click)  # type: ignore[call-arg]

    click.response.edit_message.assert_awaited_once_with(
        content="✅ 금지어 2개를 모두 삭제했습니다.", view=None
    )
    assert await words(database, 1) == []
    assert await words(database, 2) == ["포도"]
    assert view.is_finished()


async def test_confirm_rechecks_permission_at_click_time() -> None:
    clear = AsyncMock(return_value=3)
    view = ClearForbiddenWordsView(owner_id=ADMIN_ID, clear=clear)

    click = make_interaction(user_id=ADMIN_ID, administrator=False)
    await view.confirm.callback(click)  # type: ignore[call-arg]

    clear.assert_not_awaited()
    kwargs = click.response.edit_message.await_args.kwargs
    assert "권한" in kwargs["content"]
    assert kwargs["view"] is None


async def test_cancel_button_keeps_words() -> None:
    clear = AsyncMock(return_value=3)
    view = ClearForbiddenWordsView(owner_id=OWNER_ID, clear=clear)

    click = make_interaction()
    await view.cancel.callback(click)  # type: ignore[call-arg]

    clear.assert_not_awaited()
    assert click.response.edit_message.await_args.kwargs["view"] is None
    assert view.is_finished()


async def test_only_the_invoker_can_press_buttons() -> None:
    view = ClearForbiddenWordsView(owner_id=OWNER_ID, clear=AsyncMock())

    stranger = make_interaction(user_id=ADMIN_ID, administrator=True)

    assert await view.interaction_check(stranger) is False  # type: ignore[arg-type]
    stranger.response.send_message.assert_awaited_once()
    assert await view.interaction_check(make_interaction()) is True  # type: ignore[arg-type]


async def test_timeout_removes_buttons() -> None:
    view = ClearForbiddenWordsView(owner_id=OWNER_ID, clear=AsyncMock())
    view.message = SimpleNamespace(edit=AsyncMock())  # type: ignore[assignment]

    await view.on_timeout()

    assert view.message.edit.await_args.kwargs["view"] is None
