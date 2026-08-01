"""Gemini request budgeting, chunk checkpoints and quota cooldowns.

The free tier allows 20 requests per day and one Pacific quota day covers two
Asia/Seoul report dates, so these tests pin the number of Gemini requests a
report may ever spend.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from google.genai import errors

from eslee_bot.database import Database
from eslee_bot.database.repositories import (
    DailyReportRepository,
    DailySummaryMessageRepository,
)
from eslee_bot.services.daily_summary import day_bounds_utc, select_summary_targets
from eslee_bot.services.daily_summary_ai import (
    PROMPT_OVERHEAD_CHARS,
    AISummaryResponse,
    AIUserSummary,
    ChunkSummaryResponse,
    GeminiSummaryProvider,
)
from eslee_bot.services.daily_summary_plan import (
    FREE_TIER_RPD,
    CheckpointEntry,
    RequestPacer,
    SummaryCheckpoint,
    decode_checkpoint,
    encode_checkpoint,
    estimate_tokens,
)
from eslee_bot.services.daily_summary_retry import (
    AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT,
    AIRetryKind,
    next_quota_window_start,
    quota_window,
)
from eslee_bot.services.daily_summary_runtime import DailyReportService
from eslee_bot.tasks.daily_summary_scheduler import DailySummaryScheduler
from test_daily_summary_reports import (
    FailingPublisher,
    FakeBot,
    FakePublisher,
    insert_messages,
    summary_config,
)

KST = ZoneInfo("Asia/Seoul")
REPORT_DATE = date(2026, 7, 29)
RUN_TIME = datetime(2026, 7, 30, 6, 2, tzinfo=KST).astimezone(UTC)


def gemini_client(generate: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )


def final_response() -> SimpleNamespace:
    return SimpleNamespace(
        parsed=AISummaryResponse(
            daily_summary="하루 요약",
            user_summaries=[
                AIUserSummary(user_id="10", summary="사용자 10 요약"),
                AIUserSummary(user_id="20", summary="사용자 20 요약"),
            ],
        ),
        text=None,
    )


def chunk_response(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        parsed=ChunkSummaryResponse(
            summary=f"구간 {index} 요약",
            user_observations=[AIUserSummary(user_id="10", summary="관찰")],
        ),
        text=None,
    )


def build_provider(generate: AsyncMock, *, max_chars: int | None = None) -> GeminiSummaryProvider:
    kwargs = {} if max_chars is None else {"max_request_input_chars": max_chars}
    return GeminiSummaryProvider(
        "test",
        "gemini-test",
        client=gemini_client(generate),
        sleep=AsyncMock(),
        jitter=lambda: 0,
        **kwargs,  # type: ignore[arg-type]
    )


def build_service(
    database: Database,
    provider: GeminiSummaryProvider,
    publisher: object | None = None,
) -> DailyReportService:
    return DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher or FakePublisher(),  # type: ignore[arg-type]
        pacer_factory=lambda: RequestPacer(sleep=AsyncMock(), monotonic=lambda: 0.0),
    )


async def stored_report(database: Database):  # type: ignore[no-untyped-def]
    async with database.session_factory() as session:
        return await DailyReportRepository(session).get(100, REPORT_DATE)


def chunk_prompt_indexes(generate: AsyncMock) -> list[str]:
    """The '청크 i/n' marker of every chunk request that was actually sent."""
    markers = []
    for call in generate.await_args_list:
        contents = call.kwargs["contents"]
        if "청크 " in contents:
            markers.append(contents.split("청크 ", 1)[1].split("다.", 1)[0].strip())
    return markers


async def planned_chunk_count(
    database: Database,
    report_date: date,
    max_chars: int,
) -> int:
    """How many chunks the real planner produces for the stored messages."""
    start, end = day_bounds_utc(report_date, KST)
    async with database.session_factory() as session:
        messages = await DailySummaryMessageRepository(session).list_between(100, 200, start, end)
    targets = select_summary_targets(messages, min_messages=3, max_users=20)
    provider = build_provider(AsyncMock(), max_chars=max_chars)
    return provider.plan(messages, targets, timezone=KST).chunk_count


@pytest.mark.asyncio
async def test_a_short_day_still_costs_exactly_one_gemini_request() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=[final_response()])
    service = build_service(database, build_provider(generate))
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)

        result = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)

        assert result.status == "completed"
        assert generate.await_count == 1
        report = await stored_report(database)
        assert report is not None
        assert report.status == "completed"
        # The request still counts against this quota window's budget.
        assert report.ai_request_count == 1
        assert report.ai_request_total == 1
        assert report.ai_quota_window == quota_window(RUN_TIME)
        assert report.ai_state_json == ""
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_failing_report_never_exceeds_the_automatic_budget() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=errors.APIError(503, {"message": "unavailable"}))
    service = build_service(database, build_provider(generate))
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        service,
        poll_seconds=60,
    )
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)

        for attempt in range(40):
            await scheduler.tick(now=RUN_TIME + timedelta(minutes=15 * attempt))

        assert generate.await_count == AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT
        assert generate.await_count < FREE_TIER_RPD
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_long_day_is_chunked_and_planned_within_the_budget() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    max_chars = PROMPT_OVERHEAD_CHARS + 400
    try:
        await insert_messages(database, REPORT_DATE, {10: 8, 20: 8}, first_message_id=1)
        chunks = await planned_chunk_count(database, REPORT_DATE, max_chars)
        generate = AsyncMock(
            side_effect=[chunk_response(index) for index in range(chunks)]
            + [final_response()]
        )
        service = build_service(database, build_provider(generate, max_chars=max_chars))

        result = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)

        assert result.status == "completed"
        assert chunks > 1
        assert len(chunk_prompt_indexes(generate)) == chunks
        # Every chunk request plus exactly one final consolidation.
        assert generate.await_count == chunks + 1
        assert generate.await_count <= AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_plan_larger_than_the_budget_spends_no_request_at_all() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=final_response)
    # One message per chunk makes the plan far larger than the budget.
    provider = build_provider(generate, max_chars=PROMPT_OVERHEAD_CHARS + 1)
    service = build_service(database, provider)
    try:
        await insert_messages(database, REPORT_DATE, {10: 10, 20: 10}, first_message_id=1)

        result = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)

        assert result.status == "failed"
        assert result.retry_kind is AIRetryKind.BUDGET_EXHAUSTED
        generate.assert_not_awaited()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_chunks_that_already_succeeded_are_never_requested_again() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    unavailable = errors.APIError(503, {"message": "unavailable"})
    max_chars = PROMPT_OVERHEAD_CHARS + 300
    try:
        await insert_messages(database, REPORT_DATE, {10: 6, 20: 6}, first_message_id=1)
        chunks = await planned_chunk_count(database, REPORT_DATE, max_chars)
        assert chunks >= 3
        generate = AsyncMock(
            side_effect=[
                *[chunk_response(index) for index in range(chunks - 1)],
                unavailable,  # the last chunk fails, and its one in-run retry too
                unavailable,
                chunk_response(chunks),  # the second run resumes exactly here
                final_response(),
            ]
        )
        service = build_service(database, build_provider(generate, max_chars=max_chars))

        first = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)
        assert first.status == "failed"
        after_failure = await stored_report(database)
        assert after_failure is not None
        checkpoint = decode_checkpoint(after_failure.ai_state_json)
        assert checkpoint.completed_chunk_count == chunks - 1
        assert after_failure.ai_request_count == chunks + 1

        second = await service.generate(
            REPORT_DATE,
            regenerate=True,
            automatic=True,
            now=RUN_TIME + timedelta(minutes=20),
        )

        assert second.status == "completed"
        # Every earlier chunk was requested once in total, not once per run.
        markers = chunk_prompt_indexes(generate)
        for index in range(1, chunks):
            assert markers.count(f"{index}/{chunks}") == 1
        assert generate.await_count == chunks + 3
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_failed_final_consolidation_retries_only_the_consolidation() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    unavailable = errors.APIError(503, {"message": "unavailable"})
    max_chars = PROMPT_OVERHEAD_CHARS + 700
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)
        chunks = await planned_chunk_count(database, REPORT_DATE, max_chars)
        assert chunks > 1
        generate = AsyncMock(
            side_effect=[
                *[chunk_response(index) for index in range(chunks)],
                unavailable,  # the consolidation fails, and its one retry too
                unavailable,
                final_response(),  # the second run only redoes the consolidation
            ]
        )
        service = build_service(database, build_provider(generate, max_chars=max_chars))

        first = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)
        assert first.status == "failed"
        after_failure = await stored_report(database)
        assert after_failure is not None
        assert decode_checkpoint(after_failure.ai_state_json).stage == "final"

        second = await service.generate(
            REPORT_DATE,
            regenerate=True,
            automatic=True,
            now=RUN_TIME + timedelta(minutes=20),
        )

        assert second.status == "completed"
        # No chunk was recomputed just because the consolidation failed.
        assert len(chunk_prompt_indexes(generate)) == chunks
        assert generate.await_count == chunks + 3
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_discord_publish_failure_reuses_the_summary_without_calling_gemini() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=[final_response()])
    provider = build_provider(generate)
    service = build_service(database, provider, FailingPublisher())
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)

        failed = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)
        assert failed.status == "failed"
        assert generate.await_count == 1

        service.publisher = FakePublisher()  # type: ignore[assignment]
        recovered = await service.generate(
            REPORT_DATE,
            regenerate=True,
            now=RUN_TIME + timedelta(minutes=20),
        )

        assert recovered.status == "completed"
        # The stored final summary was reused instead of a second Gemini call.
        assert generate.await_count == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_checkpoint_survives_a_process_restart() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    unavailable = errors.APIError(503, {"message": "unavailable"})
    max_chars = PROMPT_OVERHEAD_CHARS + 700
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)
        chunks = await planned_chunk_count(database, REPORT_DATE, max_chars)
        assert chunks == 2
        generate = AsyncMock(
            side_effect=[
                chunk_response(1),
                unavailable,
                unavailable,
                chunk_response(2),
                final_response(),
            ]
        )
        provider = build_provider(generate, max_chars=max_chars)
        service = build_service(database, provider)
        first = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)
        assert first.status == "failed"

        # A restart keeps only what the database holds.
        restarted = build_service(database, provider)
        result = await restarted.generate(
            REPORT_DATE,
            regenerate=True,
            automatic=True,
            now=RUN_TIME + timedelta(minutes=20),
        )

        assert result.status == "completed"
        assert chunk_prompt_indexes(generate).count("1/2") == 1
        assert generate.await_count == 5
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_daily_quota_429_also_holds_back_the_next_report_date() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    daily_quota = errors.APIError(
        429,
        {
            "error": {
                "details": [
                    {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}
                ]
            }
        },
    )
    generate = AsyncMock(side_effect=daily_quota)
    service = build_service(database, build_provider(generate))
    next_date = REPORT_DATE + timedelta(days=1)
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)
        await insert_messages(database, next_date, {10: 5, 20: 5}, first_message_id=100)

        exhausted = await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)
        assert exhausted.retry_kind is AIRetryKind.DAILY_QUOTA
        assert generate.await_count == 1

        # Before the Pacific reset no report date may spend another request,
        # because one Pacific quota day covers two Asia/Seoul report dates.
        assert exhausted.retry_at is not None
        blocked = await service.generate(
            next_date,
            automatic=True,
            now=exhausted.retry_at - timedelta(minutes=1),
        )

        assert blocked.status == "cooldown"
        assert blocked.retry_kind is AIRetryKind.DAILY_QUOTA
        assert generate.await_count == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_manual_and_automatic_runs_share_one_persisted_counter() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=errors.APIError(503, {"message": "unavailable"}))
    service = build_service(database, build_provider(generate))
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)

        await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)
        assert generate.await_count == 2
        manual = await service.generate(REPORT_DATE, regenerate=True, now=RUN_TIME)

        assert manual.status == "failed"
        report = await stored_report(database)
        assert report is not None
        # The manual run continued the same counter instead of restarting at zero.
        assert report.ai_request_count == 4
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_status_diagnostics_expose_quota_values_without_message_content() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=errors.APIError(503, {"message": "unavailable"}))
    service = build_service(database, build_provider(generate))
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)
        await service.generate(REPORT_DATE, automatic=True, now=RUN_TIME)

        diagnostics = await service.diagnostics(REPORT_DATE, now=RUN_TIME)

        assert diagnostics.message_count == 10
        assert diagnostics.estimated_input_tokens > 0
        assert diagnostics.planned_chunks == 1
        assert diagnostics.completed_chunks == 0
        assert diagnostics.ai_request_count == 2
        assert diagnostics.ai_request_limit == AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT
        assert diagnostics.ai_request_total == 2
        assert diagnostics.quota_window == quota_window(RUN_TIME)
        assert diagnostics.retry_kind == AIRetryKind.TRANSIENT.value
        assert diagnostics.retry_at is not None
    finally:
        await database.close()


# The report that was stuck in production: written by the previous build with a
# 22-request counter, failed, and waiting on a daily-quota cooldown.
STUCK_DATE = date(2026, 7, 31)
STUCK_LEGACY_STATE = (
    'auto_retry:{"error":"APIError (code=429)","kind":"daily_quota",'
    '"request_count":22,"retry_at":"2026-08-01T07:05:00+00:00"}'
)
# 2026-07-31 14:10 Pacific, the window the 22 requests were actually spent in.
STUCK_LAST_WRITE = datetime(2026, 7, 31, 21, 10, tzinfo=UTC)
QUOTA_RESET = datetime(2026, 8, 1, 7, 5, tzinfo=UTC)


async def seed_stuck_legacy_report(database: Database) -> None:
    await insert_messages(database, STUCK_DATE, {10: 5, 20: 5}, first_message_id=1)
    async with database.session_factory() as session:
        repository = DailyReportRepository(session)
        report = await repository.claim(
            guild_id=100,
            report_date=STUCK_DATE,
            source_channel_id=200,
            report_channel_id=300,
            regenerate=False,
        )
        await repository.mark_failed(report, STUCK_LEGACY_STATE)
    # updated_at is server-generated, so pin it to the window that spent the 22.
    async with database.engine.begin() as connection:
        await connection.exec_driver_sql(
            "UPDATE daily_reports SET updated_at = ? WHERE report_date = ?",
            (STUCK_LAST_WRITE.isoformat(sep=" "), STUCK_DATE.isoformat()),
        )


async def stuck_report(database: Database):  # type: ignore[no-untyped-def]
    async with database.session_factory() as session:
        return await DailyReportRepository(session).get(100, STUCK_DATE)


@pytest.mark.asyncio
async def test_a_legacy_counter_blocks_only_inside_its_own_quota_window() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=[final_response()])
    service = build_service(database, build_provider(generate))
    try:
        await seed_stuck_legacy_report(database)

        # Same Pacific window as the 22 requests: the budget really is spent.
        blocked = await service.generate(
            STUCK_DATE,
            replace_preview=True,
            recover_incomplete=True,
            automatic=True,
            now=STUCK_LAST_WRITE + timedelta(minutes=30),
        )

        assert blocked.status == "limit_reached"
        assert blocked.automatic_ai_requests == 22
        assert blocked.retry_kind is AIRetryKind.BUDGET_EXHAUSTED
        # It waits for the quota reset instead of blocking the report for good.
        assert blocked.retry_at == QUOTA_RESET
        generate.assert_not_awaited()
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_stuck_legacy_report_catches_up_once_the_quota_window_rolls_over() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=[final_response()])
    publisher = FakePublisher()
    service = build_service(database, build_provider(generate), publisher)
    try:
        await seed_stuck_legacy_report(database)

        # Still inside the daily-quota cooldown: nothing is sent.
        waiting = await service.generate(
            STUCK_DATE,
            replace_preview=True,
            recover_incomplete=True,
            automatic=True,
            now=QUOTA_RESET - timedelta(minutes=1),
        )
        assert waiting.status == "cooldown"
        generate.assert_not_awaited()

        # The window has rolled over, so the catch-up is allowed exactly once.
        caught_up = await service.generate(
            STUCK_DATE,
            replace_preview=True,
            recover_incomplete=True,
            automatic=True,
            now=QUOTA_RESET + timedelta(minutes=1),
        )

        assert caught_up.status == "completed"
        assert generate.await_count == 1
        assert len(publisher.calls) == 1
        report = await stuck_report(database)
        assert report is not None
        assert report.status == "completed"
        # The new window counts from zero...
        assert report.ai_request_count == 1
        assert report.ai_quota_window == date(2026, 8, 1)
        # ...while the 22 earlier requests stay on record for auditing.
        assert report.ai_request_total == 23
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_the_catch_up_budget_is_a_full_eight_in_the_new_window() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=errors.APIError(503, {"message": "unavailable"}))
    service = build_service(database, build_provider(generate))
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        service,
        poll_seconds=60,
    )
    try:
        await seed_stuck_legacy_report(database)
        start = QUOTA_RESET + timedelta(minutes=1)

        # Every tick stays inside the new Pacific window (it lasts 24 hours).
        for attempt in range(20):
            await scheduler.tick(now=start + timedelta(minutes=15 * attempt))

        # A fresh budget, and not one request more.
        assert generate.await_count == AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT
        report = await stuck_report(database)
        assert report is not None
        assert report.ai_request_count == AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT
        assert report.ai_request_total == 22 + AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_spent_budget_no_longer_ends_the_scheduler_day() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=[final_response()])
    service = build_service(database, build_provider(generate))
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        service,
        poll_seconds=60,
    )
    try:
        await seed_stuck_legacy_report(database)

        # 06:02 KST on 2026-08-01 is still the exhausted window, so this is a
        # no-op that must not mark the whole local day as finished.
        morning = datetime(2026, 8, 1, 6, 2, tzinfo=KST).astimezone(UTC)
        await scheduler.tick(now=morning)
        generate.assert_not_awaited()

        # 16:06 KST the same local day is the next window: the catch-up runs.
        await scheduler.tick(now=QUOTA_RESET + timedelta(minutes=1))

        assert generate.await_count == 1
        report = await stuck_report(database)
        assert report is not None
        assert report.status == "completed"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_restart_keeps_the_quota_window_basis() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=errors.APIError(503, {"message": "unavailable"}))
    provider = build_provider(generate)
    service = build_service(database, provider)
    try:
        await seed_stuck_legacy_report(database)
        start = QUOTA_RESET + timedelta(minutes=1)

        for attempt in range(4):
            await service.generate(
                STUCK_DATE,
                replace_preview=True,
                recover_incomplete=True,
                automatic=True,
                now=start + timedelta(minutes=20 * attempt),
            )
        assert generate.await_count == 8

        # A restart re-reads the window and its counter from the database only.
        restarted = build_service(database, provider)
        blocked = await restarted.generate(
            STUCK_DATE,
            replace_preview=True,
            recover_incomplete=True,
            automatic=True,
            now=start + timedelta(hours=2),
        )

        assert blocked.status == "limit_reached"
        assert blocked.automatic_ai_requests == AUTOMATIC_AI_REQUEST_BUDGET_PER_REPORT
        assert generate.await_count == 8
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_completed_report_is_not_regenerated_after_a_window_rollover() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=[final_response()])
    service = build_service(database, build_provider(generate))
    try:
        await seed_stuck_legacy_report(database)
        first = await service.generate(
            STUCK_DATE,
            replace_preview=True,
            recover_incomplete=True,
            automatic=True,
            now=QUOTA_RESET + timedelta(minutes=1),
        )
        assert first.status == "completed"

        # A later window must not re-run a report that already succeeded.
        again = await service.generate(
            STUCK_DATE,
            replace_preview=True,
            recover_incomplete=True,
            automatic=True,
            now=QUOTA_RESET + timedelta(days=1),
        )

        assert again.status == "already_completed"
        assert generate.await_count == 1
    finally:
        await database.close()


def test_quota_window_follows_the_pacific_midnight_reset() -> None:
    # 06:02 KST on 2026-08-01 is still 2026-07-31 in Pacific.
    morning = datetime(2026, 8, 1, 6, 2, tzinfo=KST).astimezone(UTC)
    assert quota_window(morning) == date(2026, 7, 31)
    # 16:06 KST the same day has crossed into the next window.
    assert quota_window(QUOTA_RESET + timedelta(minutes=1)) == date(2026, 8, 1)
    assert next_quota_window_start(morning) == QUOTA_RESET


def test_checkpoint_round_trips_through_json() -> None:
    checkpoint = SummaryCheckpoint(chunk_total=2, stage="chunk:2/2")
    checkpoint.chunks[0] = CheckpointEntry("fp", {"summary": "가", "user_observations": []})

    restored = decode_checkpoint(encode_checkpoint(checkpoint))

    assert restored.chunk_total == 2
    assert restored.stage == "chunk:2/2"
    assert restored.chunk(0, "fp") == {"summary": "가", "user_observations": []}
    assert restored.chunk(0, "other-fingerprint") is None


def test_corrupt_checkpoint_json_degrades_to_an_empty_checkpoint() -> None:
    assert decode_checkpoint("not-json").completed_chunk_count == 0
    assert decode_checkpoint(None).chunk_total == 0
    assert decode_checkpoint("").final is None


@pytest.mark.asyncio
async def test_pacer_delays_requests_to_stay_inside_the_free_minute_limits() -> None:
    clock = {"now": 0.0}
    slept: list[float] = []

    async def sleep(delay: float) -> None:
        slept.append(delay)
        clock["now"] += delay

    pacer = RequestPacer(
        sleep=sleep,
        monotonic=lambda: clock["now"],
        requests_per_minute=2,
        tokens_per_minute=150_000,
    )

    await pacer.acquire(10_000)
    await pacer.acquire(10_000)
    assert slept == []

    await pacer.acquire(10_000)  # third request in the same minute
    assert slept and slept[0] == pytest.approx(60.0)

    slept.clear()
    await pacer.acquire(145_000)  # would breach the token window
    assert slept


def test_token_estimate_never_understates_the_prompt() -> None:
    assert estimate_tokens(0) == 0
    assert estimate_tokens(1_000) >= 1_000


@pytest.mark.asyncio
async def test_added_columns_are_applied_to_an_existing_database() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    try:
        async with database.engine.begin() as connection:
            await connection.exec_driver_sql("ALTER TABLE daily_reports DROP COLUMN ai_state_json")

        # A second initialize() is what a redeploy runs against live data.
        await database.initialize()

        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)
        async with database.session_factory() as session:
            repository = DailyReportRepository(session)
            report = await repository.claim(
                guild_id=100,
                report_date=REPORT_DATE,
                source_channel_id=200,
                report_channel_id=300,
                regenerate=False,
            )
            await repository.save_ai_progress(
                report,
                request_count=3,
                total_requests=25,
                quota_window=quota_window(RUN_TIME),
                state_json="{}",
            )
        restored = await stored_report(database)
        assert restored is not None
        assert restored.ai_request_count == 3
        assert restored.ai_request_total == 25
        assert restored.ai_quota_window == quota_window(RUN_TIME)
        assert restored.ai_state_json == "{}"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_automatic_and_manual_runs_do_not_double_spend() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    generate = AsyncMock(side_effect=[final_response(), final_response()])
    service = build_service(database, build_provider(generate))
    try:
        await insert_messages(database, REPORT_DATE, {10: 5, 20: 5}, first_message_id=1)

        automatic, manual = await asyncio.gather(
            service.generate(REPORT_DATE, automatic=True, now=RUN_TIME),
            service.generate(REPORT_DATE, regenerate=True, now=RUN_TIME),
        )

        statuses = {automatic.status, manual.status}
        assert "duplicate" in statuses
        assert generate.await_count == 1
    finally:
        await database.close()
