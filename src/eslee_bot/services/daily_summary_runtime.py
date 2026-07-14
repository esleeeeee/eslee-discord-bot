from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import discord

from eslee_bot.config import DailySummaryConfig
from eslee_bot.database.repositories import (
    DailyReportRepository,
    DailySummaryMessageRepository,
    DuplicateReportError,
)
from eslee_bot.services.daily_summary import (
    GeneratedSummary,
    SummaryStats,
    calculate_stats,
    current_day_window_utc,
    day_bounds_utc,
    parse_message_ids,
    select_summary_targets,
)
from eslee_bot.services.daily_summary_ai import GeminiSummaryProvider, SummaryProvider

if TYPE_CHECKING:
    from eslee_bot.bot import EsleeBot

logger = logging.getLogger(__name__)

BACKFILL_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class BackfillResult:
    fetched: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    excluded: int = 0
    failed: bool = False


@dataclass(frozen=True, slots=True)
class ReportRunResult:
    status: str
    detail: str


class DiscordPublishError(RuntimeError):
    def __init__(self, message_ids: list[int]) -> None:
        super().__init__("Discord report publishing failed")
        self.message_ids = message_ids


def _reply_to_message_id(message: discord.Message) -> int | None:
    reference = getattr(message, "reference", None)
    return getattr(reference, "message_id", None) if reference is not None else None


def _message_values(message: discord.Message) -> dict[str, Any]:
    return {
        "guild_id": message.guild.id,  # type: ignore[union-attr]
        "channel_id": message.channel.id,
        "message_id": message.id,
        "author_id": message.author.id,
        "author_display_name": getattr(
            message.author,
            "display_name",
            message.author.name,
        )[:100],
        "content": message.content,
        "reply_to_message_id": _reply_to_message_id(message),
        "created_at": message.created_at,
    }


