from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eslee_bot.database.models import (
    Announcement,
    ForbiddenWord,
    GuildSettings,
    ModerationViolation,
)


class DuplicateRecordError(ValueError):
    pass


class GuildSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, guild_id: int) -> GuildSettings | None:
        return await self.session.scalar(
            select(GuildSettings).where(GuildSettings.guild_id == guild_id)
        )

    async def set_log_channel(self, guild_id: int, channel_id: int | None) -> GuildSettings:
        settings = await self.get(guild_id)
        if settings is None:
            settings = GuildSettings(guild_id=guild_id, moderation_log_channel_id=channel_id)
            self.session.add(settings)
        else:
            settings.moderation_log_channel_id = channel_id
        await self.session.commit()
        await self.session.refresh(settings)
        return settings


class AnnouncementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **values: object) -> Announcement:
        announcement = Announcement(**values)
        self.session.add(announcement)
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateRecordError("Announcement source already registered") from error
        await self.session.refresh(announcement)
        return announcement

    async def get(self, announcement_id: int, guild_id: int) -> Announcement | None:
        return await self.session.scalar(
            select(Announcement).where(
                Announcement.id == announcement_id,
                Announcement.guild_id == guild_id,
            )
        )

    async def list_for_guild(self, guild_id: int) -> list[Announcement]:
        result = await self.session.scalars(
            select(Announcement)
            .where(Announcement.guild_id == guild_id, Announcement.enabled.is_(True))
            .order_by(Announcement.id)
        )
        return list(result)

    async def list_due(self, now: datetime) -> list[Announcement]:
        result = await self.session.scalars(
            select(Announcement)
            .where(Announcement.enabled.is_(True), Announcement.next_send_at <= now)
            .order_by(Announcement.next_send_at)
        )
        return list(result)

    async def delete(self, announcement_id: int, guild_id: int) -> bool:
        result = await self.session.execute(
            delete(Announcement).where(
                Announcement.id == announcement_id, Announcement.guild_id == guild_id
            )
        )
        await self.session.commit()
        return bool(result.rowcount)

    async def set_disabled(self, announcement_id: int, guild_id: int) -> None:
        announcement = await self.get(announcement_id, guild_id)
        if announcement is not None:
            announcement.enabled = False
            await self.session.commit()

    async def mark_sent(
        self,
        announcement_id: int,
        guild_id: int,
        *,
        reminder_message_id: int,
        sent_at: datetime,
        next_send_at: datetime,
        content_snapshot: str,
        announcement_type: str,
    ) -> None:
        announcement = await self.get(announcement_id, guild_id)
        if announcement is not None:
            announcement.reminder_message_id = reminder_message_id
            announcement.last_sent_at = sent_at
            announcement.next_send_at = next_send_at
            announcement.content_snapshot = content_snapshot
            announcement.announcement_type = announcement_type
            await self.session.commit()


class ForbiddenWordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self, guild_id: int, word: str, normalized_word: str, created_by: int
    ) -> ForbiddenWord:
        entry = ForbiddenWord(
            guild_id=guild_id,
            word=word,
            normalized_word=normalized_word,
            created_by=created_by,
        )
        self.session.add(entry)
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateRecordError("Forbidden word already registered") from error
        await self.session.refresh(entry)
        return entry

    async def list_for_guild(self, guild_id: int) -> list[ForbiddenWord]:
        result = await self.session.scalars(
            select(ForbiddenWord)
            .where(ForbiddenWord.guild_id == guild_id)
            .order_by(ForbiddenWord.normalized_word)
        )
        return list(result)

    async def add_many(
        self,
        guild_id: int,
        entries: list[tuple[str, str]],
        created_by: int,
    ) -> tuple[list[str], list[str]]:
        normalized_words = [normalized for _, normalized in entries]
        existing_result = await self.session.scalars(
            select(ForbiddenWord.normalized_word).where(
                ForbiddenWord.guild_id == guild_id,
                ForbiddenWord.normalized_word.in_(normalized_words),
            )
        )
        existing = set(existing_result)
        added = [word for word, normalized in entries if normalized not in existing]
        skipped = [word for word, normalized in entries if normalized in existing]
        self.session.add_all(
            ForbiddenWord(
                guild_id=guild_id,
                word=word,
                normalized_word=normalized,
                created_by=created_by,
            )
            for word, normalized in entries
            if normalized not in existing
        )
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateRecordError("A forbidden word was registered concurrently") from error
        return added, skipped

    async def delete(self, guild_id: int, normalized_word: str) -> bool:
        result = await self.session.execute(
            delete(ForbiddenWord).where(
                ForbiddenWord.guild_id == guild_id,
                ForbiddenWord.normalized_word == normalized_word,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)


class ModerationViolationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, guild_id: int, user_id: int, channel_id: int, matched_words: list[str]
    ) -> ModerationViolation:
        violation = ModerationViolation(
            guild_id=guild_id,
            user_id=user_id,
            channel_id=channel_id,
            matched_words=json.dumps(matched_words, ensure_ascii=False),
        )
        self.session.add(violation)
        await self.session.commit()
        await self.session.refresh(violation)
        return violation
