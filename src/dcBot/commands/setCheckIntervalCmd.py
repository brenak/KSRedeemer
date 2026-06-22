import discord
from discord import app_commands
from typing import Dict, Any

from dcBot.permissions import check_permissions


def register_set_check_interval_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    cache_manager,
):
    @tree.command(
        name="set-check-interval",
        description="Set how often (in hours) the bot checks for new gift codes.",
    )
    @app_commands.describe(hours="Check interval in hours (minimum 1)")
    async def set_check_interval(interaction: discord.Interaction, hours: int):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        if hours < 1:
            await interaction.response.send_message(
                "❌ Interval must be at least 1 hour.", ephemeral=True
            )
            return

        cache_manager.set_interval(hours)
        await interaction.response.send_message(
            f"✅ Gift code check interval updated to **{hours} hour(s)**. "
            f"Takes effect on the next check cycle."
        )
