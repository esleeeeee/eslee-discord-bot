from __future__ import annotations

from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from eslee_bot.cogs.daily_summary import DailySummaryCog
from eslee_bot.config import DailySummaryConfig
from eslee_bot.database import Database
from eslee_bot.database.repositories import DailyReportRepository
from eslee_bot.services.daily_summary_ai import GeminiConnectionResult
from eslee_bot.services.daily_summary_runtime import ReportRunResult


def summary_config(
    *,
    api_key: str | None = "secret-test-key",
    model: str = "gemini-3.5-flash",
) -> DailySummaryConfig:
    return DailySummaryConfig(
        requested_enabled=True,
        guild_id=100,
        source_channel_id=200,
        report_channel_id=300,
        gemini_api_key=api_key,
        ai_model=model,
        timezone=ZoneInfo("Asia/Seoul"),
        run_time=time(0, 2),
    )


def interaction(*, guild_id: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        response=SimpleNamespace(
            send_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def fake_bot(
    database: Database,
    *,
    config: DailySummaryConfig | None = None,
    provider: object | None = None,
    report_result: ReportRunResult | None = None,
) -> SimpleNamespace:
    report_service = SimpleNamespace(
        generate=AsyncMock(return_value=report_result or ReportRunResult("completed", "done"))
    )
    return SimpleNamespace(
        database=database,
        daily_summary=SimpleNamespace(
            config=config or summary_config(),
            provider=provider,
            report_service=report_service,
        ),
    )


@pytest.mark.asyncio
async def test_status_response_is_ephemeral() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    bot = fake_bot(database)
    cog = DailySummaryCog(bot)  # type: ignore[arg-type]
    request = interaction()
    try:
        with patch.dict(
            DailySummaryCog._require_target.__globals__,
            {"require_management_permission": AsyncMock(return_value=True)},
        ):
            await DailySummaryCog.status.callback(cog, request)  # type: ignore[arg-type]

        request.response.send_message.assert_awaited_once()
        assert request.response.send_message.await_args.kwargs["ephemeral"] is True
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "duplicate", "skipped"])
async def test_today_progress_and_every_result_are_ephemeral(status: str) -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    result = ReportRunResult(status, "result")
    bot = fake_bot(database, report_result=result)
    cog = DailySummaryCog(bot)  # type: ignore[arg-type]
    request = interaction()
    try:
        with patch.dict(
            DailySummaryCog._require_target.__globals__,
            {"require_management_permission": AsyncMock(return_value=True)},
        ):
            await DailySummaryCog.today.callback(cog, request, False)  # type: ignore[arg-type]

        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        request.followup.send.assert_awaited_once()
        assert request.followup.send.await_args.kwargs["ephemeral"] is True
        assert bot.daily_summary.report_service.generate.await_args.kwargs == {
            "regenerate": False,
            "preview": True,
        }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_yesterday_progress_and_result_are_ephemeral() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    bot = fake_bot(database)
    cog = DailySummaryCog(bot)  # type: ignore[arg-type]
    request = interaction()
    try:
        with patch.dict(
            DailySummaryCog._require_target.__globals__,
            {"require_management_permission": AsyncMock(return_value=True)},
        ):
            await DailySummaryCog.yesterday.callback(cog, request, False)  # type: ignore[arg-type]

        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        request.followup.send.assert_awaited_once()
        assert request.followup.send.await_args.kwargs["ephemeral"] is True
        assert bot.daily_summary.report_service.generate.await_args.kwargs == {
            "regenerate": False,
            "replace_preview": True,
        }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_wrong_guild_denial_is_ephemeral() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    bot = fake_bot(database)
    cog = DailySummaryCog(bot)  # type: ignore[arg-type]
    request = interaction(guild_id=999)
    try:
        with patch.dict(
            DailySummaryCog._require_target.__globals__,
            {"require_management_permission": AsyncMock(return_value=True)},
        ):
            await DailySummaryCog.status.callback(cog, request)  # type: ignore[arg-type]

        request.response.send_message.assert_awaited_once()
        assert request.response.send_message.await_args.kwargs["ephemeral"] is True
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connection_result",
    [
        GeminiConnectionResult(True, "Gemini API 연결 정상"),
        GeminiConnectionResult(False, "Gemini API 키가 유효하지 않습니다. (401)", 401),
    ],
)
async def test_connection_check_is_ephemeral_and_does_not_generate_a_report(
    connection_result: GeminiConnectionResult,
) -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    provider = SimpleNamespace(check_connection=AsyncMock(return_value=connection_result))
    bot = fake_bot(database, provider=provider)
    cog = DailySummaryCog(bot)  # type: ignore[arg-type]
    request = interaction()
    try:
        with patch.dict(
            DailySummaryCog._require_target.__globals__,
            {"require_management_permission": AsyncMock(return_value=True)},
        ):
            await DailySummaryCog.connection_check.callback(  # type: ignore[arg-type]
                cog,
                request,
            )

        request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        request.followup.send.assert_awaited_once()
        assert request.followup.send.await_args.kwargs["ephemeral"] is True
        embed = request.followup.send.await_args.kwargs["embed"]
        rendered = str(embed.to_dict())
        assert connection_result.message in rendered
        assert "secret-test-key" not in rendered
        provider.check_connection.assert_awaited_once_with()
        bot.daily_summary.report_service.generate.assert_not_awaited()
        async with database.session_factory() as session:
            assert await DailyReportRepository(session).latest(100) is None
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_connection_check_with_missing_key_is_private_and_makes_no_api_call() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    bot = fake_bot(database, config=summary_config(api_key=None), provider=None)
    cog = DailySummaryCog(bot)  # type: ignore[arg-type]
    request = interaction()
    try:
        with patch.dict(
            DailySummaryCog._require_target.__globals__,
            {"require_management_permission": AsyncMock(return_value=True)},
        ):
            await DailySummaryCog.connection_check.callback(  # type: ignore[arg-type]
                cog,
                request,
            )

        request.response.send_message.assert_awaited_once()
        assert request.response.send_message.await_args.kwargs["ephemeral"] is True
        request.response.defer.assert_not_awaited()
        request.followup.send.assert_not_awaited()
        rendered = str(request.response.send_message.await_args.kwargs["embed"].to_dict())
        assert "GEMINI_API_KEY" in rendered
        assert "secret-test-key" not in rendered
        bot.daily_summary.report_service.generate.assert_not_awaited()
    finally:
        await database.close()
