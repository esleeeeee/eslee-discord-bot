from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from eslee_bot.database.repositories import GuildSettingsRepository
from eslee_bot.utils.permissions import require_management_permission

if TYPE_CHECKING:
    from eslee_bot.bot import EsleeBot


class SettingsCog(commands.Cog):
    settings_group = app_commands.Group(name="설정", description="서버별 봇 설정을 관리합니다.")

    def __init__(self, bot: EsleeBot) -> None:
        self.bot = bot

    @settings_group.command(name="로그채널", description="관리자 감사 로그 채널을 설정합니다.")
    async def set_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            return
        async with self.bot.database.session_factory() as session:
            await GuildSettingsRepository(session).set_log_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            f"✅ 관리자 로그 채널을 {channel.mention}(으)로 설정했습니다.", ephemeral=True
        )


async def setup(bot: EsleeBot) -> None:
    await bot.add_cog(SettingsCog(bot))
