from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

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
        "run_time": datetime.strptime("00:02", "%H:%M").time(),
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

    async def publish(self, embeds, existing_message_ids):  # type: ignore[no-untyped-def]
        self.calls.append((embeds, list(existing_message_ids)))
        return [501 + index for index in range(len(embeds))]


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

        duplicate = await service.generate(report_date)
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


class FakeReportService:
    def __init__(self) -> None:
        self.dates: list[date] = []

    async def generate(
        self, report_date: date, *, replace_preview: bool = False
    ) -> ReportRunResult:
        assert replace_preview is True
        self.dates.append(report_date)
        return ReportRunResult("completed", "done")


@pytest.mark.asyncio
async def test_scheduler_cleans_only_expired_raw_messages_and_runs_at_0002() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    now = datetime(2026, 7, 14, 0, 2, tzinfo=KST).astimezone(UTC)
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
                "created_at": datetime(2026, 7, 10, 14, 59, tzinfo=UTC),
            },
            {
                "guild_id": 100,
                "channel_id": 200,
                "message_id": 2,
                "author_id": 1,
                "author_display_name": "keep",
                "content": "keep",
                "reply_to_message_id": None,
                "created_at": datetime(2026, 7, 10, 15, tzinfo=UTC),
            },
        ]
        async with database.session_factory() as session:
            await DailySummaryMessageRepository(session).add_many(rows)
            await ForbiddenWordRepository(session).add(100, "금지", "금지", 1)

        await scheduler.tick(now=now)
        await scheduler.tick(now=now + timedelta(minutes=1))

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
