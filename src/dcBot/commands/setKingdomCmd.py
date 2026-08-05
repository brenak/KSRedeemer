import discord
from discord import app_commands
from typing import Callable, Dict, Any

from dcBot.permissions import check_permissions


def register_set_kingdom_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
):
    @tree.command(name="setkingdom", description="Update a player's kingdom number")
    @app_commands.describe(
        player_id="The player ID to update",
        kingdom="The player's new kingdom number",
    )
    async def set_kingdom(interaction: discord.Interaction, player_id: str, kingdom: str):

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

            old_kingdom = player.get("kingdom", "N/A")
            player["kingdom"] = kingdom
            save_bot_data(bot_data)

            await interaction.followup.send(
                f"✅ Updated `{player_id}` ({player.get('player_nick', 'N/A')}) "
                f"kingdom: `{old_kingdom}` → `{kingdom}`"
            )

        except Exception as e:
            error_message = f"❌ Error updating kingdom: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in setkingdom command: {e}")
