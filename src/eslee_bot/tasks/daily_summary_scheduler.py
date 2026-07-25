from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast

from eslee_bot.config import DailySummaryConfig
from eslee_bot.database.repositories import DailySummaryMessageRepository
from eslee_bot.services.daily_summary import retention_cutoff_utc, scheduled_report_date

if TYPE_CHECKING:
    from eslee_bot.bot import EsleeBot
    from eslee_bot.services.daily_summary_runtime import DailyReportService

logger = logging.getLogger(__name__)


class DailySummaryScheduler:
    def __init__(
        self,
        bot: EsleeBot,
        config: DailySummaryConfig,
        report_service: DailyReportService,
        *,
        poll_seconds: int,
    ) -> None:
        self.bot = bot
        self.config = config
        self.report_service = report_service
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._last_terminal_report_day: date | None = None
        self._last_cleanup_day: date | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="daily-summary-scheduler")
        logger.info(
            "Daily summary scheduler started "
            "(enabled=true timezone=%s run_time=%s poll_seconds=%s)",
            self.config.timezone_name,
            self.config.run_time_text,
            self.poll_seconds,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Daily summary scheduler tick failed safely")
            await asyncio.sleep(self.poll_seconds)

    async def tick(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        timezone = cast(Any, self.config.timezone)
        local_date = current.astimezone(timezone).date()
        if self._last_cleanup_day != local_date:
            cutoff = retention_cutoff_utc(
                current,
                timezone,
                self.config.raw_retention_days,
            )
            async with self.bot.database.session_factory() as session:
                deleted = await DailySummaryMessageRepository(session).delete_before(cutoff)
            self._last_cleanup_day = local_date
            logger.info("Daily summary raw retention cleanup deleted %s messages", deleted)

        report_date = scheduled_report_date(
            current,
            timezone,
            cast(Any, self.config.run_time),
        )
        if report_date is None or self._last_terminal_report_day == local_date:
            return
        logger.info(
            "Daily summary scheduled run starting (date=%s local_time=%s)",
            report_date,
            current.astimezone(timezone).isoformat(),
        )
        result = await self.report_service.generate(
            report_date,
            replace_preview=True,
            recover_incomplete=True,
        )
        if result.status in {"completed", "already_completed", "skipped"}:
            self._last_terminal_report_day = local_date
        else:
            logger.warning(
                "Daily summary scheduled run remains incomplete and will retry "
                "(date=%s status=%s)",
                report_date,
                result.status,
            )
        logger.info(
            "Daily summary scheduled run finished (date=%s status=%s)",
            report_date,
            result.status,
        )
