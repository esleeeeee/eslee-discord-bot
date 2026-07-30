from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from google.genai import errors

from eslee_bot.config import DailySummaryConfig
from eslee_bot.database import Database
from eslee_bot.database.repositories import (
    DailyReportRepository,
    DailySummaryMessageRepository,
    ForbiddenWordRepository,
)
from eslee_bot.services.daily_summary import (
    GeneratedSummary,
    SummaryStats,
    UserSummary,
    day_bounds_utc,
)
from eslee_bot.services.daily_summary_ai import (
    AISummaryResponse,
    AIUserSummary,
    GeminiSummaryProvider,
)
from eslee_bot.services.daily_summary_retry import (
    MAX_AUTOMATIC_AI_REQUESTS_PER_REPORT,
)
from eslee_bot.services.daily_summary_runtime import (
    DailyReportService,
    DiscordPublishError,
    ReportRunResult,
    build_report_embeds,
)
from eslee_bot.tasks.daily_summary_scheduler import DailySummaryScheduler

KST = ZoneInfo("Asia/Seoul")


def summary_config(**overrides: object) -> DailySummaryConfig:
    values: dict[str, object] = {
        "requested_enabled": True,
        "guild_id": 100,
        "source_channel_id": 200,
        "report_channel_id": 300,
        "gemini_api_key": "test-key",
        "timezone": KST,
        "run_time": datetime.strptime("06:02", "%H:%M").time(),
        "min_total_messages": 10,
        "min_participants": 2,
        "min_user_messages": 3,
        "max_users": 20,
    }
    values.update(overrides)
    return DailySummaryConfig(**values)  # type: ignore[arg-type]


class FakeBot:
    def __init__(self, database: Database) -> None:
        self.database = database


class FakeProvider:
    def __init__(self, *, block: bool = False) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not block:
            self.release.set()

    async def summarize(self, messages, targets, *, timezone):  # type: ignore[no-untyped-def]
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return GeneratedSummary(
            daily_summary="하루 전체 요약이다.",
            user_summaries=tuple(
                UserSummary(user_id=target.user_id, summary=f"{target.display_name} 요약")
                for target in targets
            ),
            api_request_count=1,
            used_chunk_fallback=False,
        )

    async def close(self) -> None:
        return None


class FakePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[list[object], list[int]]] = []
        self.republish_calls: list[list[int]] = []

    async def publish(self, embeds, existing_message_ids):  # type: ignore[no-untyped-def]
        self.calls.append((embeds, list(existing_message_ids)))
        return [501 + index for index in range(len(embeds))]

    async def republish(self, source_message_ids):  # type: ignore[no-untyped-def]
        self.republish_calls.append(list(source_message_ids))
        return [801 + index for index in range(len(source_message_ids))]


class FailingPublisher:
    async def publish(self, embeds, existing_message_ids):  # type: ignore[no-untyped-def]
        raise DiscordPublishError([701])


async def insert_messages(
    database: Database,
    report_date: date,
    counts: dict[int, int],
    *,
    first_message_id: int,
) -> None:
    start, _ = day_bounds_utc(report_date, KST)
    rows = []
    message_id = first_message_id
    minute = 0
    for author_id, count in counts.items():
        for _ in range(count):
            rows.append(
                {
                    "guild_id": 100,
                    "channel_id": 200,
                    "message_id": message_id,
                    "author_id": author_id,
                    "author_display_name": f"사용자 {author_id}",
                    "content": f"메시지 {message_id}",
                    "reply_to_message_id": None,
                    "created_at": start + timedelta(minutes=minute),
                }
            )
            message_id += 1
            minute += 1
    async with database.session_factory() as session:
        inserted, skipped = await DailySummaryMessageRepository(session).add_many(rows)
    assert inserted == len(rows)
    assert skipped == 0


