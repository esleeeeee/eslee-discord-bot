from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from eslee_bot.database.models import (
    Announcement,
    DailyReport,
    DailySummaryMessage,
    ForbiddenWord,
    GuildSettings,
    ModerationViolation,
)
from eslee_bot.utils.time import ensure_utc


class DuplicateRecordError(ValueError):
    pass


class DuplicateReportError(ValueError):
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


class DailySummaryMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_if_missing(self, **values: Any) -> bool:
        message_id = int(values["message_id"])
        if await self.session.scalar(
            select(DailySummaryMessage.id).where(DailySummaryMessage.message_id == message_id)
        ):
            return False
        self.session.add(DailySummaryMessage(**values))
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return False
        return True

    async def add_many(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        if not rows:
            return 0, 0
        unique_rows: dict[int, dict[str, Any]] = {}
        for row in rows:
            unique_rows.setdefault(int(row["message_id"]), row)
        message_ids = list(unique_rows)
        existing = set(
            await self.session.scalars(
                select(DailySummaryMessage.message_id).where(
                    DailySummaryMessage.message_id.in_(message_ids)
                )
            )
        )
        pending = [row for message_id, row in unique_rows.items() if message_id not in existing]
        self.session.add_all(DailySummaryMessage(**row) for row in pending)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            inserted = 0
            for row in pending:
                inserted += int(await self.add_if_missing(**row))
            return inserted, len(rows) - inserted
        return len(pending), len(rows) - len(pending)

    async def update_or_delete(
        self,
        *,
        message_id: int,
        guild_id: int,
        channel_id: int,
        content: str,
        author_display_name: str,
        reply_to_message_id: int | None,
    ) -> bool:
        stored = await self.session.scalar(
            select(DailySummaryMessage).where(
                DailySummaryMessage.message_id == message_id,
                DailySummaryMessage.guild_id == guild_id,
                DailySummaryMessage.channel_id == channel_id,
            )
        )
        if stored is None:
            return False
        if not content.strip():
            await self.session.delete(stored)
        else:
            stored.content = content
            stored.author_display_name = author_display_name
            stored.reply_to_message_id = reply_to_message_id
        await self.session.commit()
        return True

    async def delete(self, message_id: int, guild_id: int, channel_id: int) -> bool:
        result = await self.session.execute(
            delete(DailySummaryMessage).where(
                DailySummaryMessage.message_id == message_id,
                DailySummaryMessage.guild_id == guild_id,
                DailySummaryMessage.channel_id == channel_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)

    async def delete_many(
        self,
        message_ids: set[int],
        guild_id: int,
        channel_id: int,
    ) -> int:
        if not message_ids:
            return 0
        result = await self.session.execute(
            delete(DailySummaryMessage).where(
                DailySummaryMessage.message_id.in_(message_ids),
                DailySummaryMessage.guild_id == guild_id,
                DailySummaryMessage.channel_id == channel_id,
            )
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    async def list_between(
        self,
        guild_id: int,
        channel_id: int,
        start: datetime,
        end: datetime,
    ) -> list[DailySummaryMessage]:
        result = await self.session.scalars(
            select(DailySummaryMessage)
            .where(
                DailySummaryMessage.guild_id == guild_id,
                DailySummaryMessage.channel_id == channel_id,
                DailySummaryMessage.created_at >= start,
                DailySummaryMessage.created_at < end,
            )
            .order_by(DailySummaryMessage.created_at, DailySummaryMessage.message_id)
        )
        return list(result)

    async def count_between(
        self,
        guild_id: int,
        channel_id: int,
        start: datetime,
        end: datetime,
    ) -> int:
        return int(
            await self.session.scalar(
                select(func.count(DailySummaryMessage.id)).where(
                    DailySummaryMessage.guild_id == guild_id,
                    DailySummaryMessage.channel_id == channel_id,
                    DailySummaryMessage.created_at >= start,
                    DailySummaryMessage.created_at < end,
                )
            )
            or 0
        )

    async def delete_before(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(DailySummaryMessage).where(DailySummaryMessage.created_at < cutoff)
        )
        await self.session.commit()
        return int(result.rowcount or 0)


class DailyReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, guild_id: int, report_date: date) -> DailyReport | None:
        return await self.session.scalar(
            select(DailyReport).where(
                DailyReport.guild_id == guild_id,
                DailyReport.report_date == report_date,
            )
        )

    async def latest(self, guild_id: int) -> DailyReport | None:
        return await self.session.scalar(
            select(DailyReport)
            .where(DailyReport.guild_id == guild_id)
            .order_by(DailyReport.report_date.desc())
            .limit(1)
        )

    async def claim(
        self,
        *,
        guild_id: int,
        report_date: date,
        source_channel_id: int,
        report_channel_id: int,
        regenerate: bool,
        replace_preview: bool = False,
        replace_if_updated_before: datetime | None = None,
    ) -> DailyReport:
        report = await self.get(guild_id, report_date)
        if report is None:
            report = DailyReport(
                guild_id=guild_id,
                report_date=report_date,
                source_channel_id=source_channel_id,
                report_channel_id=report_channel_id,
                status="generating",
            )
            self.session.add(report)
            try:
                await self.session.commit()
            except IntegrityError as error:
                await self.session.rollback()
                raise DuplicateReportError("Daily report is already being generated") from error
            await self.session.refresh(report)
            return report
        replace_outdated_final = bool(
            replace_preview
            and replace_if_updated_before is not None
            and report.status in {"completed", "skipped", "failed"}
            and ensure_utc(report.updated_at) < replace_if_updated_before
        )
        if not regenerate and not (
            (replace_preview and report.status.startswith("preview_"))
            or replace_outdated_final
        ):
            raise DuplicateReportError(f"Daily report already has status {report.status}")
        report.status = "generating"
        report.error_message = None
        await self.session.commit()
        await self.session.refresh(report)
        return report

    async def save_ai_progress(
        self,
        report: DailyReport,
        *,
        request_count: int,
        total_requests: int,
        quota_window: date,
        state_json: str,
    ) -> None:
        """Persist the quota-window counter and chunk checkpoint mid-run."""
        report.ai_request_count = request_count
        report.ai_request_total = total_requests
        report.ai_quota_window = quota_window
        report.ai_state_json = state_json
        await self.session.commit()

    async def save_ai_failure(
        self,
        report: DailyReport,
        *,
        request_count: int,
        total_requests: int,
        quota_window: date,
        state_json: str,
        retry_kind: str | None,
        retry_at: datetime | None,
    ) -> None:
        report.ai_request_count = request_count
        report.ai_request_total = total_requests
        report.ai_quota_window = quota_window
        report.ai_state_json = state_json
        report.ai_retry_kind = retry_kind
        report.ai_retry_at = retry_at
        await self.session.commit()

    async def quota_blocked_until(self, guild_id: int, now: datetime) -> datetime | None:
        """Latest daily-quota cooldown across every report of this guild.

        One Pacific quota day spans two Asia/Seoul report dates, so a daily-quota
        429 on one date must also hold back the next date's automatic run.
        """
        blocked = await self.session.scalar(
            select(func.max(DailyReport.ai_retry_at)).where(
                DailyReport.guild_id == guild_id,
                DailyReport.ai_retry_kind == "daily_quota",
                DailyReport.ai_retry_at.is_not(None),
            )
        )
        if blocked is None:
            return None
        blocked = ensure_utc(blocked)
        return blocked if blocked > ensure_utc(now) else None

    async def mark_skipped(
        self,
        report: DailyReport,
        *,
        message_count: int,
        participant_count: int,
        reason: str,
        preview: bool = False,
    ) -> None:
        report.message_count = message_count
        report.participant_count = participant_count
        report.status = "preview_skipped" if preview else "skipped"
        report.error_message = reason
        self._clear_ai_state(report)
        await self.session.commit()

    async def mark_completed(
        self,
        report: DailyReport,
        *,
        message_count: int,
        participant_count: int,
        busiest_hour: int,
        top_user_id: int,
        top_user_display_name: str,
        top_user_message_count: int,
        daily_summary: str,
        user_summaries: list[dict[str, str]],
        discord_message_ids: list[int],
        preview: bool = False,
    ) -> None:
        report.message_count = message_count
        report.participant_count = participant_count
        report.busiest_hour = busiest_hour
        report.top_user_id = top_user_id
        report.top_user_display_name = top_user_display_name
        report.top_user_message_count = top_user_message_count
        report.daily_summary = daily_summary
        report.user_summaries_json = json.dumps(user_summaries, ensure_ascii=False)
        report.discord_message_ids_json = json.dumps(discord_message_ids)
        report.status = "preview_completed" if preview else "completed"
        report.error_message = None
        # The quota already spent produced a result, so the next attempt for this
        # date (a final after a preview, or a manual regenerate) starts fresh.
        self._clear_ai_state(report)
        await self.session.commit()

    @staticmethod
    def _clear_ai_state(report: DailyReport) -> None:
        """Drop the checkpoint and cooldown once a report reaches a terminal state.

        The quota-window and lifetime counters stay, so requests already spent in
        the current window still count against its budget.
        """
        report.ai_state_json = ""
        report.ai_retry_kind = None
        report.ai_retry_at = None

    async def mark_failed(
        self,
        report: DailyReport,
        error_message: str,
        *,
        discord_message_ids: list[int] | None = None,
        preview: bool = False,
    ) -> None:
        report.status = "preview_failed" if preview else "failed"
        report.error_message = error_message[:1000]
        if discord_message_ids is not None:
            report.discord_message_ids_json = json.dumps(discord_message_ids)
        await self.session.commit()
