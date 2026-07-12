from __future__ import annotations

import discord


def has_management_permission(*, user_id: int, guild_owner_id: int, administrator: bool) -> bool:
    return user_id == guild_owner_id or administrator


def interaction_user_can_manage(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    user = interaction.user
    if guild is None or not isinstance(user, discord.Member):
        return False
    return has_management_permission(
        user_id=user.id,
        guild_owner_id=guild.owner_id,
        administrator=user.guild_permissions.administrator,
    )


async def require_management_permission(interaction: discord.Interaction) -> bool:
    if interaction_user_can_manage(interaction):
        return True
    message = "🚫 해당 명령어를 사용할 권한이 없습니다."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)
    return False
