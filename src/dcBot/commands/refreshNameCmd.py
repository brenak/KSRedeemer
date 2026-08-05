import discord
from discord import app_commands
from typing import Callable, Dict, Any

from dcBot.permissions import check_permissions
from services.kingshot_client import KingshotClient
from services.kingshot_lookup import lookup_player_nick, LOOKUP_KINGDOM


def register_refresh_name_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
    kingshot_client: KingshotClient,
):
    @tree.command(
        name="refreshname",
        description=f"Re-look up a kingdom {LOOKUP_KINGDOM} player's name from kingshot_web",
    )
    @app_commands.describe(player_id="The player ID to refresh")
    async def refresh_name(interaction: discord.Interaction, player_id: str):

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

            player_kingdom = player.get("kingdom", LOOKUP_KINGDOM)
            if player_kingdom != LOOKUP_KINGDOM:
                await interaction.followup.send(
                    f"❌ `/refreshname` only works for kingdom `{LOOKUP_KINGDOM}` players "
                    f"— `{player_id}` is kingdom `{player_kingdom}`. Use `/setname` instead."
                )
                return

            if not kingshot_client.configured():
                await interaction.followup.send(
                    "❌ kingshot_web lookup isn't configured on this bot "
                    "(KINGSHOT_URL / KINGSHOT_USERNAME / KINGSHOT_PASSWORD)."
                )
                return

            looked_up = await lookup_player_nick(player_id, player_kingdom, kingshot_client)
            if not looked_up:
                await interaction.followup.send(
                    f"⚠️ `{player_id}` isn't in kingshot_web yet (not Intel-confirmed) "
                    f"— name unchanged (`{player.get('player_nick', 'N/A')}`)."
                )
                return

            old_nick = player.get("player_nick", "N/A")
            if old_nick == looked_up:
                await interaction.followup.send(f"✅ `{player_id}` name is already up to date: `{old_nick}`")
                return

            player["player_nick"] = looked_up
            save_bot_data(bot_data)

            await interaction.followup.send(
                f"✅ Updated `{player_id}` name: `{old_nick}` → `{looked_up}`"
            )

        except Exception as e:
            error_message = f"❌ Error refreshing name: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in refreshname command: {e}")