class DailySummaryCollector:
    def __init__(self, bot: EsleeBot, config: DailySummaryConfig) -> None:
        self.bot = bot
        self.config = config
        self._backfill_task: asyncio.Task[BackfillResult] | None = None

    def is_target_scope(self, guild_id: int | None, channel_id: int) -> bool:
        return (
            self.config.enabled
            and guild_id == self.config.guild_id
            and channel_id == self.config.source_channel_id
        )

    def should_store(self, message: discord.Message) -> bool:
        message_type = getattr(message, "type", discord.MessageType.default)
        return bool(
            message.guild is not None
            and self.is_target_scope(message.guild.id, message.channel.id)
            and not message.author.bot
            and message.webhook_id is None
            and message_type in {discord.MessageType.default, discord.MessageType.reply}
            and message.content.strip()
        )

    async def collect(self, message: discord.Message) -> bool:
        if not self.should_store(message):
            return False
        async with self.bot.database.session_factory() as session:
            return await DailySummaryMessageRepository(session).add_if_missing(
                **_message_values(message)
            )

    async def update(self, message: discord.Message) -> bool:
        if message.guild is None or not self.is_target_scope(message.guild.id, message.channel.id):
            return False
        async with self.bot.database.session_factory() as session:
            repository = DailySummaryMessageRepository(session)
            if not self.should_store(message):
                return await repository.delete(
                    message.id,
                    message.guild.id,
                    message.channel.id,
                )
            updated = await repository.update_or_delete(
                message_id=message.id,
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                content=message.content,
                author_display_name=getattr(
                    message.author,
                    "display_name",
                    message.author.name,
                )[:100],
                reply_to_message_id=_reply_to_message_id(message),
            )
            if updated:
                return True
            return await repository.add_if_missing(**_message_values(message))

    async def delete(self, message_id: int, guild_id: int, channel_id: int) -> bool:
        if not self.is_target_scope(guild_id, channel_id):
            return False
        async with self.bot.database.session_factory() as session:
            return await DailySummaryMessageRepository(session).delete(
                message_id,
                guild_id,
                channel_id,
            )

    async def delete_many(
        self,
        message_ids: set[int],
        guild_id: int,
        channel_id: int,
    ) -> int:
        if not self.is_target_scope(guild_id, channel_id):
            return 0
        async with self.bot.database.session_factory() as session:
            return await DailySummaryMessageRepository(session).delete_many(
                message_ids,
                guild_id,
                channel_id,
            )

    def start_backfill(self) -> asyncio.Task[BackfillResult] | None:
        if not self.config.enabled:
            return None
        if self._backfill_task is not None and not self._backfill_task.done():
            return self._backfill_task
        self._backfill_task = asyncio.create_task(
            self.run_backfill(),
            name="daily-summary-startup-backfill",
        )
        return self._backfill_task

    async def stop(self) -> None:
        if self._backfill_task is None or self._backfill_task.done():
            return
        self._backfill_task.cancel()
        try:
            await self._backfill_task
        except asyncio.CancelledError:
            pass

    async def run_backfill(self, *, now: datetime | None = None) -> BackfillResult:
        started_at = datetime.now(UTC)
        fetched = inserted = skipped = excluded = 0
        try:
            channel = await self._resolve_source_channel()
            current = now or datetime.now(UTC)
            timezone = cast(Any, self.config.timezone)
            window_start, window_end = current_day_window_utc(current, timezone)
            logger.info(
                "Daily summary startup backfill started (window_start=%s window_end=%s)",
                window_start.isoformat(),
                window_end.isoformat(),
            )
            batch: list[dict[str, Any]] = []
            history = channel.history(
                limit=None,
                after=window_start - timedelta(milliseconds=1),
                before=window_end + timedelta(milliseconds=1),
                oldest_first=True,
            )
            async for message in history:
                fetched += 1
                created_at = message.created_at.astimezone(UTC)
                if (
                    created_at < window_start
                    or created_at > window_end
                    or not self.should_store(message)
                ):
                    excluded += 1
                    continue
                batch.append(_message_values(message))
                if len(batch) >= BACKFILL_BATCH_SIZE:
                    added, existing = await self._flush_batch(batch)
                    inserted += added
                    skipped += existing
                    batch.clear()
            added, existing = await self._flush_batch(batch)
            inserted += added
            skipped += existing
        except asyncio.CancelledError:
            raise
        except (discord.Forbidden, discord.HTTPException) as error:
            logger.warning(
                "Daily summary startup backfill could not read Discord history (error_type=%s)",
                type(error).__name__,
            )
            return BackfillResult(
                fetched=fetched,
                inserted=inserted,
                skipped_existing=skipped,
                excluded=excluded,
                failed=True,
            )
        except Exception as error:
            logger.error(
                "Daily summary startup backfill failed safely (error_type=%s)",
                type(error).__name__,
            )
            return BackfillResult(
                fetched=fetched,
                inserted=inserted,
                skipped_existing=skipped,
                excluded=excluded,
                failed=True,
            )
        finally:
            finished_at = datetime.now(UTC)
            logger.info(
                "Daily summary startup backfill finished "
                "(fetched=%s inserted=%s skipped=%s excluded=%s started_at=%s finished_at=%s)",
                fetched,
                inserted,
                skipped,
                excluded,
                started_at.isoformat(),
                finished_at.isoformat(),
            )
        return BackfillResult(
            fetched=fetched,
            inserted=inserted,
            skipped_existing=skipped,
            excluded=excluded,
        )

    async def _resolve_source_channel(self) -> discord.TextChannel | discord.Thread:
        channel_id = cast(int, self.config.source_channel_id)
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise TypeError("Daily summary source channel does not support message history")
        if channel.guild.id != self.config.guild_id:
            raise ValueError("Daily summary source channel belongs to another guild")
        return channel

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> tuple[int, int]:
        if not batch:
            return 0, 0
        async with self.bot.database.session_factory() as session:
            return await DailySummaryMessageRepository(session).add_many(batch)


def build_report_embeds(
    report_date: date,
    stats: SummaryStats,
    generated: GeneratedSummary,
    display_names: dict[int, str],
) -> list[discord.Embed]:
    overview = discord.Embed(
        title=f"📅 {report_date.year}년 {report_date.month}월 {report_date.day}일 서버 일일 리포트",
        description=f"📝 **오늘의 하루 요약**\n\n{generated.daily_summary[:3900]}",
        color=discord.Color.blurple(),
    )
    overview.add_field(name="💬 총 메시지", value=f"{stats.message_count:,}개", inline=True)
    overview.add_field(name="👥 참여자", value=f"{stats.participant_count:,}명", inline=True)
    overview.add_field(
        name="🔥 가장 활발한 시간대",
        value=f"{stats.busiest_hour:02d}:00~{(stats.busiest_hour + 1) % 24:02d}:00",
        inline=True,
    )
    overview.add_field(
        name="🏆 가장 많이 말한 사람",
        value=f"{stats.top_user_display_name} ({stats.top_user_message_count:,}개)",
        inline=False,
    )

    entries = [
        f"**{display_names.get(item.user_id, str(item.user_id))}**\n→ {item.summary}"
        for item in generated.user_summaries
    ]
    chunks: list[str] = []
    current = ""
    for entry in entries:
        candidate = f"{current}\n\n{entry}" if current else entry
        if current and len(candidate) > 3900:
            chunks.append(current)
            current = entry
        else:
            current = candidate
    if current:
        chunks.append(current)
    if not chunks:
        chunks.append("개인 요약 기준을 충족한 사용자가 없습니다.")

    embeds = [overview]
    for index, chunk in enumerate(chunks):
        title = "👥 오늘 사람들은 뭐했을까?"
        if index:
            title += f" ({index + 1})"
        embeds.append(discord.Embed(title=title, description=chunk, color=discord.Color.green()))
    return embeds