@pytest.mark.asyncio
async def test_report_thresholds_skip_ai_and_discord_calls() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    publisher = FakePublisher()
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    few_messages = date(2026, 7, 12)
    one_participant = date(2026, 7, 13)
    try:
        await insert_messages(database, few_messages, {1: 5, 2: 4}, first_message_id=1)
        await insert_messages(database, one_participant, {1: 10}, first_message_id=100)

        first = await service.generate(few_messages)
        second = await service.generate(one_participant)

        assert first.status == "skipped"
        assert second.status == "skipped"
        assert provider.calls == 0
        assert publisher.calls == []
        async with database.session_factory() as session:
            assert (await DailyReportRepository(session).get(100, few_messages)).status == "skipped"  # type: ignore[union-attr]
            assert (
                await DailyReportRepository(session).get(100, one_participant)
            ).status == "skipped"  # type: ignore[union-attr]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_completed_report_is_persisted_deduplicated_and_regenerable() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    publisher = FakePublisher()
    report_date = date(2026, 7, 13)
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        completed = await service.generate(report_date)
        duplicate = await service.generate(report_date)
        regenerated = await service.generate(report_date, regenerate=True)

        assert completed.status == "completed"
        assert duplicate.status == "duplicate"
        assert regenerated.status == "completed"
        assert provider.calls == 2
        assert publisher.calls[0][1] == []
        assert publisher.calls[1][1] == [501, 502]
        async with database.session_factory() as session:
            stored = await DailyReportRepository(session).get(100, report_date)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.message_count == 10
        assert stored.participant_count == 2
        assert stored.top_user_id == 10
        assert stored.discord_message_ids_json == "[501, 502]"
        assert "사용자 10 요약" in stored.user_summaries_json
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_generation_runs_ai_only_once() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider(block=True)
    publisher = FakePublisher()
    report_date = date(2026, 7, 13)
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)
        first_task = asyncio.create_task(service.generate(report_date))
        await provider.started.wait()

        duplicate = await service.generate(
            report_date,
            replace_preview=True,
            recover_incomplete=True,
        )
        provider.release.set()
        first = await first_task

        assert duplicate.status == "duplicate"
        assert first.status == "completed"
        assert provider.calls == 1
        assert len(publisher.calls) == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduled_final_posts_fresh_messages_after_a_today_preview() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    publisher = FakePublisher()
    report_date = date(2026, 7, 13)
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        preview = await service.generate(report_date, preview=True)
        duplicate_preview = await service.generate(report_date, preview=True)
        final = await service.generate(report_date, replace_preview=True)

        assert preview.status == "completed"
        assert duplicate_preview.status == "duplicate"
        assert final.status == "completed"
        assert provider.calls == 2
        assert publisher.calls[0][1] == []
        assert publisher.calls[1][1] == []
        async with database.session_factory() as session:
            stored = await DailyReportRepository(session).get(100, report_date)
        assert stored is not None
        assert stored.status == "completed"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_completed_report_is_republished_without_another_ai_request() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    publisher = FakePublisher()
    report_date = date(2026, 7, 13)
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)
        generated = await service.generate(report_date)

        republished = await service.republish(report_date)

        assert generated.status == "completed"
        assert republished.status == "completed"
        assert provider.calls == 1
        assert publisher.republish_calls == [[501, 502]]
        async with database.session_factory() as session:
            stored = await DailyReportRepository(session).get(100, report_date)
        assert stored is not None
        assert stored.discord_message_ids_json == "[501, 502]"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_replaces_a_legacy_final_created_before_0600_window_end() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    publisher = FakePublisher()
    report_date = datetime.now(KST).date()
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        legacy_final = await service.generate(report_date)
        replacement = await service.generate(report_date, replace_preview=True)

        assert legacy_final.status == "completed"
        assert replacement.status == "completed"
        assert provider.calls == 2
        assert publisher.calls[1][1] == []
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_discord_publish_failure_is_persisted_without_completed_status() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    report_date = date(2026, 7, 13)
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        FailingPublisher(),  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        result = await service.generate(report_date)

        assert result.status == "failed"
        assert provider.calls == 1
        async with database.session_factory() as session:
            stored = await DailyReportRepository(session).get(100, report_date)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.error_message == "DiscordPublishError"
        assert stored.discord_message_ids_json == "[701]"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_recovery_reclaims_a_failed_report() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    report_date = date(2026, 7, 13)
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        FailingPublisher(),  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        failed = await service.generate(report_date)
        recovered_publisher = FakePublisher()
        service.publisher = recovered_publisher  # type: ignore[assignment]
        recovered = await service.generate(
            report_date,
            replace_preview=True,
            recover_incomplete=True,
        )

        assert failed.status == "failed"
        assert recovered.status == "completed"
        assert provider.calls == 2
        assert len(recovered_publisher.calls) == 1
        async with database.session_factory() as session:
            stored = await DailyReportRepository(session).get(100, report_date)
        assert stored is not None
        assert stored.status == "completed"
    finally:
        await database.close()


