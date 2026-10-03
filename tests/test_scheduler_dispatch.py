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
            stored = await AnnouncementRepository(session).get(
                announcement.id, announcement.guild_id
            )
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
            stored = await AnnouncementRepository(session).get(
                announcement.id, announcement.guild_id
            )
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


@pytest.mark.asyncio
async def test_success_then_database_failure_cannot_resend_after_restart(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'dispatch.db'}"
    database = Database(url)
    await database.initialize()
    announcement = await create_announcement(database)
    channel = SimpleNamespace(
        fetch_message=AsyncMock(
            return_value=SimpleNamespace(content="source", attachments=[], poll=None)
        ),
        send=AsyncMock(return_value=SimpleNamespace(id=99)),
    )
    scheduler = AnnouncementScheduler(FakeBot(database), 60)
    scheduler._get_channel = AsyncMock(return_value=channel)
    original = AnnouncementRepository.mark_sent
    monkeypatch.setattr(
        AnnouncementRepository, "mark_sent", AsyncMock(side_effect=RuntimeError("commit failed"))
    )
    with pytest.raises(RuntimeError):
        await scheduler._dispatch(announcement)
    monkeypatch.setattr(AnnouncementRepository, "mark_sent", original)
    await database.close()
    restarted = Database(url)
    await restarted.initialize()
    try:
        next_scheduler = AnnouncementScheduler(FakeBot(restarted), 60)
        next_scheduler._get_channel = AsyncMock(return_value=channel)
        for _ in range(3):
            await next_scheduler.tick()
        channel.send.assert_awaited_once()
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_two_instances_claim_only_one_delivery(tmp_path):
    import asyncio

    url = f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}"
    first_db, second_db = Database(url), Database(url)
    await first_db.initialize()
    await second_db.initialize()
    try:
        announcement = await create_announcement(first_db)
        source = SimpleNamespace(content="source", attachments=[], poll=None)
        channel = SimpleNamespace(
            fetch_message=AsyncMock(return_value=source),
            send=AsyncMock(return_value=SimpleNamespace(id=99)),
        )
        first = AnnouncementScheduler(FakeBot(first_db), 60)
        second = AnnouncementScheduler(FakeBot(second_db), 60)
        first._get_channel = AsyncMock(return_value=channel)
        second._get_channel = AsyncMock(return_value=channel)
        results = await asyncio.gather(
            first._dispatch(announcement), second._dispatch(announcement)
        )
        assert sorted(results) == [False, True]
        channel.send.assert_awaited_once()
    finally:
        await first_db.close()
        await second_db.close()


