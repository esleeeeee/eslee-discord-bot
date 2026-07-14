from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import discord
from discord import app_commands
from discord.ext import commands

from eslee_bot.database.repositories import (
    DailyReportRepository,
    DailySummaryMessageRepository,
)
from eslee_bot.services.daily_summary import current_day_window_utc
from eslee_bot.utils.permissions import require_management_permission

if TYPE_CHECKING:
    from eslee_bot.bot import EsleeBot

logger = logging.getLogger(__name__)


class DailySummaryCog(commands.Cog):
    summary_group = app_commands.Group(
        name="하루요약",
        description="지정 채널의 일일 대화 요약을 관리합니다.",
    )

    def __init__(self, bot: EsleeBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        collector = self.bot.daily_summary.collector
        if collector is None:
            return
        try:
            await collector.collect(message)
        except Exception as error:
            guild_id = message.guild.id if message.guild is not None else None
            logger.error(
                "Daily summary realtime collection failed safely "
                "(guild=%s channel=%s error_type=%s)",
                guild_id,
                message.channel.id,
                type(error).__name__,
            )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        collector = self.bot.daily_summary.collector
        if (
            collector is None
            or payload.guild_id is None
            or "content" not in payload.data
            or not collector.is_target_scope(payload.guild_id, payload.channel_id)
        ):
            return
        channel = self.bot.get_channel(payload.channel_id)
        try:
            if channel is None:
                channel = await self.bot.fetch_channel(payload.channel_id)
            if not hasattr(channel, "fetch_message"):
                return
            message = await channel.fetch_message(payload.message_id)  # type: ignore[attr-defined]
            await collector.update(message)
        except discord.NotFound:
            await collector.delete(payload.message_id, payload.guild_id, payload.channel_id)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "Daily summary could not fetch edited message (guild=%s channel=%s)",
                payload.guild_id,
                payload.channel_id,
            )
        except Exception as error:
            logger.error(
                "Daily summary edit handling failed safely (guild=%s channel=%s error_type=%s)",
                payload.guild_id,
                payload.channel_id,
                type(error).__name__,
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        collector = self.bot.daily_summary.collector
        if collector is None or payload.guild_id is None:
            return
        try:
            await collector.delete(payload.message_id, payload.guild_id, payload.channel_id)
        except Exception as error:
            logger.error(
                "Daily summary delete handling failed safely (guild=%s channel=%s error_type=%s)",
                payload.guild_id,
                payload.channel_id,
                type(error).__name__,
            )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        collector = self.bot.daily_summary.collector
        if collector is None or payload.guild_id is None:
            return
        try:
            await collector.delete_many(
                payload.message_ids,
                payload.guild_id,
                payload.channel_id,
            )
        except Exception as error:
            logger.error(
                "Daily summary bulk delete handling failed safely "
                "(guild=%s channel=%s error_type=%s)",
                payload.guild_id,
                payload.channel_id,
                type(error).__name__,
            )

    @summary_group.command(name="상태", description="일일 요약 설정과 최근 상태를 확인합니다.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await self._require_target(interaction, require_enabled=False):
            return
        config = self.bot.daily_summary.config
        today_count = 0
        latest_status = "없음"
        if config.enabled:
            now = datetime.now(UTC)
            start, end = current_day_window_utc(now, cast(Any, config.timezone))
            async with self.bot.database.session_factory() as session:
                today_count = await DailySummaryMessageRepository(session).count_between(
                    cast(int, config.guild_id),
                    cast(int, config.source_channel_id),
                    start,
                    end,
                )
                latest = await DailyReportRepository(session).latest(cast(int, config.guild_id))
                if latest is not None:
                    latest_status = f"{latest.report_date.isoformat()} · {latest.status}"

        embed = discord.Embed(title="📊 하루요약 상태", color=discord.Color.blurple())
        embed.add_field(
            name="기능",
            value="활성" if config.enabled else "비활성",
            inline=True,
        )
        embed.add_field(name="대상 서버", value=str(config.guild_id or "미설정"), inline=True)
        embed.add_field(
            name="수집 채널",
            value=f"<#{config.source_channel_id}>" if config.source_channel_id else "미설정",
            inline=True,
        )
        embed.add_field(
            name="리포트 채널",
            value=f"<#{config.report_channel_id}>" if config.report_channel_id else "미설정",
            inline=True,
        )
        embed.add_field(name="시간대", value=config.timezone_name, inline=True)
        embed.add_field(name="자동 실행", value=config.run_time_text, inline=True)
        embed.add_field(name="AI 모델", value=config.ai_model or "미설정", inline=True)
        embed.add_field(
            name="원문 보관",
            value=f"{config.raw_retention_days}일",
            inline=True,
        )
        embed.add_field(name="오늘 저장 메시지", value=f"{today_count:,}개", inline=True)
        embed.add_field(name="최근 리포트", value=latest_status, inline=False)
        if config.validation_errors:
            embed.add_field(
                name="비활성 사유",
                value="\n".join(f"• {item}" for item in config.validation_errors)[:1000],
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @summary_group.command(name="오늘", description="오늘 현재까지의 테스트 리포트를 생성합니다.")
    @app_commands.describe(재생성="이미 생성된 오늘 리포트를 다시 생성합니다.")
    async def today(self, interaction: discord.Interaction, 재생성: bool = False) -> None:
        if not await self._require_target(interaction, require_enabled=True):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = self.bot.daily_summary.config
        report_date = datetime.now(UTC).astimezone(cast(Any, config.timezone)).date()
        result = await cast(Any, self.bot.daily_summary.report_service).generate(
            report_date,
            regenerate=재생성,
            preview=True,
        )
        await interaction.followup.send(
            _result_message(result.status, result.detail), ephemeral=True
        )

    @summary_group.command(name="어제", description="어제의 일일 리포트를 생성합니다.")
    @app_commands.describe(재생성="이미 생성된 어제 리포트를 다시 생성합니다.")
    async def yesterday(self, interaction: discord.Interaction, 재생성: bool = False) -> None:
        if not await self._require_target(interaction, require_enabled=True):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = self.bot.daily_summary.config
        report_date = datetime.now(UTC).astimezone(cast(Any, config.timezone)).date() - timedelta(
            days=1
        )
        result = await cast(Any, self.bot.daily_summary.report_service).generate(
            report_date,
            regenerate=재생성,
        )
        await interaction.followup.send(
            _result_message(result.status, result.detail), ephemeral=True
        )

    async def _require_target(
        self,
        interaction: discord.Interaction,
        *,
        require_enabled: bool,
    ) -> bool:
        if not await require_management_permission(interaction):
            return False
        config = self.bot.daily_summary.config
        if interaction.guild is None or interaction.guild.id != config.guild_id:
            await interaction.response.send_message(
                "🚫 이 서버는 하루요약 기능의 대상 서버가 아닙니다.",
                ephemeral=True,
            )
            return False
        if require_enabled and not config.enabled:
            await interaction.response.send_message(
                "🚫 하루요약 기능이 비활성화되어 있습니다. 환경설정을 확인해 주세요.",
                ephemeral=True,
            )
            return False
        return True


def _result_message(status: str, detail: str) -> str:
    prefix = {
        "completed": "✅",
        "skipped": "ℹ️",
        "duplicate": "ℹ️",
        "failed": "🚫",
    }.get(status, "ℹ️")
    return f"{prefix} {detail}"


async def setup(bot: EsleeBot) -> None:
    await bot.add_cog(DailySummaryCog(bot))
