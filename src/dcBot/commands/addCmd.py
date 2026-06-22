import discord
from discord import app_commands
from datetime import datetime
from typing import Callable, Dict, Any
import asyncio
import random

from dcBot.permissions import check_permissions
from browser_automation.redeem import redeem_giftcode_for_all_players


def register_add_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
):
    @tree.command(name="add", description="Add a new player by ID")
    @app_commands.describe(player_id="The player ID to add")
    async def add_player(interaction: discord.Interaction, player_id: str):
        
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return
        
        await interaction.response.defer(thinking=True)

        try:
            players = bot_data.get("players", [])

            existing = next(
                (p for p in players if p.get("player_id") == player_id), None
            )
            if existing:
                await interaction.followup.send(
                    f"❌ Player with ID `{player_id}` already exists as `{existing.get('player_nick', 'N/A')}`."
                )
                return

            new_player = {"player_id": player_id, "player_nick": f"Player {player_id}"}
            players.append(new_player)
            bot_data["players"] = players
            save_bot_data(bot_data)

            # Auto-redeem all valid codes for the new player
            gift_code_cache = bot_data.get("gift_code_cache", {})
            valid_codes = [
                code for code, data in gift_code_cache.items()
                if data.get("status") == "valid"
            ]

            response_text = ""
            success_count = 0
            expired_codes = []
            failed_codes = []
            redeemed_codes = bot_data.setdefault("redeemed_codes", {})

            if valid_codes:
                response_text += f"🎁 Auto-redeeming `{len(valid_codes)}` active code(s)...\n"
                for code in valid_codes:
                    await asyncio.sleep(random.uniform(5, 10))
                    result = await redeem_giftcode_for_all_players([new_player], code)

                    # Extract player nickname from result if available
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
                            # Track this redemption
                            code_list = redeemed_codes.setdefault(code, [])
                            if player_id not in code_list:
                                code_list.append(player_id)

                            page_nick = item.get("page_player_nick")
                            if page_nick and new_player.get("player_nick") != page_nick:
                                new_player["player_nick"] = page_nick
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

                # Save updated player with any nickname changes, redemption tracking, and cache updates
                if success_count > 0 or expired_codes:
                    bot_data["players"] = players
                    bot_data["redeemed_codes"] = redeemed_codes
                    save_bot_data(bot_data)

            # Build final response with actual nickname
            final_response = f"✅ Added player `{player_id}` with nick `{new_player['player_nick']}`"
            if response_text:
                final_response += f"\n\n{response_text}"

            await interaction.followup.send(final_response)

        except Exception as e:
            error_message = f"❌ Error adding player: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in add command: {e}")