class DailyReportPublisher:
    def __init__(self, bot: EsleeBot, config: DailySummaryConfig) -> None:
        self.bot = bot
        self.config = config

    async def publish(
        self, embeds: list[discord.Embed], existing_message_ids: list[int]
    ) -> list[int]:
        channel = await self._resolve_report_channel()
        published_ids: list[int] = []
        try:
            for index, embed in enumerate(embeds):
                existing_id = (
                    existing_message_ids[index] if index < len(existing_message_ids) else None
                )
                message = None
                if existing_id is not None:
                    try:
                        message = await channel.fetch_message(existing_id)
                    except discord.NotFound:
                        message = None
                if message is None:
                    message = await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                else:
                    await message.edit(embed=embed, content=None)
                published_ids.append(message.id)

            for stale_id in existing_message_ids[len(embeds) :]:
                try:
                    stale = await channel.fetch_message(stale_id)
                    await stale.delete()
                except discord.NotFound:
                    pass
        except (discord.Forbidden, discord.HTTPException):
            tracked_ids = published_ids + existing_message_ids[len(published_ids) :]
            raise DiscordPublishError(tracked_ids) from None
        return published_ids

    async def _resolve_report_channel(self) -> discord.TextChannel | discord.Thread:
        channel_id = cast(int, self.config.report_channel_id)
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise TypeError("Daily summary report channel is not a text channel")
        if channel.guild.id != self.config.guild_id:
            raise ValueError("Daily summary report channel belongs to another guild")
        return channel


