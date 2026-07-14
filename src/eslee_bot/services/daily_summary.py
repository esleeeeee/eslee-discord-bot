from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from eslee_bot.utils.time import ensure_utc

REPORT_WINDOW_START = time(6)


class SummaryMessage(Protocol):
    message_id: int
    author_id: int
    author_display_name: str
    content: str
    reply_to_message_id: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SummaryTarget:
    user_id: int
    display_name: str
    message_count: int


@dataclass(frozen=True, slots=True)
class SummaryStats:
    message_count: int
    participant_count: int
    busiest_hour: int
    top_user_id: int
    top_user_display_name: str
    top_user_message_count: int


@dataclass(frozen=True, slots=True)
class UserSummary:
    user_id: int
    summary: str


@dataclass(frozen=True, slots=True)
class GeneratedSummary:
    daily_summary: str
    user_summaries: tuple[UserSummary, ...]
    api_request_count: int
    used_chunk_fallback: bool


def day_bounds_utc(report_date: date, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    local_start = datetime.combine(report_date, REPORT_WINDOW_START, tzinfo=timezone)
    local_end = datetime.combine(
        report_date + timedelta(days=1),
        REPORT_WINDOW_START,
        tzinfo=timezone,
    )
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def current_report_date(now: datetime, timezone: ZoneInfo) -> date:
    local_now = ensure_utc(now).astimezone(timezone)
    report_date = local_now.date()
    if local_now.time().replace(tzinfo=None) < REPORT_WINDOW_START:
        report_date -= timedelta(days=1)
    return report_date


def current_day_window_utc(now: datetime, timezone: ZoneInfo) -> tuple[datetime, datetime]:
    current = ensure_utc(now)
    start, _ = day_bounds_utc(current_report_date(current, timezone), timezone)
    return start, current


def retention_cutoff_utc(now: datetime, timezone: ZoneInfo, retention_days: int) -> datetime:
    current_date = ensure_utc(now).astimezone(timezone).date()
    cutoff_date = current_date - timedelta(days=retention_days)
    cutoff, _ = day_bounds_utc(cutoff_date, timezone)
    return cutoff


def scheduled_report_date(now: datetime, timezone: ZoneInfo, run_time: time) -> date | None:
    local_now = ensure_utc(now).astimezone(timezone)
    if local_now.time().replace(tzinfo=None) < run_time:
        return None
    return local_now.date() - timedelta(days=1)


def calculate_stats(messages: list[SummaryMessage], timezone: ZoneInfo) -> SummaryStats:
    if not messages:
        raise ValueError("At least one message is required")
    author_counts = Counter(message.author_id for message in messages)
    hour_counts = Counter(
        ensure_utc(message.created_at).astimezone(timezone).hour for message in messages
    )
    first_seen: dict[int, tuple[datetime, int]] = {}
    latest_name: dict[int, str] = {}
    for message in messages:
        first_seen.setdefault(
            message.author_id,
            (ensure_utc(message.created_at), message.message_id),
        )
        latest_name[message.author_id] = message.author_display_name

    busiest_count = max(hour_counts.values())
    busiest_hour = min(hour for hour, count in hour_counts.items() if count == busiest_count)
    top_user_id = min(
        author_counts,
        key=lambda user_id: (
            -author_counts[user_id],
            first_seen[user_id],
            user_id,
        ),
    )
    return SummaryStats(
        message_count=len(messages),
        participant_count=len(author_counts),
        busiest_hour=busiest_hour,
        top_user_id=top_user_id,
        top_user_display_name=latest_name[top_user_id],
        top_user_message_count=author_counts[top_user_id],
    )


def select_summary_targets(
    messages: list[SummaryMessage], *, min_messages: int, max_users: int
) -> list[SummaryTarget]:
    counts = Counter(message.author_id for message in messages)
    first_seen: dict[int, tuple[datetime, int]] = {}
    latest_name: dict[int, str] = {}
    for message in messages:
        first_seen.setdefault(
            message.author_id,
            (ensure_utc(message.created_at), message.message_id),
        )
        latest_name[message.author_id] = message.author_display_name
    eligible = [user_id for user_id, count in counts.items() if count >= min_messages]
    eligible.sort(key=lambda user_id: (-counts[user_id], first_seen[user_id], user_id))
    return [
        SummaryTarget(
            user_id=user_id,
            display_name=latest_name[user_id],
            message_count=counts[user_id],
        )
        for user_id in eligible[:max_users]
    ]


def transcript_payload(messages: list[SummaryMessage], timezone: ZoneInfo) -> str:
    authors_by_message = {message.message_id: message.author_display_name for message in messages}
    payload: list[dict[str, str]] = []
    for message in messages:
        item = {
            "time": ensure_utc(message.created_at).astimezone(timezone).strftime("%H:%M:%S"),
            "user_id": str(message.author_id),
            "display_name": message.author_display_name,
            "content": message.content,
        }
        if message.reply_to_message_id is not None:
            item["reply_to_message_id"] = str(message.reply_to_message_id)
            reply_author = authors_by_message.get(message.reply_to_message_id)
            if reply_author is not None:
                item["reply_to_display_name"] = reply_author
        payload.append(item)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def target_payload(targets: list[SummaryTarget]) -> str:
    return json.dumps(
        [
            {
                "user_id": str(target.user_id),
                "display_name": target.display_name,
                "message_count": target.message_count,
            }
            for target in targets
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def parse_message_ids(value: str) -> list[int]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [int(item) for item in parsed if isinstance(item, int | str) and str(item).isdigit()]
