from datetime import UTC, datetime, timedelta

from eslee_bot.services.announcement_service import (
    LONG_TEXT_THRESHOLD,
    REMINDER_PREVIEW_LIMIT,
    AnnouncementType,
    AttachmentSummary,
    MessageContent,
    build_reminder_embed,
    classify_content,
    format_poll_time_remaining,
)


def test_short_text_classification() -> None:
    assert classify_content(MessageContent("짧은 공지")) is AnnouncementType.TEXT


def test_long_text_classification() -> None:
    content = MessageContent("가" * (LONG_TEXT_THRESHOLD + 1))
    assert classify_content(content) is AnnouncementType.LONG_TEXT


def test_image_attachment_classification() -> None:
    content = MessageContent(
        "사진", (AttachmentSummary("photo.png", "image/png", "https://example.com/a.png"),)
    )
    assert classify_content(content) is AnnouncementType.IMAGE


def test_file_attachment_classification() -> None:
    content = MessageContent("파일", (AttachmentSummary("guide.pdf", "application/pdf"),))
    assert classify_content(content) is AnnouncementType.FILE


def test_mixed_attachments_classification() -> None:
    content = MessageContent(
        "자료",
        (
            AttachmentSummary("photo.jpg", "image/jpeg"),
            AttachmentSummary("guide.pdf", "application/pdf"),
        ),
    )
    assert classify_content(content) is AnnouncementType.MIXED


def test_poll_takes_precedence_over_attachments() -> None:
    content = MessageContent(
        "",
        (AttachmentSummary("photo.jpg", "image/jpeg"),),
        poll_question="언제 만날까요?",
        poll_closed=False,
    )
    assert classify_content(content) is AnnouncementType.POLL
    embed = build_reminder_embed(content, "https://discord.com/channels/1/2/3")
    assert embed.title == "📊 투표 공지 리마인드"
    assert "언제 만날까요?" in (embed.description or "")


def test_poll_reminder_displays_hours_and_minutes_remaining() -> None:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    content = MessageContent(
        "",
        poll_question="언제 만날까요?",
        poll_closed=False,
        poll_expires_at=now + timedelta(hours=25, minutes=30),
    )
    embed = build_reminder_embed(content, "https://discord.com/channels/1/2/3", now=now)
    fields = {field.name: field.value for field in embed.fields}
    assert fields["상태"] == "진행 중"
    assert fields["남은 시간"] == "25시간 30분"


def test_expired_poll_is_displayed_as_closed() -> None:
    now = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    content = MessageContent(
        "",
        poll_question="종료된 투표",
        poll_closed=False,
        poll_expires_at=now - timedelta(seconds=1),
    )
    embed = build_reminder_embed(content, "https://discord.com/channels/1/2/3", now=now)
    fields = {field.name: field.value for field in embed.fields}
    assert fields["상태"] == "종료됨"
    assert fields["남은 시간"] == "종료됨"


def test_unknown_poll_expiration_does_not_invent_remaining_time() -> None:
    assert format_poll_time_remaining(None) is None


def test_long_preview_is_truncated_to_embed_safe_length() -> None:
    content = MessageContent("가" * 2000)
    embed = build_reminder_embed(content, "https://discord.com/channels/1/2/3")
    assert embed.description is not None
    assert len(embed.description) <= REMINDER_PREVIEW_LIMIT
    assert embed.description.endswith("…")
