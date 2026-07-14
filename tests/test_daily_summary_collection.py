from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import discord
import pytest

from eslee_bot.config import DailySummaryConfig
from eslee_bot.database import Database
from eslee_bot.database.repositories import DailySummaryMessageRepository
from eslee_bot.services.daily_summary import day_bounds_utc
from eslee_bot.services.daily_summary_runtime import DailySummaryCollector


def summary_config() -> DailySummaryConfig:
    return DailySummaryConfig(
        requested_enabled=True,
        guild_id=100,
        source_channel_id=200,
        report_channel_id=300,
        gemini_api_key="test-key",
        timezone=ZoneInfo("Asia/Seoul"),
        run_time=datetime.strptime("00:02", "%H:%M").time(),
    )


def fake_message(
    message_id: int,
    created_at: datetime,
    *,
    guild_id: int = 100,
    channel_id: int = 200,
    author_id: int = 400,
    content: str = "안녕",
    bot: bool = False,
    webhook_id: int | None = None,
    message_type: discord.MessageType = discord.MessageType.default,
    reply_to_message_id: int | None = None,
) -> SimpleNamespace:
    reference = (
        SimpleNamespace(message_id=reply_to_message_id) if reply_to_message_id is not None else None
    )
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=channel_id),
        author=SimpleNamespace(
            id=author_id,
            bot=bot,
            name=f"user-{author_id}",
            display_name=f"사용자 {author_id}",
        ),
        webhook_id=webhook_id,
        type=message_type,
        content=content,
        created_at=created_at,
        reference=reference,
    )


class FakeBot:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_channel(self, channel_id: int) -> None:
        return None

    async def fetch_channel(self, channel_id: int) -> None:
        return None


class FakeHistoryChannel:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self.messages = messages
        self.history_kwargs: dict[str, object] = {}

    def history(self, **kwargs: object):  # type: ignore[no-untyped-def]
        self.history_kwargs = kwargs

        async def iterate():  # type: ignore[no-untyped-def]
            for message in self.messages:
                yield message

        return iterate()


@pytest.mark.asyncio
async def test_realtime_collection_filters_scope_and_non_human_messages() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    collector = DailySummaryCollector(FakeBot(database), summary_config())  # type: ignore[arg-type]
    created_at = datetime(2026, 7, 14, 3, tzinfo=UTC)
    messages = [
        fake_message(1, created_at),
        fake_message(2, created_at, guild_id=101),
        fake_message(3, created_at, channel_id=201),
        fake_message(4, created_at, bot=True),
        fake_message(5, created_at, webhook_id=999),
        fake_message(6, created_at, content="   "),
        fake_message(7, created_at, message_type=discord.MessageType.recipient_add),
    ]
    try:
        results = [await collector.collect(message) for message in messages]
        assert results == [True, False, False, False, False, False, False]
        assert await collector.collect(messages[0]) is False
        async with database.session_factory() as session:
            stored = await DailySummaryMessageRepository(session).list_between(
                100,
                200,
                datetime(2026, 7, 14, tzinfo=UTC),
                datetime(2026, 7, 15, tzinfo=UTC),
            )
        assert [item.message_id for item in stored] == [1]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_message_update_delete_and_reply_relationship() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    collector = DailySummaryCollector(FakeBot(database), summary_config())  # type: ignore[arg-type]
    created_at = datetime(2026, 7, 14, 3, tzinfo=UTC)
    original = fake_message(10, created_at, reply_to_message_id=9)
    try:
        assert await collector.collect(original) is True
        edited = fake_message(10, created_at, content="수정됨", reply_to_message_id=8)
        assert await collector.update(edited) is True
        async with database.session_factory() as session:
            stored = await DailySummaryMessageRepository(session).list_between(
                100,
                200,
                datetime(2026, 7, 14, tzinfo=UTC),
                datetime(2026, 7, 15, tzinfo=UTC),
            )
        assert stored[0].content == "수정됨"
        assert stored[0].reply_to_message_id == 8
        assert await collector.collect(fake_message(11, created_at)) is True
        assert await collector.delete_many({11}, 100, 200) == 1
        assert await collector.update(fake_message(10, created_at, content="")) is True
        assert await collector.delete(10, 100, 200) is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_startup_backfill_uses_seoul_midnight_and_is_idempotent() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    collector = DailySummaryCollector(FakeBot(database), summary_config())  # type: ignore[arg-type]
    timezone = ZoneInfo("Asia/Seoul")
    now = datetime(2026, 7, 14, 19, 0, tzinfo=timezone).astimezone(UTC)
    start, _ = day_bounds_utc(datetime(2026, 7, 14).date(), timezone)
    channel = FakeHistoryChannel(
        [
            fake_message(1, start),
            fake_message(2, start.replace(hour=start.hour + 1), reply_to_message_id=1),
            fake_message(3, start, bot=True),
            fake_message(4, start, webhook_id=44),
            fake_message(5, start, content=""),
            fake_message(6, start, message_type=discord.MessageType.channel_name_change),
            fake_message(7, start.replace(day=start.day - 1)),
        ]
    )
    collector._resolve_source_channel = AsyncMock(return_value=channel)  # type: ignore[method-assign]
    try:
        first = await collector.run_backfill(now=now)
        second = await collector.run_backfill(now=now)
        assert first.fetched == 7
        assert first.inserted == 2
        assert first.skipped_existing == 0
        assert first.excluded == 5
        assert second.inserted == 0
        assert second.skipped_existing == 2
        assert channel.history_kwargs["limit"] is None
        assert channel.history_kwargs["oldest_first"] is True
        async with database.session_factory() as session:
            stored = await DailySummaryMessageRepository(session).list_between(
                100,
                200,
                start,
                now,
            )
        assert [item.message_id for item in stored] == [1, 2]
        assert stored[1].reply_to_message_id == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_backfill_failure_is_contained_and_database_remains_available() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    collector = DailySummaryCollector(FakeBot(database), summary_config())  # type: ignore[arg-type]
    collector._resolve_source_channel = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("history unavailable")
    )
    try:
        result = await collector.run_backfill()
        assert result.failed is True
        async with database.session_factory() as session:
            assert (
                await DailySummaryMessageRepository(session).count_between(
                    100,
                    200,
                    datetime(2026, 7, 14, tzinfo=UTC),
                    datetime(2026, 7, 15, tzinfo=UTC),
                )
                == 0
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_startup_backfill_is_started_without_blocking_bot_startup() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    collector = DailySummaryCollector(FakeBot(database), summary_config())  # type: ignore[arg-type]
    release = asyncio.Event()

    async def blocked_backfill():  # type: ignore[no-untyped-def]
        await release.wait()
        return None

    collector.run_backfill = AsyncMock(side_effect=blocked_backfill)  # type: ignore[method-assign]
    try:
        task = collector.start_backfill()

        assert task is not None
        assert task.done() is False
        release.set()
        await task
    finally:
        await database.close()
