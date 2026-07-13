from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from eslee_bot.database.repositories import (
    AnnouncementRepository,
    GuildSettingsRepository,
)
from eslee_bot.services.announcement_service import (
    build_reminder_embed,
    classify_content,
    content_from_message,
)
from eslee_bot.utils.message_links import make_message_jump_url
from eslee_bot.utils.time import next_future_slot, utc_now

if TYPE_CHECKING:
    from eslee_bot.bot import EsleeBot
    from eslee_bot.database.models import Announcement

logger = logging.getLogger(__name__)


class AnnouncementScheduler:
    def __init__(self, bot: EsleeBot, poll_seconds: int) -> None:
        self.bot = bot
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._tick_lock = asyncio.Lock()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="announcement-scheduler")
        logger.info("Announcement scheduler started (poll=%ss)", self.poll_seconds)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected scheduler loop failure")
            await asyncio.sleep(self.poll_seconds)

    async def tick(self) -> None:
        if self._tick_lock.locked():
            logger.warning("Skipping overlapping scheduler tick")
            return
        async with self._tick_lock:
            async with self.bot.database.session_factory() as session:
                due = await AnnouncementRepository(session).list_due(utc_now())
            for announcement in due:
                try:
                    await self._dispatch(announcement)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Announcement %s processing failed", announcement.id)

    async def send_now(
        self, announcement_id: int, guild_id: int, *, only_if_never_sent: bool = False
    ) -> bool:
        async with self._tick_lock:
            async with self.bot.database.session_factory() as session:
                announcement = await AnnouncementRepository(session).get(announcement_id, guild_id)
            if announcement is None or not announcement.enabled:
                return False
            if only_if_never_sent and announcement.last_sent_at is not None:
                return True
            return await self._dispatch(announcement)

    async def _get_channel(self, channel_id: int) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.NotFound:
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _dispatch(self, announcement: Announcement) -> bool:
        try:
            channel = await self._get_channel(announcement.channel_id)
        except discord.Forbidden:
            logger.warning("Cannot access channel for announcement %s", announcement.id)
            return False
        except discord.HTTPException:
            logger.exception("Discord API failed while resolving announcement channel")
            return False
        if channel is None or not hasattr(channel, "fetch_message"):
            await self._disable_missing_source(announcement, "공지 채널을 찾을 수 없습니다.")
            return False

        try:
            source = await channel.fetch_message(announcement.source_message_id)  # type: ignore[attr-defined]
        except discord.NotFound:
            await self._disable_missing_source(announcement, "원본 메시지가 삭제되었습니다.")
            return False
        except discord.Forbidden:
            logger.warning("Cannot access source for announcement %s", announcement.id)
            return False
        except discord.HTTPException:
            logger.exception("Discord API failed while fetching announcement %s", announcement.id)
            return False

        if announcement.reminder_message_id:
            try:
                old_reminder = await channel.fetch_message(  # type: ignore[attr-defined]
                    announcement.reminder_message_id
                )
                await old_reminder.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                logger.warning("Cannot delete old reminder for announcement %s", announcement.id)
            except discord.HTTPException:
                logger.exception(
                    "Failed to delete old reminder for announcement %s", announcement.id
                )

        content = content_from_message(source)
        jump_url = make_message_jump_url(
            announcement.guild_id, announcement.channel_id, announcement.source_message_id
        )
        embed = build_reminder_embed(content, jump_url)
        try:
            reminder = await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.Forbidden:
            logger.warning("Cannot send reminder for announcement %s", announcement.id)
            return False
        except discord.HTTPException:
            logger.exception("Failed to send reminder for announcement %s", announcement.id)
            return False

        sent_at = utc_now()
        async with self.bot.database.session_factory() as session:
            repository = AnnouncementRepository(session)
            await repository.mark_sent(
                announcement.id,
                announcement.guild_id,
                reminder_message_id=reminder.id,
                sent_at=sent_at,
                next_send_at=next_future_slot(announcement.next_send_at, sent_at),
                content_snapshot=source.content,
                announcement_type=classify_content(content).value,
            )
        logger.info("Announcement %s reminder sent", announcement.id)
        return True

    async def _disable_missing_source(self, announcement: Announcement, reason: str) -> None:
        async with self.bot.database.session_factory() as session:
            await AnnouncementRepository(session).set_disabled(
                announcement.id, announcement.guild_id
            )
            settings = await GuildSettingsRepository(session).get(announcement.guild_id)
        logger.warning("Announcement %s disabled: %s", announcement.id, reason)
        if settings is None or settings.moderation_log_channel_id is None:
            return
        try:
            channel = await self._get_channel(settings.moderation_log_channel_id)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not access announcement log channel")
            return
        if channel is None:
            return
        try:
            await channel.send(
                f"⚠️ 공지 #{announcement.id}가 자동 비활성화되었습니다: {reason}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not notify log channel about announcement %s", announcement.id)
