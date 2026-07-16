from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, Table, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from eslee_bot.config import normalize_database_url
from eslee_bot.database.models import Base

TABLE_ORDER = (
    "guild_settings",
    "forbidden_words",
    "announcements",
    "moderation_violations",
)
TABLES = {name: Base.metadata.tables[name] for name in TABLE_ORDER}
NATURAL_KEYS = {
    "guild_settings": ("guild_id",),
    "forbidden_words": ("guild_id", "normalized_word"),
    "announcements": ("guild_id", "channel_id", "source_message_id"),
    "moderation_violations": (
        "guild_id",
        "user_id",
        "channel_id",
        "matched_words",
        "created_at",
    ),
}
MIGRATION_LOCK_ID = 0x45534C45454D4947


class MigrationError(RuntimeError):
    pass


class SchemaMismatchError(MigrationError):
    pass


class MigrationConflictError(MigrationError):
    pass


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    path: Path
    sha256: str
    rows: dict[str, tuple[dict[str, Any], ...]]
    columns: dict[str, tuple[str, ...]]

    @property
    def counts(self) -> dict[str, int]:
        return {name: len(self.rows[name]) for name in TABLE_ORDER}


@dataclass(frozen=True, slots=True)
class PlannedInsert:
    values: dict[str, Any]
    id_remapped: bool


@dataclass(slots=True)
class TableMigrationResult:
    source: int
    target_before: int
    inserted: int = 0
    id_remapped: int = 0
    skipped_existing: int = 0
    target_after: int = 0
    missing: int = 0


