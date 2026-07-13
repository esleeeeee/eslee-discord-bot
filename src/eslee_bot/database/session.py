from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from eslee_bot.config import normalize_database_url
from eslee_bot.database.models import Base

logger = logging.getLogger(__name__)


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
        logger.info("Database initialized")

    async def close(self) -> None:
        await self.engine.dispose()
