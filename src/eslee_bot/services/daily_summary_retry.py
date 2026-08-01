from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

# The free tier allows 20 requests per day and one Pacific quota day spans two
# Asia/Seoul report dates, so a single report may never approach that number.
AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT = 8
# A human running /하루요약 may spend a little more, but from the same counter.
MANUAL_AI_REQUEST_BUDGET_PER_REPORT = 12
MAX_AI_REQUESTS_PER_SUMMARY_RUN = MANUAL_AI_REQUEST_BUDGET_PER_REPORT
# One extra attempt only; every attempt is charged to the report budget.
TRANSIENT_ATTEMPTS_PER_REQUEST = 2
RATE_LIMIT_COOLDOWN = timedelta(minutes=10)
UNKNOWN_QUOTA_COOLDOWN = timedelta(hours=1)
TRANSIENT_FAILURE_COOLDOWN = timedelta(minutes=15)
NON_RETRYABLE_FAILURE_COOLDOWN = timedelta(days=1)
DAILY_QUOTA_RESET_BUFFER = timedelta(minutes=5)
RETRY_STATE_PREFIX = "auto_retry:"
PACIFIC = ZoneInfo("America/Los_Angeles")


class AIRetryKind(StrEnum):
    DAILY_QUOTA = "daily_quota"
    RATE_LIMIT = "rate_limit"
    UNKNOWN_QUOTA = "unknown_quota"
    TRANSIENT = "transient"
    NON_RETRYABLE = "non_retryable"
    RUN_LIMIT = "run_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"


class AIRequestFailure(RuntimeError):
    def __init__(
        self,
        kind: AIRetryKind,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.kind = kind
        self.code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Gemini request failed ({kind.value}, code={status_code})")


@dataclass(frozen=True, slots=True)
class AutomaticRetryState:
    request_count: int = 0
    kind: AIRetryKind | None = None
    retry_at: datetime | None = None
    error_summary: str | None = None


def _details_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str).lower()
    except (TypeError, ValueError):
        return str(value).lower()


def _duration_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s?\s*", value)
        return float(match.group(1)) if match else None
    if isinstance(value, dict):
        seconds = value.get("seconds", 0)
        nanos = value.get("nanos", 0)
        try:
            return max(0.0, float(seconds) + float(nanos) / 1_000_000_000)
        except (TypeError, ValueError):
            return None
    return None


def _find_retry_delay(value: Any) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold().replace("_", "") == "retrydelay":
                parsed = _duration_seconds(item)
                if parsed is not None:
                    return parsed
            nested = _find_retry_delay(item)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_retry_delay(item)
            if nested is not None:
                return nested
    return None


def classify_gemini_429(error: BaseException) -> tuple[AIRetryKind, float | None]:
    details = getattr(error, "details", None)
    text = _details_text(details if details is not None else str(error))
    compact = re.sub(r"[\s_-]+", "", text)
    retry_after = _find_retry_delay(details)

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if retry_after is None and headers is not None:
        retry_after = _duration_seconds(headers.get("retry-after"))

    daily_markers = (
        "perday",
        "requestsperday",
        "tokensperday",
        "requestperday",
        "rpd",
        "dailyquota",
    )
    if any(marker in compact for marker in daily_markers):
        return AIRetryKind.DAILY_QUOTA, retry_after

    minute_markers = (
        "perminute",
        "requestsperminute",
        "tokensperminute",
        "requestperminute",
        "rpm",
        "tpm",
    )
    if retry_after is not None or any(marker in compact for marker in minute_markers):
        return AIRetryKind.RATE_LIMIT, retry_after
    return AIRetryKind.UNKNOWN_QUOTA, None


def quota_window(now: datetime) -> date:
    """The Pacific date whose free-tier daily quota `now` draws from.

    The window resets at Pacific midnight, which lands in the afternoon in Seoul,
    so one window covers the tail of one Asia/Seoul report date and the start of
    the next.
    """
    return now.astimezone(PACIFIC).date()


def next_quota_window_start(now: datetime) -> datetime:
    """When the next free-tier quota window opens, with the usual safety buffer."""
    current = now.astimezone(UTC)
    next_day = current.astimezone(PACIFIC).date() + timedelta(days=1)
    reset = datetime.combine(next_day, time.min, tzinfo=PACIFIC)
    return (reset + DAILY_QUOTA_RESET_BUFFER).astimezone(UTC)


def next_retry_at(
    now: datetime,
    kind: AIRetryKind,
    retry_after_seconds: float | None = None,
) -> datetime:
    current = now.astimezone(UTC)
    # Both the daily quota and the per-report budget are counted per quota
    # window, so both wait for the same reset instead of a fixed cooldown.
    if kind in (AIRetryKind.DAILY_QUOTA, AIRetryKind.BUDGET_EXHAUSTED):
        return next_quota_window_start(current)
    if kind is AIRetryKind.RATE_LIMIT:
        server_delay = timedelta(seconds=max(0.0, retry_after_seconds or 0.0))
        return current + max(RATE_LIMIT_COOLDOWN, server_delay)
    if kind is AIRetryKind.UNKNOWN_QUOTA:
        return current + UNKNOWN_QUOTA_COOLDOWN
    if kind is AIRetryKind.TRANSIENT:
        return current + TRANSIENT_FAILURE_COOLDOWN
    return current + NON_RETRYABLE_FAILURE_COOLDOWN


def decode_retry_state(value: str | None) -> AutomaticRetryState:
    """Read the retry state the previous build encoded into `error_message`.

    Reports written before the dedicated columns existed keep their spent-request
    count and cooldown across the deploy instead of restarting at zero.
    """
    if not value or not value.startswith(RETRY_STATE_PREFIX):
        return AutomaticRetryState()
    try:
        payload = json.loads(value.removeprefix(RETRY_STATE_PREFIX))
        kind_value = payload.get("kind")
        retry_value = payload.get("retry_at")
        retry_at = datetime.fromisoformat(retry_value) if retry_value else None
        if retry_at is not None:
            retry_at = retry_at.astimezone(UTC)
        return AutomaticRetryState(
            request_count=max(0, int(payload.get("request_count", 0))),
            kind=AIRetryKind(kind_value) if kind_value else None,
            retry_at=retry_at,
            error_summary=payload.get("error"),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return AutomaticRetryState()
