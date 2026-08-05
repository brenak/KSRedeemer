import discord
from discord import app_commands
from datetime import datetime
from typing import Callable, Dict, Any, Optional
import asyncio
import random

from dcBot.permissions import check_permissions
from browser_automation.redeem import redeem_giftcode_for_all_players, DEFAULT_KINGDOM
from services.kingshot_client import KingshotClient
from services.kingshot_lookup import lookup_player_nick


def register_add_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
    add_queue,
    kingshot_client: KingshotClient,
):
    @tree.command(name="add", description="Add a new player by ID")
    @app_commands.describe(
        player_id="The player ID to add",
        kingdom=f"The player's kingdom number (defaults to {DEFAULT_KINGDOM})",
        name="Optional name/tag for this player (skips the kingdom-1259 name lookup)",
    )
    async def add_player(
        interaction: discord.Interaction,
        player_id: str,
        kingdom: Optional[str] = None,
        name: Optional[str] = None,
    ):

        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        players = bot_data.get("players", [])
        existing = next((p for p in players if p.get("player_id") == player_id), None)
        if existing:
            await interaction.response.send_message(
                f"❌ Player with ID `{player_id}` already exists as `{existing.get('player_nick', 'N/A')}`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        position = add_queue.position()
        if position > 0:
            await interaction.followup.send(
                f"⏳ Request to add `{player_id}` queued — "
                f"{position} request(s) ahead of you. You'll be notified when done."
            )

        async def do_add():
            try:
                current_players = bot_data.get("players", [])

                # Re-check for duplicate in case another queued add beat us here
                if any(p.get("player_id") == player_id for p in current_players):
                    await interaction.followup.send(
                        f"❌ Player `{player_id}` was already added by a concurrent request."
                    )
                    return

                player_kingdom = kingdom or DEFAULT_KINGDOM
                resolved_nick = name
                if not resolved_nick:
                    resolved_nick = await lookup_player_nick(player_id, player_kingdom, kingshot_client)
                if not resolved_nick:
                    resolved_nick = f"Player {player_id}"

                new_player = {
                    "player_id": player_id,
                    "player_nick": resolved_nick,
                    "kingdom": player_kingdom,
                }
                current_players.append(new_player)
                bot_data["players"] = current_players
                save_bot_data(bot_data)

                gift_code_cache = bot_data.get("gift_code_cache", {})
                valid_codes = [
                    code for code, data in gift_code_cache.items()
                    if data.get("status") == "valid"
                ]

                response_text = ""
                redeemed_codes = bot_data.setdefault("redeemed_codes", {})

                if valid_codes:
                    try:
                        success_count = 0
                        expired_codes = []
                        failed_codes = []

                        response_text += f"🎁 Auto-redeeming `{len(valid_codes)}` active code(s)...\n"
                        for code in valid_codes:
                            await asyncio.sleep(random.uniform(5, 10))
                            result = await redeem_giftcode_for_all_players([new_player], code)

                            if result and len(result) > 0:
                                item = result[0]

                                if item.get("errorCode") == "EXPIRED":
                                    cache = bot_data.setdefault("gift_code_cache", {})
                                    cache[code] = {
                                        **cache.get(code, {}),
                                        "status": "invalid",
                                        "manually_expired": True,
                                        "last_checked": datetime.now().isoformat(),
                                    }
                                    expired_codes.append(code)
                                    continue

                                if item.get("success"):
                                    success_count += 1
                                    code_list = redeemed_codes.setdefault(code, [])
                                    if player_id not in code_list:
                                        code_list.append(player_id)
                                else:
                                    message = item.get("result", {}).get("message") or item.get("message", "Unknown error")
                                    failed_codes.append(f"`{code}`: {message}")

                        response_text += f"✅ Auto-redeemed `{success_count}/{len(valid_codes)}` code(s)\n"
                        if expired_codes:
                            response_text += "⏰ Expired (removed from active list):\n"
                            response_text += "\n".join(f"  • `{c}`" for c in expired_codes) + "\n"
                        if failed_codes:
                            response_text += "❌ Failed:\n"
                            response_text += "\n".join(f"  • {f}" for f in failed_codes) + "\n"

                        if success_count > 0 or expired_codes:
                            bot_data["players"] = current_players
                            bot_data["redeemed_codes"] = redeemed_codes
                            save_bot_data(bot_data)
                    except Exception as e:
                        response_text += f"⚠️ Auto-redeem step failed: {str(e)}\n"
                        print(f"Error during auto-redeem for newly added player {player_id}: {e}")

                final_response = (
                    f"✅ Added player `{player_id}` (Kingdom `{new_player['kingdom']}`) "
                    f"with nick `{new_player['player_nick']}`"
                )
                if response_text:
                    final_response += f"\n\n{response_text}"

                await interaction.followup.send(final_response)

            except Exception as e:
                await interaction.followup.send(f"❌ Error adding player `{player_id}`: {str(e)}")
                print(f"Error in add queue worker for {player_id}: {e}")

        await add_queue.enqueue(do_add())
