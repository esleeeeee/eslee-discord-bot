from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from eslee_bot.database.repositories import AnnouncementRepository, DuplicateRecordError
from eslee_bot.services.announcement_service import AnnouncementService
from eslee_bot.utils.message_links import make_message_jump_url
from eslee_bot.utils.permissions import require_management_permission
from eslee_bot.utils.text import truncate_text

if TYPE_CHECKING:
    from eslee_bot.bot import EsleeBot

logger = logging.getLogger(__name__)


class AnnouncementCog(commands.Cog):
    announcement_group = app_commands.Group(name="공지", description="공지 리마인드를 관리합니다.")

    def __init__(self, bot: EsleeBot) -> None:
        self.bot = bot
        self.context_menu = app_commands.ContextMenu(
            name="공지로 등록", callback=self.register_context_message
        )
        self.bot.tree.add_command(self.context_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.context_menu.name, type=self.context_menu.type)

    async def _register(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if not await require_management_permission(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self.bot.database.session_factory() as session:
                announcement = await AnnouncementService(session).register_message(
                    message, interaction.user.id
                )
        except DuplicateRecordError:
            await interaction.followup.send("ℹ️ 이미 등록된 공지입니다.", ephemeral=True)
            return
        except ValueError as error:
            await interaction.followup.send(f"🚫 {error}", ephemeral=True)
            return

        sent = await self.bot.announcement_scheduler.send_now(
            announcement.id, announcement.guild_id, only_if_never_sent=True
        )
        result = "✅ 공지로 등록되었고 첫 리마인드를 전송했습니다."
        if not sent:
            result = (
                "⚠️ 공지는 등록했지만 첫 전송에 실패했습니다. 권한을 확인하면 자동 재시도합니다."
            )
        await interaction.followup.send(result, ephemeral=True)

    async def register_context_message(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        await self._register(interaction, message)

    @announcement_group.command(name="등록", description="텍스트 공지를 등록합니다.")
    @app_commands.describe(content="공지 내용", channel="원본 공지를 작성할 채널")
    async def register(
        self,
        interaction: discord.Interaction,
        content: app_commands.Range[str, 1, 2000],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                "🚫 서버에서만 사용할 수 있습니다.", ephemeral=True
            )
            return
        target = channel or interaction.channel
        if not isinstance(target, (discord.TextChannel, discord.Thread)):
            await interaction.response.send_message(
                "🚫 텍스트 채널에서만 등록할 수 있습니다.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            source = await target.send(content, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                "🚫 선택한 채널에 원본 공지를 작성할 수 없습니다.", ephemeral=True
            )
            return
        await self._register_after_defer(interaction, source)

    async def _register_after_defer(
        self, interaction: discord.Interaction, source: discord.Message
    ) -> None:
        try:
            async with self.bot.database.session_factory() as session:
                announcement = await AnnouncementService(session).register_message(
                    source, interaction.user.id
                )
        except DuplicateRecordError:
            await interaction.followup.send("ℹ️ 이미 등록된 공지입니다.", ephemeral=True)
            return
        sent = await self.bot.announcement_scheduler.send_now(
            announcement.id, announcement.guild_id, only_if_never_sent=True
        )
        message = "✅ 공지로 등록되었고 첫 리마인드를 전송했습니다."
        if not sent:
            message = "⚠️ 공지는 등록했지만 첫 전송에 실패했습니다. 권한을 확인해 주세요."
        await interaction.followup.send(message, ephemeral=True)

    @announcement_group.command(name="목록", description="활성 공지 목록을 표시합니다.")
    async def list_announcements(self, interaction: discord.Interaction) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            return
        async with self.bot.database.session_factory() as session:
            announcements = await AnnouncementRepository(session).list_for_guild(
                interaction.guild.id
            )
        if not announcements:
            await interaction.response.send_message("등록된 공지가 없습니다.", ephemeral=True)
            return
        lines = []
        for item in announcements[:25]:
            preview = truncate_text(item.content_snapshot.replace("\n", " ") or "(본문 없음)", 70)
            url = make_message_jump_url(item.guild_id, item.channel_id, item.source_message_id)
            lines.append(f"**#{item.id}** · {item.announcement_type} · [{preview}]({url})")
        embed = discord.Embed(
            title="📢 활성 공지 목록", description="\n".join(lines), color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def announcement_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild is None:
            return []
        async with self.bot.database.session_factory() as session:
            announcements = await AnnouncementRepository(session).list_for_guild(
                interaction.guild.id
            )
        choices = []
        for item in announcements:
            snapshot = item.content_snapshot.replace("\n", " ") or "(본문 없음)"
            label = f"#{item.id} {truncate_text(snapshot, 70)}"
            if current.casefold() in label.casefold():
                choices.append(app_commands.Choice(name=label, value=item.id))
        return choices[:25]

    @announcement_group.command(name="삭제", description="공지를 삭제합니다.")
    @app_commands.autocomplete(announcement_id=announcement_autocomplete)
    async def delete_announcement(
        self, interaction: discord.Interaction, announcement_id: int
    ) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            return
        async with self.bot.database.session_factory() as session:
            deleted = await AnnouncementRepository(session).delete(
                announcement_id, interaction.guild.id
            )
        text = "✅ 공지를 삭제했습니다." if deleted else "🚫 해당 공지를 찾을 수 없습니다."
        await interaction.response.send_message(text, ephemeral=True)

    @announcement_group.command(name="즉시전송", description="공지 리마인드를 즉시 전송합니다.")
    @app_commands.autocomplete(announcement_id=announcement_autocomplete)
    async def send_immediately(
        self, interaction: discord.Interaction, announcement_id: int
    ) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        sent = await self.bot.announcement_scheduler.send_now(announcement_id, interaction.guild.id)
        text = "✅ 공지 리마인드를 전송했습니다." if sent else "🚫 공지를 전송하지 못했습니다."
        await interaction.followup.send(text, ephemeral=True)


async def setup(bot: EsleeBot) -> None:
    await bot.add_cog(AnnouncementCog(bot))
