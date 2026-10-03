"""Opt-in tests ONLY for a disposable PostgreSQL database created for this test run."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from eslee_bot.database import Database
from eslee_bot.database.models import Base
from eslee_bot.database.repositories import AnnouncementRepository
from eslee_bot.tasks.announcement_scheduler import AnnouncementScheduler


@pytest.fixture
async def database():
    raw_url = os.environ.get("ESLEE_TEST_POSTGRES_URL")
    if raw_url is None:
        pytest.skip("Disposable local PostgreSQL URL not supplied")
    url = make_url(raw_url)
    if (
        os.environ.get("ESLEE_TEST_POSTGRES_OWNED") != "1"
        or url.host != "127.0.0.1"
        or not re.fullmatch(r"eslee_audit_[a-f0-9]{32}", url.database or "")
    ):
        pytest.fail("Refusing any database not explicitly identified as an owned disposable DB")
    db = Database(raw_url)
    try:
        await db.initialize()
        yield db
    finally:
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await db.close()


async def announcement(db):
    async with db.session_factory() as session:
        return await AnnouncementRepository(session).create(
            guild_id=10,
            channel_id=20,
            source_message_id=30,
            creator_id=40,
            content_snapshot="source",
            announcement_type="TEXT",
            enabled=True,
            next_send_at=datetime.now(UTC) - timedelta(minutes=1),
        )


def scheduler(db, channel):
    result = AnnouncementScheduler(SimpleNamespace(database=db), 60)
    result._get_channel = AsyncMock(return_value=channel)
    return result


def channel():
    return SimpleNamespace(
        fetch_message=AsyncMock(
            return_value=SimpleNamespace(content="source", attachments=[], poll=None)
        ),
        send=AsyncMock(return_value=SimpleNamespace(id=99)),
    )


async def test_postgres_independent_instances_claim_once(database):
    item = await announcement(database)
    second = Database(database.url)
    shared_channel = channel()
    try:
        results = await asyncio.gather(
            scheduler(database, shared_channel)._dispatch(item),
            scheduler(second, shared_channel)._dispatch(item),
        )
        assert sorted(results) == [False, True]
        shared_channel.send.assert_awaited_once()
    finally:
        await second.close()


async def test_postgres_actual_finalize_constraint_failure_survives_restart(database):
    item = await announcement(database)
    shared_channel = channel()
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE announcements ADD CONSTRAINT audit_reject_finalization "
                "CHECK (reminder_message_id IS NULL)"
            )
        )
    with pytest.raises(IntegrityError):
        await scheduler(database, shared_channel)._dispatch(item)
    await database.close()
    restarted = Database(database.url)
    try:
        async with restarted.engine.begin() as connection:
            await connection.execute(
                text("ALTER TABLE announcements DROP CONSTRAINT audit_reject_finalization")
            )
        for _ in range(3):
            await scheduler(restarted, shared_channel).tick()
        shared_channel.send.assert_awaited_once()
        async with restarted.session_factory() as session:
            stored = await AnnouncementRepository(session).get(item.id, 10)
            assert stored.dispatch_id is not None
            assert stored.reminder_message_id is None
    finally:
        await restarted.close()


async def test_postgres_committed_claim_without_send_stays_uncertain(database):
    item = await announcement(database)
    async with database.session_factory() as session:
        assert await AnnouncementRepository(session).claim_dispatch(
            item, "pre-send", datetime.now(UTC)
        )
    await database.close()
    restarted = Database(database.url)
    shared_channel = channel()
    try:
        await scheduler(restarted, shared_channel).tick()
        shared_channel.send.assert_not_awaited()
    finally:
        await restarted.close()


async def test_postgres_stale_completed_slot_cannot_send_twice(database):
    item = await announcement(database)
    shared_channel = channel()
    worker = scheduler(database, shared_channel)
    assert await worker._dispatch(item)
    assert not await worker._dispatch(item)
    shared_channel.send.assert_awaited_once()


async def test_postgres_migrates_existing_table_without_losing_row(database):
    item = await announcement(database)
    async with database.engine.begin() as connection:
        await connection.execute(text("ALTER TABLE announcements DROP COLUMN dispatch_id"))
        await connection.execute(text("ALTER TABLE announcements DROP COLUMN dispatch_started_at"))
    await database.initialize()
    async with database.engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: {item["name"] for item in inspect(sync).get_columns("announcements")}
        )
        assert {"dispatch_id", "dispatch_started_at"} <= columns
    async with database.session_factory() as session:
        stored = await AnnouncementRepository(session).get(item.id, 10)
        assert stored.content_snapshot == "source"
        assert stored.dispatch_id is None


async def test_postgres_claim_recovery_is_guild_and_operation_scoped(database):
    from eslee_bot.recover_dispatch import reset_verified_unsent

    item = await announcement(database)
    async with database.session_factory() as session:
        assert await AnnouncementRepository(session).claim_dispatch(
            item, "offline", datetime.now(UTC)
        )
    args = dict(
        announcement_id=item.id,
        dispatch_id="offline",
        bots_stopped=True,
        verified_not_delivered=True,
    )
    assert not await reset_verified_unsent(database, guild_id=11, **args)
    assert await reset_verified_unsent(database, guild_id=10, **args)
    shared_channel = channel()
    await scheduler(database, shared_channel).tick()
    shared_channel.send.assert_awaited_once()