@dataclass(frozen=True, slots=True)
class MigrationReport:
    source_path: Path
    source_sha256: str
    dry_run: bool
    results: dict[str, TableMigrationResult]

    @property
    def committed(self) -> bool:
        return not self.dry_run


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise MigrationError("SQLite contains an invalid datetime value.") from error
    else:
        raise MigrationError(f"Unsupported datetime value type: {type(value).__name__}")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_row(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for column in table.columns:
        value = row[column.name]
        if isinstance(column.type, DateTime):
            value = _parse_datetime(value)
        elif isinstance(column.type, Boolean) and value is not None:
            value = bool(value)
        normalized[column.name] = value
    return normalized


def _quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _expected_columns() -> dict[str, tuple[str, ...]]:
    return {name: tuple(column.name for column in TABLES[name].columns) for name in TABLE_ORDER}


def _schema_diff(
    expected: dict[str, tuple[str, ...]],
    actual: dict[str, tuple[str, ...]],
    *,
    allow_extra_tables: bool,
) -> list[str]:
    details: list[str] = []
    expected_tables = set(expected)
    actual_tables = set(actual)
    if missing_tables := sorted(expected_tables - actual_tables):
        details.append(f"missing tables={missing_tables}")
    if not allow_extra_tables and (extra_tables := sorted(actual_tables - expected_tables)):
        details.append(f"extra tables={extra_tables}")
    for table_name in sorted(expected_tables & actual_tables):
        expected_columns = set(expected[table_name])
        actual_columns = set(actual[table_name])
        missing_columns = sorted(expected_columns - actual_columns)
        extra_columns = sorted(actual_columns - expected_columns)
        if missing_columns or extra_columns:
            details.append(f"{table_name}(missing={missing_columns}, extra={extra_columns})")
    return details


def _validate_schema(
    actual: dict[str, tuple[str, ...]], *, label: str, allow_extra_tables: bool = False
) -> None:
    details = _schema_diff(
        _expected_columns(),
        actual,
        allow_extra_tables=allow_extra_tables,
    )
    if details:
        raise SchemaMismatchError(
            f"{label} schema does not match the current application model: " + "; ".join(details)
        )


def read_sqlite_snapshot(path: Path) -> SourceSnapshot:
    resolved = path.expanduser().resolve(strict=True)
    with resolved.open("rb") as file:
        header = file.read(16)
    if header != b"SQLite format 3\x00":
        raise MigrationError(f"File does not have a valid SQLite header: {resolved}")

    before_hash = file_sha256(resolved)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError(f"SQLite integrity check failed: {integrity}")

        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns = {
            name: tuple(
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info({_quote_sqlite_identifier(name)})"
                )
            )
            for name in table_names
        }
        _validate_schema(columns, label="SQLite", allow_extra_tables=True)

        rows = {
            name: tuple(
                _normalize_row(TABLES[name], dict(row))
                for row in connection.execute(
                    f"SELECT * FROM {_quote_sqlite_identifier(name)} ORDER BY id"
                )
            )
            for name in TABLE_ORDER
        }
        connection.rollback()
    finally:
        connection.close()

    after_hash = file_sha256(resolved)
    if before_hash != after_hash:
        raise MigrationError("SQLite file changed while it was being read; migration stopped.")
    return SourceSnapshot(
        path=resolved,
        sha256=before_hash,
        rows=rows,
        columns=columns,
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _parse_datetime(value)
    return value


def _row_key(row: dict[str, Any], columns: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(_canonical_value(row[column]) for column in columns)


def plan_table_rows(
    table_name: str,
    source_rows: tuple[dict[str, Any], ...],
    target_rows: tuple[dict[str, Any], ...],
) -> tuple[list[PlannedInsert], TableMigrationResult]:
    natural_columns = NATURAL_KEYS[table_name]
    target_by_id = {int(row["id"]): row for row in target_rows}
    target_by_natural = {_row_key(row, natural_columns): row for row in target_rows}
    next_id = (
        max(
            [
                0,
                *(int(row["id"]) for row in source_rows),
                *(int(row["id"]) for row in target_rows),
            ]
        )
        + 1
    )
    planned: list[PlannedInsert] = []
    result = TableMigrationResult(source=len(source_rows), target_before=len(target_rows))

    for source in source_rows:
        source_id = int(source["id"])
        natural_key = _row_key(source, natural_columns)
        existing_id = target_by_id.get(source_id)
        existing_natural = target_by_natural.get(natural_key)

        if table_name == "announcements":
            if existing_id is None and existing_natural is None:
                values = dict(source)
                planned.append(PlannedInsert(values=values, id_remapped=False))
                target_by_id[source_id] = values
                target_by_natural[natural_key] = values
                continue
            if (
                existing_id is not None
                and existing_natural is not None
                and int(existing_id["id"]) == source_id
                and int(existing_natural["id"]) == source_id
            ):
                result.skipped_existing += 1
                continue
            raise MigrationConflictError(
                f"Cannot preserve SQLite announcement id={source_id} due to a target conflict."
            )

        if existing_natural is not None:
            result.skipped_existing += 1
            continue

        values = dict(source)
        remapped = existing_id is not None
        if remapped:
            while next_id in target_by_id:
                next_id += 1
            values["id"] = next_id
            next_id += 1
            result.id_remapped += 1
        planned.append(PlannedInsert(values=values, id_remapped=remapped))
        target_by_id[int(values["id"])] = values
        target_by_natural[natural_key] = values

    return planned, result


def _inspect_postgresql_schema(connection: Connection) -> dict[str, tuple[str, ...]]:
    inspector = inspect(connection)
    return {
        name: tuple(column["name"] for column in inspector.get_columns(name))
        for name in inspector.get_table_names()
    }


async def _fetch_target_rows(
    connection: AsyncConnection, table: Table
) -> tuple[dict[str, Any], ...]:
    result = await connection.execute(select(table).order_by(table.c.id))
    return tuple(_normalize_row(table, dict(row)) for row in result.mappings().all())


def _missing_source_rows(
    table_name: str,
    source_rows: tuple[dict[str, Any], ...],
    target_rows: tuple[dict[str, Any], ...],
) -> int:
    natural_columns = NATURAL_KEYS[table_name]
    target_by_id = {int(row["id"]): row for row in target_rows}
    target_natural_keys = {_row_key(row, natural_columns) for row in target_rows}
    missing = 0
    for source in source_rows:
        natural_key = _row_key(source, natural_columns)
        if table_name == "announcements":
            target = target_by_id.get(int(source["id"]))
            if target is None or _row_key(target, natural_columns) != natural_key:
                missing += 1
        elif natural_key not in target_natural_keys:
            missing += 1
    return missing


async def _synchronize_id_sequences(connection: AsyncConnection) -> None:
    for table_name, table in TABLES.items():
        maximum_id = await connection.scalar(select(func.max(table.c.id)))
        if maximum_id is None:
            continue
        sequence_name = await connection.scalar(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": table_name},
        )
        if sequence_name is None:
            continue
        await connection.execute(
            text("SELECT setval(CAST(:sequence_name AS regclass), :maximum_id, true)"),
            {"sequence_name": sequence_name, "maximum_id": maximum_id},
        )


async def migrate_sqlite_to_postgresql(
    snapshot: SourceSnapshot,
    database_url: str,
    *,
    dry_run: bool = False,
) -> MigrationReport:
    normalized_url = normalize_database_url(database_url)
    if not normalized_url.startswith("postgresql+asyncpg://"):
        raise MigrationError("The target DATABASE_URL must be a PostgreSQL URL.")

    engine = create_async_engine(normalized_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": MIGRATION_LOCK_ID},
                )
                target_schema = await connection.run_sync(_inspect_postgresql_schema)
                _validate_schema(
                    target_schema,
                    label="PostgreSQL",
                    allow_extra_tables=True,
                )

                plans: dict[str, list[PlannedInsert]] = {}
                results: dict[str, TableMigrationResult] = {}
                for table_name in TABLE_ORDER:
                    target_rows = await _fetch_target_rows(connection, TABLES[table_name])
                    plans[table_name], results[table_name] = plan_table_rows(
                        table_name,
                        snapshot.rows[table_name],
                        target_rows,
                    )

                for table_name in TABLE_ORDER:
                    table = TABLES[table_name]
                    for planned in plans[table_name]:
                        statement = (
                            postgresql_insert(table)
                            .values(**planned.values)
                            .on_conflict_do_nothing()
                            .returning(table.c.id)
                        )
                        inserted_id = (await connection.execute(statement)).scalar_one_or_none()
                        if inserted_id is None:
                            raise MigrationConflictError(
                                f"A concurrent change or unique conflict occurred in {table_name}."
                            )
                        results[table_name].inserted += 1

                for table_name in TABLE_ORDER:
                    target_rows = await _fetch_target_rows(connection, TABLES[table_name])
                    result = results[table_name]
                    result.target_after = len(target_rows)
                    result.missing = _missing_source_rows(
                        table_name,
                        snapshot.rows[table_name],
                        target_rows,
                    )
                    if result.missing:
                        raise MigrationError(
                            f"Verification found {result.missing} missing SQLite rows in "
                            f"{table_name}."
                        )

                if file_sha256(snapshot.path) != snapshot.sha256:
                    raise MigrationError(
                        "SQLite file changed during migration; PostgreSQL transaction stopped."
                    )

                if dry_run:
                    await transaction.rollback()
                else:
                    await _synchronize_id_sequences(connection)
                    await transaction.commit()
            except BaseException:
                if transaction.is_active:
                    await transaction.rollback()
                raise
    finally:
        await engine.dispose()

    return MigrationReport(
        source_path=snapshot.path,
        source_sha256=snapshot.sha256,
        dry_run=dry_run,
        results=results,
    )


def print_migration_report(report: MigrationReport) -> None:
    mode = "DRY RUN (rolled back)" if report.dry_run else "COMMITTED"
    print(f"SQLite source: {report.source_path}")
    print(f"SQLite SHA-256: {report.source_sha256}")
    print(f"Result: {mode}")
    print()
    print(
        "table | SQLite | PostgreSQL(before) | inserted | remapped-id | "
        "skipped | PostgreSQL(after) | missing"
    )
    for table_name in TABLE_ORDER:
        result = report.results[table_name]
        print(
            f"{table_name} | {result.source} | {result.target_before} | "
            f"{result.inserted} | {result.id_remapped} | {result.skipped_existing} | "
            f"{result.target_after} | {result.missing}"
        )
    total_missing = sum(result.missing for result in report.results.values())
    print()
    print(f"Missing source rows: {total_missing}")
