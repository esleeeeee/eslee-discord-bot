from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

ANNOUNCEMENT_INTERVAL = timedelta(hours=6)
KST = ZoneInfo("Asia/Seoul")


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def initial_next_send(now: datetime) -> datetime:
    return ensure_utc(now) + ANNOUNCEMENT_INTERVAL


def next_future_slot(
    previous_due: datetime, now: datetime, interval: timedelta = ANNOUNCEMENT_INTERVAL
) -> datetime:
    """Return one future slot while skipping every interval missed during downtime."""
    due = ensure_utc(previous_due)
    current = ensure_utc(now)
    if due > current:
        return due
    missed_intervals = (current - due) // interval + 1
    return due + missed_intervals * interval


def format_kst(value: datetime) -> str:
    return ensure_utc(value).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
