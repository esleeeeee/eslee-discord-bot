from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from eslee_bot.database.models import Announcement
from eslee_bot.database.repositories import AnnouncementRepository
from eslee_bot.utils.text import truncate_text
from eslee_bot.utils.time import ensure_utc, utc_now

LONG_TEXT_THRESHOLD = 1000
REMINDER_PREVIEW_LIMIT = 900


class AnnouncementType(StrEnum):
    TEXT = "TEXT"
    LONG_TEXT = "LONG_TEXT"
    IMAGE = "IMAGE"
    FILE = "FILE"
    POLL = "POLL"
    MIXED = "MIXED"


@dataclass(frozen=True, slots=True)
class AttachmentSummary:
    filename: str
    content_type: str | None = None
    url: str | None = None

    @property
    def is_image(self) -> bool:
        if self.content_type and self.content_type.startswith("image/"):
            return True
        return self.filename.casefold().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))


@dataclass(frozen=True, slots=True)
class MessageContent:
    text: str
    attachments: tuple[AttachmentSummary, ...] = ()
    poll_question: str | None = None
    poll_closed: bool | None = None
    poll_expires_at: datetime | None = None


def classify_content(content: MessageContent) -> AnnouncementType:
    if content.poll_question is not None:
        return AnnouncementType.POLL
    images = sum(attachment.is_image for attachment in content.attachments)
    files = len(content.attachments) - images
    if images and files:
        return AnnouncementType.MIXED
    if images:
        return AnnouncementType.IMAGE
    if files:
        return AnnouncementType.FILE
    if len(content.text) > LONG_TEXT_THRESHOLD:
        return AnnouncementType.LONG_TEXT
    return AnnouncementType.TEXT


def content_from_message(message: discord.Message) -> MessageContent:
    poll = message.poll
    return MessageContent(
        text=message.content,
        attachments=tuple(
            AttachmentSummary(
                filename=attachment.filename,
                content_type=attachment.content_type,
                url=attachment.url,
            )
            for attachment in message.attachments
        ),
        poll_question=poll.question if poll is not None else None,
        poll_closed=poll.is_finalised() if poll is not None else None,
        poll_expires_at=poll.expires_at if poll is not None else None,
    )


def format_poll_time_remaining(
    expires_at: datetime | None, *, now: datetime | None = None
) -> str | None:
    if expires_at is None:
        return None
    remaining_seconds = (ensure_utc(expires_at) - ensure_utc(now or utc_now())).total_seconds()
    if remaining_seconds <= 0:
        return "종료됨"
    total_minutes = max(1, math.ceil(remaining_seconds / 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    return f"{minutes}분"


def build_reminder_embed(
    content: MessageContent, jump_url: str, *, now: datetime | None = None
) -> discord.Embed:
    announcement_type = classify_content(content)
    if announcement_type is AnnouncementType.POLL:
        remaining = format_poll_time_remaining(content.poll_expires_at, now=now)
        closed = bool(content.poll_closed) or remaining == "종료됨"
        status = "종료됨" if closed else "진행 중"
        embed = discord.Embed(
            title="📊 투표 공지 리마인드",
            description=truncate_text(content.poll_question or "투표 공지", REMINDER_PREVIEW_LIMIT),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="상태", value=status, inline=True)
        if closed:
            embed.add_field(name="남은 시간", value="종료됨", inline=True)
        elif remaining is not None:
            embed.add_field(name="남은 시간", value=remaining, inline=True)
        embed.add_field(
            name="참여",
            value=f"[원본 투표에서 참여해 주세요.]({jump_url})",
            inline=False,
        )
        return embed

    preview = truncate_text(content.text, REMINDER_PREVIEW_LIMIT) if content.text else "(본문 없음)"
    embed = discord.Embed(
        title="📢 공지 리마인드",
        description=preview,
        color=discord.Color.gold(),
    )
    if content.attachments:
        names = ", ".join(attachment.filename for attachment in content.attachments[:5])
        if len(content.attachments) > 5:
            names += f" 외 {len(content.attachments) - 5}개"
        embed.add_field(
            name=f"첨부파일 {len(content.attachments)}개",
            value=truncate_text(names, 500),
            inline=False,
        )
        representative = next(
            (attachment for attachment in content.attachments if attachment.is_image), None
        )
        if representative is not None and representative.url:
            embed.set_image(url=representative.url)
    embed.add_field(name="원본", value=f"[원본 공지로 이동]({jump_url})", inline=False)
    return embed


class AnnouncementService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = AnnouncementRepository(session)

    async def register_message(self, message: discord.Message, creator_id: int) -> Announcement:
        if message.guild is None:
            raise ValueError("서버 메시지만 공지로 등록할 수 있습니다.")
        content = content_from_message(message)
        return await self.repository.create(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            source_message_id=message.id,
            creator_id=creator_id,
            content_snapshot=message.content,
            announcement_type=classify_content(content).value,
            enabled=True,
            next_send_at=utc_now(),
        )