class DailyReportService:
    def __init__(
        self,
        bot: EsleeBot,
        config: DailySummaryConfig,
        provider: SummaryProvider,
        publisher: DailyReportPublisher,
    ) -> None:
        self.bot = bot
        self.config = config
        self.provider = provider
        self.publisher = publisher
        self._locks: dict[date, asyncio.Lock] = {}

    async def generate(
        self,
        report_date: date,
        *,
        regenerate: bool = False,
        preview: bool = False,
        replace_preview: bool = False,
    ) -> ReportRunResult:
        lock = self._locks.setdefault(report_date, asyncio.Lock())
        if lock.locked():
            return ReportRunResult("duplicate", "같은 날짜의 리포트가 이미 생성 중입니다.")
        async with lock:
            return await self._generate_locked(
                report_date,
                regenerate=regenerate,
                preview=preview,
                replace_preview=replace_preview,
            )

    async def _generate_locked(
        self,
        report_date: date,
        *,
        regenerate: bool,
        preview: bool,
        replace_preview: bool,
    ) -> ReportRunResult:
        guild_id = cast(int, self.config.guild_id)
        source_channel_id = cast(int, self.config.source_channel_id)
        report_channel_id = cast(int, self.config.report_channel_id)
        timezone = cast(Any, self.config.timezone)
        async with self.bot.database.session_factory() as session:
            reports = DailyReportRepository(session)
            try:
                report = await reports.claim(
                    guild_id=guild_id,
                    report_date=report_date,
                    source_channel_id=source_channel_id,
                    report_channel_id=report_channel_id,
                    regenerate=regenerate,
                    replace_preview=replace_preview,
                )
            except DuplicateReportError as error:
                return ReportRunResult("duplicate", str(error))
            existing_ids = parse_message_ids(report.discord_message_ids_json)

        start, end = day_bounds_utc(report_date, timezone)
        async with self.bot.database.session_factory() as session:
            messages = await DailySummaryMessageRepository(session).list_between(
                guild_id,
                source_channel_id,
                start,
                end,
            )
        participant_count = len({message.author_id for message in messages})
        if (
            len(messages) < self.config.min_total_messages
            or participant_count < self.config.min_participants
        ):
            reason = "Minimum message or participant threshold was not met"
            async with self.bot.database.session_factory() as session:
                stored = await DailyReportRepository(session).get(guild_id, report_date)
                if stored is not None:
                    await DailyReportRepository(session).mark_skipped(
                        stored,
                        message_count=len(messages),
                        participant_count=participant_count,
                        reason=reason,
                        preview=preview,
                    )
            logger.info(
                "Daily report skipped (date=%s messages=%s participants=%s)",
                report_date,
                len(messages),
                participant_count,
            )
            return ReportRunResult("skipped", "메시지 또는 참여자 수가 기준보다 적습니다.")

        stats = calculate_stats(messages, timezone)
        targets = select_summary_targets(
            messages,
            min_messages=self.config.min_user_messages,
            max_users=self.config.max_users,
        )
        published_ids: list[int] | None = None
        try:
            generated = await self.provider.summarize(messages, targets, timezone=timezone)
            display_names = {target.user_id: target.display_name for target in targets}
            embeds = build_report_embeds(report_date, stats, generated, display_names)
            published_ids = await self.publisher.publish(embeds, existing_ids)
            async with self.bot.database.session_factory() as session:
                stored = await DailyReportRepository(session).get(guild_id, report_date)
                if stored is None:
                    raise RuntimeError("Claimed daily report disappeared")
                await DailyReportRepository(session).mark_completed(
                    stored,
                    message_count=stats.message_count,
                    participant_count=stats.participant_count,
                    busiest_hour=stats.busiest_hour,
                    top_user_id=stats.top_user_id,
                    top_user_display_name=stats.top_user_display_name,
                    top_user_message_count=stats.top_user_message_count,
                    daily_summary=generated.daily_summary,
                    user_summaries=[
                        {"user_id": str(item.user_id), "summary": item.summary}
                        for item in generated.user_summaries
                    ],
                    discord_message_ids=published_ids,
                    preview=preview,
                )
            logger.info(
                "Daily report completed "
                "(date=%s messages=%s participants=%s api_requests=%s chunked=%s)",
                report_date,
                stats.message_count,
                stats.participant_count,
                generated.api_request_count,
                generated.used_chunk_fallback,
            )
            return ReportRunResult("completed", "일일 리포트를 생성했습니다.")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            partial_ids = error.message_ids if isinstance(error, DiscordPublishError) else None
            safe_error = _safe_error_summary(error)
            async with self.bot.database.session_factory() as session:
                stored = await DailyReportRepository(session).get(guild_id, report_date)
                if stored is not None:
                    await DailyReportRepository(session).mark_failed(
                        stored,
                        safe_error,
                        discord_message_ids=partial_ids or published_ids,
                        preview=preview,
                    )
            logger.exception("Daily report generation failed safely (date=%s)", report_date)
            return ReportRunResult("failed", "리포트 생성에 실패했습니다. 로그를 확인하세요.")


def _safe_error_summary(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, int):
        return f"{type(error).__name__} (code={code})"
    return type(error).__name__


class DailySummaryRuntime:
    def __init__(self, bot: EsleeBot, config: DailySummaryConfig) -> None:
        self.bot = bot
        self.config = config
        self.collector: DailySummaryCollector | None = None
        self.provider: GeminiSummaryProvider | None = None
        self.report_service: DailyReportService | None = None
        self.scheduler: Any | None = None
        self._warning_logged = False

        if config.enabled:
            self.collector = DailySummaryCollector(bot, config)
            self.provider = GeminiSummaryProvider(
                cast(str, config.gemini_api_key),
                config.ai_model,
            )
            publisher = DailyReportPublisher(bot, config)
            self.report_service = DailyReportService(bot, config, self.provider, publisher)
            from eslee_bot.tasks.daily_summary_scheduler import DailySummaryScheduler

            self.scheduler = DailySummaryScheduler(
                bot,
                config,
                self.report_service,
                poll_seconds=bot.settings.scheduler_poll_seconds,
            )

    def start(self) -> None:
        if self.config.requested_enabled and not self.config.enabled and not self._warning_logged:
            logger.warning(
                "Daily summary disabled because configuration is invalid: %s",
                "; ".join(self.config.validation_errors),
            )
            self._warning_logged = True
        if self.collector is not None:
            self.collector.start_backfill()
        if self.scheduler is not None:
            self.scheduler.start()

    async def stop(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        if self.collector is not None:
            await self.collector.stop()
        if self.provider is not None:
            await self.provider.close()
