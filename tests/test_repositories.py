from datetime import UTC, datetime

import pytest

from eslee_bot.database import Database
from eslee_bot.database.repositories import (
    AnnouncementRepository,
    DuplicateRecordError,
    ForbiddenWordRepository,
    GuildSettingsRepository,
    ModerationViolationRepository,
)
from eslee_bot.utils.text import normalize_forbidden_word


@pytest.mark.asyncio
async def test_forbidden_words_are_unique_per_guild_after_normalization() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.session_factory() as session:
            repository = ForbiddenWordRepository(session)
            await repository.add(1, "TEST", normalize_forbidden_word("TEST"), 99)
            with pytest.raises(DuplicateRecordError):
                await repository.add(1, "test", normalize_forbidden_word("test"), 99)
        async with database.session_factory() as session:
            entries = await ForbiddenWordRepository(session).list_for_guild(1)
            assert [entry.word for entry in entries] == ["TEST"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_violation_persists_matches_but_not_message_content() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.session_factory() as session:
            violation = await ModerationViolationRepository(session).create(
                guild_id=1,
                user_id=2,
                channel_id=3,
                matched_words=["사과", "바나나"],
            )
            assert "사과" in violation.matched_words
            assert not hasattr(violation, "message_content")
            assert isinstance(violation.created_at, datetime)
            if violation.created_at.tzinfo is not None:
                assert violation.created_at.tzinfo == UTC
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_batch_add_skips_existing_forbidden_words_and_adds_the_rest() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.session_factory() as session:
            repository = ForbiddenWordRepository(session)
            await repository.add(1, "사과", "사과", 99)
            added, skipped = await repository.add_many(
                1,
                [("사과", "사과"), ("바나나", "바나나"), ("TEST", "test")],
                99,
            )
            assert added == ["바나나", "TEST"]
            assert skipped == ["사과"]
        async with database.session_factory() as session:
            stored = await ForbiddenWordRepository(session).list_for_guild(1)
            assert {entry.normalized_word for entry in stored} == {"사과", "바나나", "test"}
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_repository_data_is_isolated_between_guilds() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.session_factory() as session:
            forbidden_words = ForbiddenWordRepository(session)
            await forbidden_words.add(1, "사과", "사과", 100)
            await forbidden_words.add(2, "사과", "사과", 200)

            settings = GuildSettingsRepository(session)
            await settings.set_log_channel(1, 101)
            await settings.set_log_channel(2, 202)

            announcements = AnnouncementRepository(session)
            first = await announcements.create(
                guild_id=1,
                channel_id=11,
                source_message_id=111,
                creator_id=100,
                content_snapshot="guild one",
                announcement_type="TEXT",
                enabled=True,
                next_send_at=datetime.now(UTC),
            )
            second = await announcements.create(
                guild_id=2,
                channel_id=22,
                source_message_id=222,
                creator_id=200,
                content_snapshot="guild two",
                announcement_type="TEXT",
                enabled=True,
                next_send_at=datetime.now(UTC),
            )

        async with database.session_factory() as session:
            forbidden_words = ForbiddenWordRepository(session)
            assert [item.word for item in await forbidden_words.list_for_guild(1)] == ["사과"]
            assert [item.word for item in await forbidden_words.list_for_guild(2)] == ["사과"]

            settings = GuildSettingsRepository(session)
            first_settings = await settings.get(1)
            second_settings = await settings.get(2)
            assert first_settings is not None
            assert second_settings is not None
            assert first_settings.moderation_log_channel_id == 101
            assert second_settings.moderation_log_channel_id == 202

            announcements = AnnouncementRepository(session)
            assert [item.id for item in await announcements.list_for_guild(1)] == [first.id]
            assert [item.id for item in await announcements.list_for_guild(2)] == [second.id]
            assert await announcements.get(first.id, 2) is None
            assert await announcements.delete(first.id, 2) is False
            assert await announcements.get(first.id, 1) is not None
    finally:
        await database.close()
