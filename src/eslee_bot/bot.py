from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from eslee_bot.config import Settings
from eslee_bot.database import Database
from eslee_bot.onekey_api import OneKeyApiServer
from eslee_bot.services.daily_summary_runtime import DailySummaryRuntime
from eslee_bot.tasks.announcement_scheduler import AnnouncementScheduler

logger = logging.getLogger(__name__)

EXTENSIONS = (
    "eslee_bot.cogs.announcements",
    "eslee_bot.cogs.moderation",
    "eslee_bot.cogs.settings",
    "eslee_bot.cogs.daily_summary",
)


class EsleeBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.database = Database(settings.database_url)
        self.announcement_scheduler = AnnouncementScheduler(self, settings.scheduler_poll_seconds)
        self.daily_summary = DailySummaryRuntime(self, settings.get_daily_summary_config())
        self.onekey_api: OneKeyApiServer | None = None
        if settings.onekey_api_enabled:
            assert settings.onekey_discord_user_id is not None
            assert settings.onekey_api_token is not None
            self.onekey_api = OneKeyApiServer(
                bot=self,
                target_user_id=settings.onekey_discord_user_id,
                api_token=settings.onekey_api_token.get_secret_value(),
                port=settings.port,
            )
        self.tree.on_error = self._on_app_command_error

    async def setup_hook(self) -> None:
        await self.database.initialize()
        for extension in EXTENSIONS:
            await self.load_extension(extension)

        if self.onekey_api is not None:
            await self.onekey_api.start()

        await self._sync_application_commands()

    async def _sync_application_commands(self) -> None:
        """Publish globally, with an optional guild copy for faster development."""
        global_synced = await self.tree.sync()
        logger.info("Synced %s global application commands", len(global_synced))

        if self.settings.discord_dev_guild_id is not None:
            guild = discord.Object(id=self.settings.discord_dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(
                "Synced %s application commands to development guild %s",
                len(synced),
                guild.id,
            )

    async def on_ready(self) -> None:
        logger.info(
            "Logged in as %s (%s); connected guilds=%s",
            self.user,
            self.user.id,  # type: ignore[union-attr]
            len(self.guilds),
        )
        self.announcement_scheduler.start()
        self.daily_summary.start()

    async def _on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.exception("Unhandled application command error", exc_info=error)
        message = "🚫 명령을 처리하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Could not send application command error response")

    async def close(self) -> None:
        if self.onekey_api is not None:
            await self.onekey_api.close()
        await self.daily_summary.stop()
        await self.announcement_scheduler.stop()
        await self.database.close()
        await super().close()
