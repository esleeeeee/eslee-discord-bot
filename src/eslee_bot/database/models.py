from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
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


class DailySummaryMessage(Base):
    __tablename__ = "daily_summary_messages"
    __table_args__ = (
        Index(
            "ix_daily_summary_messages_scope_created",
            "guild_id",
            "channel_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    author_id: Mapped[int] = mapped_column(BigInteger, index=True)
    author_display_name: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DailyReport(Base):
    __tablename__ = "daily_reports"
    __table_args__ = (
        UniqueConstraint("guild_id", "report_date", name="uq_daily_report_guild_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    source_channel_id: Mapped[int] = mapped_column(BigInteger)
    report_channel_id: Mapped[int] = mapped_column(BigInteger)
    message_count: Mapped[int] = mapped_column(default=0)
    participant_count: Mapped[int] = mapped_column(default=0)
    busiest_hour: Mapped[int | None] = mapped_column(nullable=True)
    top_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    top_user_display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    top_user_message_count: Mapped[int] = mapped_column(default=0)
    daily_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_summaries_json: Mapped[str] = mapped_column(Text, default="[]")
    discord_message_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