def test_report_embeds_split_long_personal_summaries_within_discord_limit() -> None:
    stats = SummaryStats(100, 20, 22, 1, "은성", 15)
    generated = GeneratedSummary(
        daily_summary="하루 요약",
        user_summaries=tuple(
            UserSummary(user_id=user_id, summary="가" * 450) for user_id in range(1, 21)
        ),
        api_request_count=1,
        used_chunk_fallback=False,
    )

    embeds = build_report_embeds(
        date(2026, 7, 13),
        stats,
        generated,
        {user_id: f"사용자 {user_id}" for user_id in range(1, 21)},
    )

    assert len(embeds) >= 4
    assert all(len(embed.description or "") <= 3900 for embed in embeds)
    assert embeds[0].footer.text == "전날 06시부터 오늘 06시까지의 기록입니다."


def test_preview_embed_identifies_the_in_progress_0600_window() -> None:
    stats = SummaryStats(10, 2, 8, 1, "은성", 6)
    generated = GeneratedSummary("미리보기", (), 1, False)

    embeds = build_report_embeds(
        date(2026, 7, 15),
        stats,
        generated,
        {},
        preview=True,
    )

    assert embeds[0].footer.text == "오늘 06시부터 현재까지의 미리보기 기록입니다."


class FakeReportService:
    def __init__(
        self,
        results: list[ReportRunResult] | None = None,
        *,
        raise_once: bool = False,
    ) -> None:
        self.dates: list[date] = []
        self.results = results or [ReportRunResult("completed", "done")]
        self.raise_once = raise_once

    async def generate(
        self,
        report_date: date,
        *,
        replace_preview: bool = False,
        recover_incomplete: bool = False,
        automatic: bool = False,
        now: datetime | None = None,
    ) -> ReportRunResult:
        assert replace_preview is True
        assert recover_incomplete is True
        assert automatic is True
        assert now is not None
        self.dates.append(report_date)
        if self.raise_once:
            self.raise_once = False
            raise RuntimeError("temporary scheduler failure")
        index = min(len(self.dates) - 1, len(self.results) - 1)
        return self.results[index]


