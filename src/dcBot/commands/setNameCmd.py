import discord
from discord import app_commands
from typing import Callable, Dict, Any

from dcBot.permissions import check_permissions


def register_set_name_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
):
    @tree.command(name="setname", description="Manually set/tag a player's name")
    @app_commands.describe(
        player_id="The player ID to update",
        name="The name/tag to set",
    )
    async def set_name(interaction: discord.Interaction, player_id: str, name: str):

        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            players = bot_data.get("players", [])
            player = next((p for p in players if p.get("player_id") == player_id), None)

            if not player:
                await interaction.followup.send(
                    f"❌ No player found with ID `{player_id}`. Use `/list` to see all players."
                )
                return

            old_nick = player.get("player_nick", "N/A")
            player["player_nick"] = name
            save_bot_data(bot_data)

            await interaction.followup.send(
                f"✅ Updated `{player_id}` name: `{old_nick}` → `{name}`"
            )

        except Exception as e:
            error_message = f"❌ Error updating name: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in setname command: {e}")
