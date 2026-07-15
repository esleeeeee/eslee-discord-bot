from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from eslee_bot.database.repositories import (
    DuplicateRecordError,
    ForbiddenWordRepository,
    GuildSettingsRepository,
    ModerationViolationRepository,
)
from eslee_bot.services.moderation_service import (
    build_user_warning,
    find_forbidden_words,
    parse_forbidden_word_batch,
)
from eslee_bot.utils.permissions import require_management_permission
from eslee_bot.utils.text import normalize_forbidden_word, truncate_text
from eslee_bot.utils.time import format_kst, utc_now

if TYPE_CHECKING:
    from eslee_bot.bot import EsleeBot

logger = logging.getLogger(__name__)


class ModerationCog(commands.Cog):
    forbidden_group = app_commands.Group(name="금지어", description="금지어를 관리합니다.")

    def __init__(self, bot: EsleeBot) -> None:
        self.bot = bot

    @forbidden_group.command(name="추가", description="금지어를 추가합니다.")
    async def add_forbidden_word(
        self, interaction: discord.Interaction, word: app_commands.Range[str, 1, 100]
    ) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            return
        try:
            normalized = normalize_forbidden_word(word)
            async with self.bot.database.session_factory() as session:
                await ForbiddenWordRepository(session).add(
                    interaction.guild.id, word.strip(), normalized, interaction.user.id
                )
        except ValueError as error:
            await interaction.response.send_message(f"🚫 {error}", ephemeral=True)
            return
        except DuplicateRecordError:
            await interaction.response.send_message(
                "ℹ️ 같은 금지어가 이미 등록되어 있습니다.", ephemeral=True
            )
            return
        suffix = " 1글자 금지어는 오탐 가능성이 높습니다." if len(word.strip()) == 1 else ""
        await interaction.response.send_message(
            f"✅ 금지어 `{word.strip()}`을(를) 추가했습니다.{suffix}", ephemeral=True
        )

    @forbidden_group.command(
        name="일괄추가", description="쉼표 또는 줄바꿈으로 여러 금지어를 추가합니다."
    )
    @app_commands.describe(words="예: 사과, 바나나, TEST (최대 500개)")
    async def add_forbidden_words(
        self, interaction: discord.Interaction, words: app_commands.Range[str, 1, 6000]
    ) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            return
        try:
            entries = parse_forbidden_word_batch(words)
            async with self.bot.database.session_factory() as session:
                added, skipped = await ForbiddenWordRepository(session).add_many(
                    interaction.guild.id, entries, interaction.user.id
                )
        except ValueError as error:
            await interaction.response.send_message(f"🚫 {error}", ephemeral=True)
            return
        except DuplicateRecordError:
            await interaction.response.send_message(
                "⚠️ 동시에 금지어가 변경되었습니다. 다시 시도해 주세요.", ephemeral=True
            )
            return

        lines = [f"✅ 금지어 {len(added)}개를 추가했습니다."]
        if added:
            added_text = ", ".join(f"`{word.replace('`', 'ˋ')}`" for word in added)
            lines.append(truncate_text(added_text, 1000))
        if skipped:
            skipped_text = ", ".join(f"`{word.replace('`', 'ˋ')}`" for word in skipped)
            lines.append(
                f"ℹ️ 이미 등록되어 건너뜀 ({len(skipped)}개): {truncate_text(skipped_text, 700)}"
            )
        if any(len(word) == 1 for word in added):
            lines.append("⚠️ 1글자 금지어는 오탐 가능성이 높습니다.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    async def word_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        async with self.bot.database.session_factory() as session:
            words = await ForbiddenWordRepository(session).list_for_guild(interaction.guild.id)
        return [
            app_commands.Choice(name=item.word, value=item.word)
            for item in words
            if current.casefold() in item.word.casefold()
        ][:25]

    @forbidden_group.command(name="삭제", description="금지어를 삭제합니다.")
    @app_commands.autocomplete(word=word_autocomplete)
    async def delete_forbidden_word(self, interaction: discord.Interaction, word: str) -> None:
        if not await require_management_permission(interaction):
            return
        if interaction.guild is None:
            return
        try:
            normalized = normalize_forbidden_word(word)
        except ValueError as error:
            await interaction.response.send_message(f"🚫 {error}", ephemeral=True)
            return
        async with self.bot.database.session_factory() as session:
            deleted = await ForbiddenWordRepository(session).delete(
                interaction.guild.id, normalized
            )
        text = "✅ 금지어를 삭제했습니다." if deleted else "🚫 등록된 금지어를 찾을 수 없습니다."
        await interaction.response.send_message(text, ephemeral=True)

    @forbidden_group.command(name="목록", description="등록된 금지어를 표시합니다.")
    async def list_forbidden_words(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "🚫 서버에서만 사용할 수 있습니다.", ephemeral=True
            )
            return
        async with self.bot.database.session_factory() as session:
            words = await ForbiddenWordRepository(session).list_for_guild(interaction.guild.id)
        if not words:
            await interaction.response.send_message("등록된 금지어가 없습니다.", ephemeral=True)
            return
        description = "\n".join(f"• `{item.word}`" for item in words[:100])
        embed = discord.Embed(
            title=f"🚫 금지어 목록 ({len(words)}개)",
            description=truncate_text(description, 4000),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._moderate_message(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.guild_id is None or "content" not in payload.data:
            return
        if payload.cached_message is not None:
            new_content = payload.data.get("content")
            if new_content == payload.cached_message.content:
                return
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if not hasattr(channel, "fetch_message"):
            return
        try:
            message = await channel.fetch_message(payload.message_id)  # type: ignore[attr-defined]
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        await self._moderate_message(message)

    async def _moderate_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or message.webhook_id is not None
            or message.author.bot
            or not message.content
        ):
            return
        try:
            async with self.bot.database.session_factory() as session:
                entries = await ForbiddenWordRepository(session).list_for_guild(message.guild.id)
            matches = find_forbidden_words(
                message.content, ((entry.word, entry.normalized_word) for entry in entries)
            )
        except Exception:
            logger.exception("Failed to inspect message in guild %s", message.guild.id)
            return
        if not matches:
            return

        deleted = False
        try:
            await message.delete()
            deleted = True
        except discord.NotFound:
            logger.info("Moderated message was already deleted in guild %s", message.guild.id)
        except discord.Forbidden:
            logger.warning("Missing Manage Messages permission in guild %s", message.guild.id)
        except discord.HTTPException:
            logger.exception("Discord API failed while deleting a moderated message")

        await self._warn_user(message, matches)
        try:
            await self._send_audit_log(message, matches, deleted)
        except Exception:
            logger.exception("Failed to process moderation audit log in guild %s", message.guild.id)
        try:
            async with self.bot.database.session_factory() as session:
                await ModerationViolationRepository(session).create(
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    channel_id=message.channel.id,
                    matched_words=matches,
                )
        except Exception:
            logger.exception("Failed to persist moderation violation in guild %s", message.guild.id)

    async def _warn_user(self, message: discord.Message, matches: list[str]) -> None:
        warning = build_user_warning(matches)
        try:
            await message.channel.send(
                f"{message.author.mention}\n{warning}",
                delete_after=5,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not deliver moderation warning in guild %s", message.guild.id)

    async def _send_audit_log(
        self, message: discord.Message, matches: list[str], deleted: bool
    ) -> None:
        async with self.bot.database.session_factory() as session:
            settings = await GuildSettingsRepository(session).get(message.guild.id)  # type: ignore[union-attr]
        if settings is None or settings.moderation_log_channel_id is None:
            return
        channel = self.bot.get_channel(settings.moderation_log_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(settings.moderation_log_channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.warning("Configured moderation log channel is unavailable")
                return
        if not isinstance(channel, discord.abc.Messageable):
            return
        display_name = getattr(message.author, "display_name", message.author.name)
        embed = discord.Embed(title="🚨 금지어 사용 감지", color=discord.Color.red())
        embed.add_field(
            name="사용자",
            value=f"{display_name} (@{message.author.name}) (`{message.author.id}`)",
            inline=False,
        )
        embed.add_field(name="채널", value=f"<#{message.channel.id}>", inline=True)
        embed.add_field(
            name="처리 결과", value="삭제 성공" if deleted else "삭제 실패", inline=True
        )
        embed.add_field(
            name="감지된 금지어",
            value=truncate_text(", ".join(matches), 1000),
            inline=False,
        )
        embed.add_field(
            name="원본 메시지", value=truncate_text(message.content, 1000), inline=False
        )
        embed.set_footer(text=format_kst(utc_now()))
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Failed to send moderation audit log in guild %s", message.guild.id)


async def setup(bot: EsleeBot) -> None:
    await bot.add_cog(ModerationCog(bot))
