import discord
from discord import app_commands
from typing import Callable, Dict, Any

from dcBot.permissions import check_permissions
from dcBot.interaction_reply import send_followup
from services.mightpulse_client import MightPulseClient, MightPulseError
from services.player_sync import apply_player_info, lookup_player, refresh_all_players


def register_refresh_name_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
    add_queue,
    mightpulse_client: MightPulseClient,
):
    @tree.command(
        name="refreshname",
        description="Re-look up a player's name, kingdom and alliance from MightPulse (or 'all')",
    )
    @app_commands.describe(player_id="The player ID to refresh, or 'all' for every registered player")
    async def refresh_name(interaction: discord.Interaction, player_id: str):

        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        if not mightpulse_client.configured():
            await send_followup(interaction, "❌ MightPulse lookup isn't configured on this bot (MIGHTPULSE_API_KEY).")
            return

        if player_id.strip().lower() == "all":
            await _refresh_all(interaction)
            return

        try:
            players = bot_data.get("players", [])
            player = next((p for p in players if p.get("player_id") == player_id), None)

            if not player:
                await send_followup(interaction,
                    f"❌ No player found with ID `{player_id}`. Use `/list` to see all players."
                )
                return

            info = await lookup_player(player_id, mightpulse_client)
            if not info:
                await send_followup(interaction,
                    f"⚠️ `{player_id}` wasn't found on MightPulse — unchanged (`{player.get('player_nick', 'N/A')}`)."
                )
                return

            # An explicit single-player refresh means "use their in-game
            # name", so it overrides a custom name from /setname or /add.
            player.pop("nick_locked", None)
            changes = apply_player_info(player, info)
            save_bot_data(bot_data)

            if not changes:
                await send_followup(interaction,
                    f"✅ `{player_id}` is already up to date: `{player.get('player_nick', 'N/A')}`"
                )
                return

            await send_followup(interaction,
                f"✅ Updated `{player_id}`:\n" + "\n".join(f"• {c}" for c in changes)
            )

        except MightPulseError as e:
            await send_followup(interaction, f"❌ MightPulse lookup failed: {e}")
        except Exception as e:
            await send_followup(interaction, f"❌ Error refreshing name: {str(e)}")
            print(f"Error in refreshname command: {e}")

    async def _refresh_all(interaction: discord.Interaction):
        count = len(bot_data.get("players", []))
        if not count:
            await send_followup(interaction, "❌ No players registered.")
            return

        position = add_queue.position()
        if position > 0:
            await send_followup(interaction,
                f"⏳ Refresh of {count} player(s) queued — {position} request(s) ahead."
            )

        async def do_refresh():
            try:
                report = await refresh_all_players(bot_data, mightpulse_client, add_new_members=False)
                save_bot_data(bot_data)

                lines = [f"🔃 **Refreshed {count} player(s) from MightPulse**"]
                lines.extend(report.summary_lines() or ["✅ Everything already up to date"])
                response = "\n".join(lines)
                if len(response) > 1900:
                    response = response[:1900] + "\n…(truncated)"
                await send_followup(interaction, response)
            except Exception as e:
                await send_followup(interaction, f"❌ Error refreshing names: {str(e)}")
                print(f"Error in refreshname all: {e}")

        await add_queue.enqueue(do_refresh())
