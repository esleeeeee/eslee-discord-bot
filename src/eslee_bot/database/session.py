from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from eslee_bot.config import normalize_database_url
from eslee_bot.database.models import Base

logger = logging.getLogger(__name__)

# Columns added after daily_reports first shipped. create_all() only creates
# missing tables, so existing production rows need an additive ALTER instead.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "daily_reports": {
        "ai_request_count": "INTEGER NOT NULL DEFAULT 0",
        "ai_request_total": "INTEGER NOT NULL DEFAULT 0",
        "ai_quota_window": "DATE",
        "ai_retry_kind": "VARCHAR(20)",
        "ai_retry_at": "TIMESTAMP WITH TIME ZONE",
        "ai_state_json": "TEXT NOT NULL DEFAULT ''",
    },
}


class Database:
    def __init__(self, url: str) -> None:
        self.url = normalize_database_url(url)
        self._ensure_sqlite_directory()
        self.engine = create_async_engine(self.url, pool_pre_ping=True)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    def _ensure_sqlite_directory(self) -> None:
        prefix = "sqlite+aiosqlite:///"
        if not self.url.startswith(prefix):
            return
        raw_path = self.url.removeprefix(prefix)
        if raw_path == ":memory:" or raw_path.startswith("file:"):
            return
        Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(self._add_missing_columns)
        logger.info("Database initialized")

    @staticmethod
    def _add_missing_columns(connection: Connection) -> None:
        inspector = inspect(connection)
        existing_tables = set(inspector.get_table_names())
        for table_name, columns in ADDED_COLUMNS.items():
            if table_name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in columns.items():
                if column_name in present:
                    continue
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
                )
                logger.info("Added missing column %s.%s", table_name, column_name)

    async def close(self) -> None:
        await self.engine.dispose()
