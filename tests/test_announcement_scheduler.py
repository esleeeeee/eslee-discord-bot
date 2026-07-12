from datetime import UTC, datetime, timedelta

from eslee_bot.utils.message_links import make_message_jump_url
from eslee_bot.utils.time import (
    ANNOUNCEMENT_INTERVAL,
    initial_next_send,
    next_future_slot,
)


def test_initial_next_send_is_exactly_six_hours() -> None:
    now = datetime(2026, 7, 12, 3, 0, tzinfo=UTC)
    assert initial_next_send(now) == now + timedelta(hours=6)
    assert ANNOUNCEMENT_INTERVAL == timedelta(hours=6)


def test_due_slot_advances_once_to_the_next_interval() -> None:
    due = datetime(2026, 7, 12, 3, 0, tzinfo=UTC)
    now = due + timedelta(minutes=1)
    assert next_future_slot(due, now) == due + timedelta(hours=6)


def test_long_downtime_skips_missed_slots_and_returns_one_future_slot() -> None:
    due = datetime(2026, 7, 10, 3, 0, tzinfo=UTC)
    now = datetime(2026, 7, 12, 4, 0, tzinfo=UTC)
    result = next_future_slot(due, now)
    assert result > now
    assert result <= now + ANNOUNCEMENT_INTERVAL
    assert (result - due) % ANNOUNCEMENT_INTERVAL == timedelta(0)


def test_future_due_time_is_not_changed() -> None:
    now = datetime(2026, 7, 12, 3, 0, tzinfo=UTC)
    due = now + timedelta(hours=2)
    assert next_future_slot(due, now) == due


def test_source_jump_url() -> None:
    assert make_message_jump_url(1, 2, 3) == "https://discord.com/channels/1/2/3"
