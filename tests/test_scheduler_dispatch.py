from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from eslee_bot.database import Database
from eslee_bot.database.repositories import AnnouncementRepository
from eslee_bot.tasks.announcement_scheduler import AnnouncementScheduler


class FakeBot:
    def __init__(self, database: Database) -> None:
        self.database = database


def discord_not_found() -> discord.NotFound:
    response = SimpleNamespace(status=404, reason="Not Found")
    return discord.NotFound(response, "missing")  # type: ignore[arg-type]


async def create_announcement(database: Database, *, reminder_message_id: int | None = None):
    async with database.session_factory() as session:
        return await AnnouncementRepository(session).create(
            guild_id=10,
            channel_id=20,
            source_message_id=30,
            creator_id=40,
            content_snapshot="old snapshot",
            announcement_type="TEXT",
            enabled=True,
            next_send_at=datetime.now(UTC) - timedelta(minutes=1),
            reminder_message_id=reminder_message_id,
        )


@pytest.mark.asyncio
async def test_dispatch_replaces_reminder_and_updates_persistent_schedule() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        announcement = await create_announcement(database, reminder_message_id=31)
        old_reminder = SimpleNamespace(delete=AsyncMock())
        source = SimpleNamespace(content="updated source", attachments=[], poll=None)
        new_reminder = SimpleNamespace(id=32)
        channel = SimpleNamespace(
            fetch_message=AsyncMock(side_effect=[source, old_reminder]),
            send=AsyncMock(return_value=new_reminder),
        )
        scheduler = AnnouncementScheduler(FakeBot(database), 60)  # type: ignore[arg-type]
        scheduler._get_channel = AsyncMock(return_value=channel)  # type: ignore[method-assign]

        assert await scheduler._dispatch(announcement) is True
        old_reminder.delete.assert_awaited_once()
        channel.send.assert_awaited_once()

        async with database.session_factory() as session:
            stored = await AnnouncementRepository(session).get(announcement.id)
            assert stored is not None
            assert stored.content_snapshot == "updated source"
            assert stored.reminder_message_id == 32
            assert stored.last_sent_at is not None
            assert stored.next_send_at > datetime.now(UTC).replace(tzinfo=None)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_missing_source_disables_only_that_announcement() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        announcement = await create_announcement(database)
        channel = SimpleNamespace(fetch_message=AsyncMock(side_effect=discord_not_found()))
        scheduler = AnnouncementScheduler(FakeBot(database), 60)  # type: ignore[arg-type]
        scheduler._get_channel = AsyncMock(return_value=channel)  # type: ignore[method-assign]

        assert await scheduler._dispatch(announcement) is False

        async with database.session_factory() as session:
            stored = await AnnouncementRepository(session).get(announcement.id)
            assert stored is not None
            assert stored.enabled is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_tick_continues_after_one_announcement_fails() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        first = await create_announcement(database)
        async with database.session_factory() as session:
            second = await AnnouncementRepository(session).create(
                guild_id=10,
                channel_id=20,
                source_message_id=31,
                creator_id=40,
                content_snapshot="second",
                announcement_type="TEXT",
                enabled=True,
                next_send_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        scheduler = AnnouncementScheduler(FakeBot(database), 60)  # type: ignore[arg-type]
        scheduler._dispatch = AsyncMock(side_effect=[RuntimeError("one failure"), True])  # type: ignore[method-assign]

        await scheduler.tick()

        assert scheduler._dispatch.await_count == 2
        assert [call.args[0].id for call in scheduler._dispatch.await_args_list] == [
            first.id,
            second.id,
        ]
    finally:
        await database.close()