@pytest.mark.asyncio
async def test_scheduler_cleans_only_expired_raw_messages_and_runs_at_0602() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    before = datetime(2026, 7, 14, 6, 1, tzinfo=KST).astimezone(UTC)
    run_time = datetime(2026, 7, 14, 6, 2, tzinfo=KST).astimezone(UTC)
    report_service = FakeReportService()
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(raw_retention_days=3),
        report_service,  # type: ignore[arg-type]
        poll_seconds=60,
    )
    try:
        rows = [
            {
                "guild_id": 100,
                "channel_id": 200,
                "message_id": 1,
                "author_id": 1,
                "author_display_name": "old",
                "content": "old",
                "reply_to_message_id": None,
                "created_at": datetime(2026, 7, 10, 20, 59, tzinfo=UTC),
            },
            {
                "guild_id": 100,
                "channel_id": 200,
                "message_id": 2,
                "author_id": 1,
                "author_display_name": "keep",
                "content": "keep",
                "reply_to_message_id": None,
                "created_at": datetime(2026, 7, 10, 21, tzinfo=UTC),
            },
        ]
        async with database.session_factory() as session:
            await DailySummaryMessageRepository(session).add_many(rows)
            await ForbiddenWordRepository(session).add(100, "금지", "금지", 1)

        await scheduler.tick(now=before)
        assert report_service.dates == []
        await scheduler.tick(now=run_time)
        await scheduler.tick(now=run_time + timedelta(minutes=1))

        assert report_service.dates == [date(2026, 7, 13)]
        async with database.session_factory() as session:
            kept = await DailySummaryMessageRepository(session).list_between(
                100,
                200,
                datetime(2020, 1, 1, tzinfo=UTC),
                datetime(2030, 1, 1, tzinfo=UTC),
            )
            forbidden = await ForbiddenWordRepository(session).list_for_guild(100)
        assert [item.message_id for item in kept] == [2]
        assert [item.word for item in forbidden] == ["금지"]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_catches_up_when_first_post_schedule_tick_is_0603() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    report_service = FakeReportService()
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        report_service,  # type: ignore[arg-type]
        poll_seconds=60,
    )
    try:
        before = datetime(2026, 7, 14, 6, 1, tzinfo=KST).astimezone(UTC)
        catch_up = datetime(2026, 7, 14, 6, 3, tzinfo=KST).astimezone(UTC)

        await scheduler.tick(now=before)
        await scheduler.tick(now=catch_up)

        assert report_service.dates == [date(2026, 7, 13)]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_first_startup_tick_after_0602_catches_up() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    report_service = FakeReportService()
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        report_service,  # type: ignore[arg-type]
        poll_seconds=60,
    )
    try:
        startup = datetime(2026, 7, 14, 9, 30, tzinfo=KST).astimezone(UTC)

        await scheduler.tick(now=startup)

        assert report_service.dates == [date(2026, 7, 13)]
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_cools_down_before_retrying_a_nonterminal_result() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    report_service = FakeReportService(
        [
            ReportRunResult("failed", "temporary failure"),
            ReportRunResult("completed", "done"),
        ]
    )
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        report_service,  # type: ignore[arg-type]
        poll_seconds=60,
    )
    try:
        first = datetime(2026, 7, 14, 6, 2, tzinfo=KST).astimezone(UTC)

        await scheduler.tick(now=first)
        await scheduler.tick(now=first + timedelta(minutes=1))
        assert report_service.dates == [date(2026, 7, 13)]
        await scheduler.tick(now=first + timedelta(minutes=15))

        assert report_service.dates == [date(2026, 7, 13), date(2026, 7, 13)]
    finally:
        await database.close()


class SchedulerLoopBot(FakeBot):
    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self.close_checks = 0

    async def wait_until_ready(self) -> None:
        return None

    def is_closed(self) -> bool:
        self.close_checks += 1
        return self.close_checks > 2


@pytest.mark.asyncio
async def test_scheduler_loop_survives_one_tick_exception() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    report_service = FakeReportService(raise_once=True)
    scheduler = DailySummaryScheduler(
        SchedulerLoopBot(database),  # type: ignore[arg-type]
        summary_config(run_time=datetime.min.time()),
        report_service,  # type: ignore[arg-type]
        poll_seconds=0,
    )
    try:
        await scheduler._run()

        assert len(report_service.dates) == 2
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_scheduler_recovery_does_not_regenerate_a_completed_report() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = FakeProvider()
    publisher = FakePublisher()
    report_date = date(2026, 7, 13)
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        completed = await service.generate(report_date)
        recovered = await service.generate(
            report_date,
            replace_preview=True,
            recover_incomplete=True,
        )

        assert completed.status == "completed"
        assert recovered.status == "already_completed"
        assert provider.calls == 1
        assert len(publisher.calls) == 1
    finally:
        await database.close()


