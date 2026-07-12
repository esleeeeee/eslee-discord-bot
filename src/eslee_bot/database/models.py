from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GuildSettings(TimestampMixin, Base):
    __tablename__ = "guild_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    moderation_log_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Announcement(TimestampMixin, Base):
    __tablename__ = "announcements"
    __table_args__ = (
        UniqueConstraint(
            "guild_id", "channel_id", "source_message_id", name="uq_announcement_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    source_message_id: Mapped[int] = mapped_column(BigInteger)
    creator_id: Mapped[int] = mapped_column(BigInteger)
    content_snapshot: Mapped[str] = mapped_column(Text, default="")
    announcement_type: Mapped[str] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reminder_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ForbiddenWord(TimestampMixin, Base):
    __tablename__ = "forbidden_words"
    __table_args__ = (
        UniqueConstraint("guild_id", "normalized_word", name="uq_forbidden_word_guild_normalized"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    word: Mapped[str] = mapped_column(String(100))
    normalized_word: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[int] = mapped_column(BigInteger)


class ModerationViolation(TimestampMixin, Base):
    __tablename__ = "moderation_violations"

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    matched_words: Mapped[str] = mapped_column(Text)
