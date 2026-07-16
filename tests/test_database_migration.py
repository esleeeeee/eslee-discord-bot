from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import URL, create_engine

from eslee_bot.database.migration import (
    MigrationConflictError,
    MigrationError,
    SchemaMismatchError,
    SourceSnapshot,
    file_sha256,
    migrate_sqlite_to_postgresql,
    plan_table_rows,
    read_sqlite_snapshot,
)
from eslee_bot.database.models import (
    Announcement,
    Base,
    ForbiddenWord,
    GuildSettings,
    ModerationViolation,
)


def _create_source_database(path: Path) -> None:
    engine = create_engine(URL.create("sqlite", database=str(path)))
    Base.metadata.create_all(engine)
    created_at = datetime(2026, 7, 12, 2, 30, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(
            GuildSettings.__table__.insert(),
            {
                "id": 1,
                "guild_id": 100,
                "moderation_log_channel_id": 101,
                "updated_at": created_at,
                "created_at": created_at,
            },
        )
        connection.execute(
            ForbiddenWord.__table__.insert(),
            {
                "id": 2,
                "guild_id": 100,
                "word": "blocked",
                "normalized_word": "blocked",
                "created_by": 102,
                "created_at": created_at,
            },
        )
        connection.execute(
            Announcement.__table__.insert(),
            {
                "id": 3,
                "guild_id": 100,
                "channel_id": 103,
                "source_message_id": 104,
                "creator_id": 105,
                "content_snapshot": "announcement",
                "announcement_type": "once",
                "enabled": True,
                "last_sent_at": None,
                "next_send_at": created_at + timedelta(hours=1),
                "reminder_message_id": None,
                "created_at": created_at,
            },
        )
        connection.execute(
            ModerationViolation.__table__.insert(),
            {
                "id": 4,
                "guild_id": 100,
                "user_id": 106,
                "channel_id": 103,
                "matched_words": '["blocked"]',
                "created_at": created_at,
            },
        )
    engine.dispose()


def test_read_sqlite_snapshot_is_read_only_and_normalizes_values(tmp_path: Path) -> None:
    source_path = tmp_path / "source.db"
    _create_source_database(source_path)
    hash_before = file_sha256(source_path)
    modified_before = source_path.stat().st_mtime_ns

    snapshot = read_sqlite_snapshot(source_path)

    assert file_sha256(source_path) == hash_before == snapshot.sha256
    assert source_path.stat().st_mtime_ns == modified_before
    assert snapshot.counts == {
        "guild_settings": 1,
        "forbidden_words": 1,
        "announcements": 1,
        "moderation_violations": 1,
    }
    announcement = snapshot.rows["announcements"][0]
    assert announcement["enabled"] is True
    assert announcement["created_at"].tzinfo is UTC


def test_read_sqlite_snapshot_rejects_schema_mismatch_without_writing(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "wrong-schema.sqlite3"
    with sqlite3.connect(source_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    hash_before = file_sha256(source_path)

    with pytest.raises(SchemaMismatchError, match="missing tables"):
        read_sqlite_snapshot(source_path)

    assert file_sha256(source_path) == hash_before


@pytest.mark.asyncio
async def test_migration_rejects_a_non_postgresql_target(tmp_path: Path) -> None:
    snapshot = SourceSnapshot(
        path=tmp_path / "unused.db",
        sha256="unused",
        rows={},
        columns={},
    )

    with pytest.raises(MigrationError, match="must be a PostgreSQL URL"):
        await migrate_sqlite_to_postgresql(
            snapshot,
            "sqlite+aiosqlite:///./target.db",
            dry_run=True,
        )


def test_natural_duplicate_is_skipped_and_numeric_id_collision_is_remapped() -> None:
    source_rows = (
        {"id": 1, "guild_id": 10, "normalized_word": "alpha"},
        {"id": 2, "guild_id": 10, "normalized_word": "beta"},
    )
    target_rows = (
        {"id": 1, "guild_id": 99, "normalized_word": "occupied"},
        {"id": 8, "guild_id": 10, "normalized_word": "beta"},
    )

    planned, result = plan_table_rows("forbidden_words", source_rows, target_rows)

    assert [row.values for row in planned] == [
        {"id": 9, "guild_id": 10, "normalized_word": "alpha"}
    ]
    assert planned[0].id_remapped is True
    assert result.id_remapped == 1
    assert result.skipped_existing == 1


def test_violation_fingerprint_treats_equivalent_instants_as_duplicates() -> None:
    source_rows = (
        {
            "id": 1,
            "guild_id": 10,
            "user_id": 20,
            "channel_id": 30,
            "matched_words": '["blocked"]',
            "created_at": datetime(2026, 7, 12, 0, 0, tzinfo=UTC),
        },
    )
    target_rows = (
        {
            **source_rows[0],
            "id": 99,
            "created_at": datetime(
                2026,
                7,
                12,
                9,
                0,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        },
    )

    planned, result = plan_table_rows(
        "moderation_violations",
        source_rows,
        target_rows,
    )

    assert planned == []
    assert result.skipped_existing == 1


def test_existing_announcement_with_same_id_and_natural_key_is_skipped() -> None:
    source = {"id": 3, "guild_id": 10, "channel_id": 20, "source_message_id": 30}

    planned, result = plan_table_rows("announcements", (source,), (dict(source),))

    assert planned == []
    assert result.skipped_existing == 1


@pytest.mark.parametrize(
    "target",
    [
        {"id": 3, "guild_id": 10, "channel_id": 20, "source_message_id": 999},
        {"id": 99, "guild_id": 10, "channel_id": 20, "source_message_id": 30},
    ],
)
def test_announcement_id_or_natural_key_conflict_aborts(target: dict[str, int]) -> None:
    source = {"id": 3, "guild_id": 10, "channel_id": 20, "source_message_id": 30}

    with pytest.raises(MigrationConflictError, match="announcement id=3"):
        plan_table_rows("announcements", (source,), (target,))