def gemini_client(generate: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )


def successful_gemini_response() -> SimpleNamespace:
    return SimpleNamespace(
        parsed=AISummaryResponse(
            daily_summary="자동 요약 성공",
            user_summaries=[
                AIUserSummary(user_id="10", summary="사용자 10 요약"),
                AIUserSummary(user_id="20", summary="사용자 20 요약"),
            ],
        ),
        text=None,
    )


@pytest.mark.asyncio
async def test_daily_quota_cooldown_prevents_tick_flood_and_catches_up_after_reset() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    report_date = date(2026, 7, 29)
    daily_quota = errors.APIError(
        429,
        {
            "error": {
                "details": [
                    {
                        "violations": [
                            {
                                "quotaId": (
                                    "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
                                )
                            }
                        ]
                    }
                ]
            }
        },
    )
    generate = AsyncMock(side_effect=[daily_quota, successful_gemini_response()])
    provider = GeminiSummaryProvider(
        "test",
        "gemini-test",
        client=gemini_client(generate),
        sleep=AsyncMock(),
        jitter=lambda: 0,
    )
    publisher = FakePublisher()
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        publisher,  # type: ignore[arg-type]
    )
    first = datetime(2026, 7, 30, 6, 2, tzinfo=KST).astimezone(UTC)
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        service,
        poll_seconds=60,
    )
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        await scheduler.tick(now=first)
        assert generate.await_count == 1

        restarted_scheduler = DailySummaryScheduler(
            FakeBot(database),  # type: ignore[arg-type]
            summary_config(),
            service,
            poll_seconds=60,
        )
        for hour in range(7, 16):
            await restarted_scheduler.tick(
                now=datetime(2026, 7, 30, hour, 0, tzinfo=KST).astimezone(UTC)
            )
        await restarted_scheduler.tick(
            now=datetime(2026, 7, 30, 16, 4, tzinfo=KST).astimezone(UTC)
        )
        assert generate.await_count == 1

        await restarted_scheduler.tick(
            now=datetime(2026, 7, 30, 16, 5, tzinfo=KST).astimezone(UTC)
        )

        assert generate.await_count == 2
        assert len(publisher.calls) == 1
        async with database.session_factory() as session:
            stored = await DailyReportRepository(session).get(100, report_date)
        assert stored is not None
        assert stored.status == "completed"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_automatic_ai_requests_have_a_persisted_per_report_limit() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    report_date = date(2026, 7, 29)
    generate = AsyncMock(
        side_effect=errors.APIError(503, {"message": "temporarily unavailable"})
    )
    provider = GeminiSummaryProvider(
        "test",
        "gemini-test",
        client=gemini_client(generate),
        sleep=AsyncMock(),
        jitter=lambda: 0,
    )
    service = DailyReportService(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        provider,
        FakePublisher(),  # type: ignore[arg-type]
    )
    scheduler = DailySummaryScheduler(
        FakeBot(database),  # type: ignore[arg-type]
        summary_config(),
        service,
        poll_seconds=60,
    )
    first = datetime(2026, 7, 30, 6, 2, tzinfo=KST).astimezone(UTC)
    try:
        await insert_messages(database, report_date, {10: 5, 20: 5}, first_message_id=1)

        for attempt in range(12):
            await scheduler.tick(now=first + timedelta(minutes=15 * attempt))

        assert generate.await_count == MAX_AUTOMATIC_AI_REQUESTS_PER_REPORT

        restarted_scheduler = DailySummaryScheduler(
            FakeBot(database),  # type: ignore[arg-type]
            summary_config(),
            service,
            poll_seconds=60,
        )
        await restarted_scheduler.tick(now=first + timedelta(hours=5))
        assert generate.await_count == MAX_AUTOMATIC_AI_REQUESTS_PER_REPORT
    finally:
        await database.close()
