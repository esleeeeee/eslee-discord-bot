from datetime import UTC, datetime

import pytest

from eslee_bot.database import Database
from eslee_bot.database.repositories import (
    DuplicateRecordError,
    ForbiddenWordRepository,
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