@pytest.mark.asyncio
async def test_uncertain_dispatch_reconciles_only_matching_bot_message():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        announcement = await create_announcement(database)
        async with database.session_factory() as session:
            assert await AnnouncementRepository(session).claim_dispatch(
                announcement, "operation", datetime.now(UTC)
            )
        embed = discord.Embed()
        embed.set_footer(text="dispatch:operation")
        message = SimpleNamespace(
            id=99, author=SimpleNamespace(id=7), embeds=[embed], created_at=datetime.now(UTC)
        )
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        bot = FakeBot(database)
        bot.user = SimpleNamespace(id=8)
        scheduler = AnnouncementScheduler(bot, 60)
        scheduler._get_channel = AsyncMock(return_value=channel)
        assert not await scheduler.reconcile(announcement.id, 11, 99)
        assert not await scheduler.reconcile(announcement.id, 10, 99)
        message.author.id = 8
        assert await scheduler.reconcile(announcement.id, 10, 99)
        async with database.session_factory() as session:
            stored = await AnnouncementRepository(session).get(announcement.id, 10)
            assert stored.dispatch_id is None
            assert stored.reminder_message_id == 99
            assert stored.next_send_at > datetime.now(UTC).replace(tzinfo=None)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_forbidden_delivery_releases_claim_for_permission_recovery():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        announcement = await create_announcement(database)
        error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        channel = SimpleNamespace(
            fetch_message=AsyncMock(
                return_value=SimpleNamespace(content="source", attachments=[], poll=None)
            ),
            send=AsyncMock(side_effect=[error, SimpleNamespace(id=99)]),
        )
        scheduler = AnnouncementScheduler(FakeBot(database), 60)
        scheduler._get_channel = AsyncMock(return_value=channel)
        assert not await scheduler._dispatch(announcement)
        assert await scheduler._dispatch(announcement)
        assert channel.send.await_count == 2
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_offline_unsent_recovery_requires_confirmations_and_exact_claim():
    from eslee_bot.recover_dispatch import reset_verified_unsent

    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        announcement = await create_announcement(database)
        async with database.session_factory() as session:
            assert await AnnouncementRepository(session).claim_dispatch(
                announcement, "stopped-before-send", datetime.now(UTC)
            )
        args = dict(
            guild_id=10,
            announcement_id=announcement.id,
            dispatch_id="stopped-before-send",
            verified_not_delivered=True,
        )
        with pytest.raises(ValueError):
            await reset_verified_unsent(database, bots_stopped=False, **args)
        assert not await reset_verified_unsent(
            database, bots_stopped=True, **{**args, "guild_id": 11}
        )
        assert not await reset_verified_unsent(
            database, bots_stopped=True, **{**args, "dispatch_id": "stale"}
        )
        assert await reset_verified_unsent(database, bots_stopped=True, **args)
        channel = SimpleNamespace(
            fetch_message=AsyncMock(
                return_value=SimpleNamespace(content="source", attachments=[], poll=None)
            ),
            send=AsyncMock(return_value=SimpleNamespace(id=99)),
        )
        scheduler = AnnouncementScheduler(FakeBot(database), 60)
        scheduler._get_channel = AsyncMock(return_value=channel)
        await scheduler.tick()
        channel.send.assert_awaited_once()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_uncertain_list_keeps_25_items_within_embed_limits(monkeypatch):
    from eslee_bot.cogs import announcements as module

    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        now = datetime.now(UTC)
        entries = [
            SimpleNamespace(
                id=index,
                guild_id=10,
                channel_id=20,
                source_message_id=30,
                content_snapshot="긴 내용 " * 100,
                announcement_type="TEXT",
                dispatch_id="a" * 32,
                dispatch_started_at=now,
                last_sent_at=now,
                next_send_at=now,
            )
            for index in range(25)
        ]
        monkeypatch.setattr(module, "require_management_permission", AsyncMock(return_value=True))
        monkeypatch.setattr(
            AnnouncementRepository, "list_for_guild", AsyncMock(return_value=entries)
        )
        bot = FakeBot(database)
        bot.tree = SimpleNamespace(add_command=lambda command: None)
        cog = module.AnnouncementCog(bot)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=10),
            response=SimpleNamespace(send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await module.AnnouncementCog.list_announcements.callback(cog, interaction)
        calls = (
            interaction.response.send_message.await_args_list
            + interaction.followup.send.await_args_list
        )
        assert len(calls) == 3
        descriptions = [call.kwargs["embed"].description for call in calls]
        assert all(len(text) <= 4096 for text in descriptions)
        assert sum(text.count("**#") for text in descriptions) == 25
        assert all("송신 미확정" in text and "마지막 성공" in text for text in descriptions)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_existing_announcement_table_gets_dispatch_columns(tmp_path):
    from sqlalchemy import inspect, text

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE announcements (id INTEGER PRIMARY KEY)"))
            await connection.execute(text("INSERT INTO announcements (id) VALUES (1)"))
        await database.initialize()
        async with database.engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {item["name"] for item in inspect(sync).get_columns("announcements")}
            )
            assert {"dispatch_id", "dispatch_started_at"} <= columns
            assert (
                await connection.execute(text("SELECT count(*) FROM announcements"))
            ).scalar() == 1
    finally:
        await database.close()
