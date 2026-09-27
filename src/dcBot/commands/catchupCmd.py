import discord
from discord import app_commands
from typing import Callable, Dict, Any, Optional

from dcBot.active_codes import active_codes, build_pending, redeem_pending, format_code_results
from dcBot.permissions import check_permissions
from dcBot.interaction_reply import send_followup


def register_catchup_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
    add_queue,
):
    @tree.command(
        name="catchup",
        description="Redeem any active codes a player (or all players) haven't received yet.",
    )
    @app_commands.describe(player_id="Optional: only catch up a specific player ID.")
    async def catchup(interaction: discord.Interaction, player_id: Optional[str] = None):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        # --- Build the work list before queuing ---
        if not active_codes(bot_data):
            await send_followup(interaction, "❌ No active codes in cache to redeem.")
            return

        all_players = bot_data.get("players", [])
        if player_id:
            target = next((p for p in all_players if p.get("player_id") == player_id), None)
            if not target:
                await send_followup(interaction,
                    f"❌ Player `{player_id}` not found in the player list."
                )
                return
            players_to_check = [target]
        else:
            players_to_check = list(all_players)

        if not players_to_check:
            await send_followup(interaction, "❌ No players registered.")
            return

        pending = build_pending(bot_data, players_to_check)

        if not pending:
            scope = f"player `{player_id}`" if player_id else "all players"
            await send_followup(interaction,
                f"✅ {scope.capitalize()} already has all active codes — nothing to redeem."
            )
            return

        total_redemptions = sum(len(v) for v in pending.values())
        scope = f"player `{player_id}`" if player_id else f"{len(players_to_check)} player(s)"

        position = add_queue.position()
        if position > 0:
            await send_followup(interaction,
                f"⏳ Catchup for {scope} queued — {position} request(s) ahead. "
                f"Will redeem {len(pending)} code(s) for up to {total_redemptions} redemption(s)."
            )

        async def do_catchup():
            try:
                code_results, expired_codes = await redeem_pending(bot_data, pending, save_bot_data)

                lines = [f"🔄 **Catchup complete** for {scope}\n"]
                lines.extend(format_code_results(code_results, expired_codes, pending))

                response = "\n".join(lines)
                if len(response) > 1900:
                    response = response[:1900] + "\n…(truncated)"

                await send_followup(interaction, response)

            except Exception as e:
                await send_followup(interaction, f"❌ Error during catchup: {str(e)}")
                print(f"Error in catchup worker: {e}")

        await add_queue.enqueue(do_catchup())
