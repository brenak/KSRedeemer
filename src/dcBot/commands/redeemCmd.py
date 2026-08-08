import discord
from discord import app_commands
from datetime import datetime
from typing import Callable, Dict, Any, Optional

from giftcode_api.redeem import redeem_giftcode_for_all_players
from dcBot.permissions import check_permissions
from services.kingshot_client import KingshotClient
from services.kingshot_lookup import refresh_player_nicks


def register_redeem_command(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
    kingshot_client: KingshotClient,
):

    @tree.command(
        name="redeem",
        description="Redeem a Kingshot gift code for all players or a single player.",
    )
    @app_commands.describe(
        gift_code="The gift code to redeem",
        player_id="Optional: A specific player ID to redeem the code for.",
    )
    async def redeem(
        interaction: discord.Interaction,
        gift_code: str,
        player_id: Optional[str] = None,
    ):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            # Check if code is in cache and marked invalid
            gift_code_cache = bot_data.get("gift_code_cache", {})
            code_cache = gift_code_cache.get(gift_code, {})

            if code_cache and code_cache["status"] == "invalid":
                await interaction.followup.send(
                    f"❌ Gift code `{gift_code}` is expired or invalid (last checked: {code_cache['last_checked']})"
                )
                return

            all_players = bot_data.get("players", [])
            redeemed_codes = bot_data.setdefault("redeemed_codes", {})
            already_redeemed_ids = set(redeemed_codes.get(gift_code, []))

            skipped_count = 0

            if player_id:
                target_player = next(
                    (p for p in all_players if p.get("player_id") == player_id), None
                )
                if not target_player:
                    target_player = {"player_id": player_id, "player_nick": "N/A"}
                if player_id in already_redeemed_ids:
                    await interaction.followup.send(
                        f"⏭️ Player `{player_id}` has already redeemed `{gift_code}`."
                    )
                    return
                players_to_redeem = [target_player]
            else:
                players_to_redeem = [p for p in all_players if p.get("player_id") not in already_redeemed_ids]
                skipped_count = len(all_players) - len(players_to_redeem)

            if not players_to_redeem:
                await interaction.followup.send(
                    f"✅ All players have already redeemed `{gift_code}`."
                )
                return

            results = await redeem_giftcode_for_all_players(players_to_redeem, gift_code)
            failed = 0
            new_redemptions = []

            failed_players = []
            for item in results:
                error_code = item.get("errorCode", "")
                if error_code == "INVALID_CODE":
                    failed += 1
                    failed_players.append(f"❌ Invalid gift code.")
                    break

                if error_code == "EXPIRED":
                    cache = bot_data.setdefault("gift_code_cache", {})
                    cache[gift_code] = {
                        **cache.get(gift_code, {}),
                        "status": "invalid",
                        "manually_expired": True,
                        "last_checked": datetime.now().isoformat(),
                    }
                    save_bot_data(bot_data)
                    failed += 1
                    failed_players.append(f"⏰ Gift code `{gift_code}` has expired and has been removed from the active list.")
                    break

                if error_code == "RATE_LIMITED":
                    failed += 1
                    failed_players.append(f"🚦 {item.get('message', 'Rate limited by the gift-code server.')}")
                    break

                redeemed_player_id = item.get("player_id")

                success = item.get("success")
                if success:
                    if redeemed_player_id:
                        new_redemptions.append(redeemed_player_id)
                    continue

                result = item.get("result", {})
                res_player_id = item.get("player_id", "Unknown")
                res_player_nick = item.get("stored_player_nick", "N/A")
                message = result.get("message", "No message")
                failed += 1

                failed_players.append(f"❌ `{res_player_id}` ({res_player_nick}): {message}")

            if new_redemptions:
                code_list = redeemed_codes.setdefault(gift_code, [])
                for pid in new_redemptions:
                    if pid not in code_list:
                        code_list.append(pid)

            renamed = await refresh_player_nicks(players_to_redeem, kingshot_client)

            if new_redemptions or renamed:
                bot_data["players"] = all_players
                save_bot_data(bot_data)

            response_message = (
                f"🎁 **Redeem Results for `{gift_code}`**\n"
                f"🚀 `{len(results) - failed}/{len(results)}` succeeded!"
            )
            if skipped_count:
                response_message += f"\n⏭️ `{skipped_count}` skipped (already redeemed)"
            response_message += "\n\n"
            response_message += "\n".join(failed_players)

            footer_parts = []
            if new_redemptions:
                footer_parts.append(f"Recorded {len(new_redemptions)} new redemption(s)")
            if renamed:
                footer_parts.append(f"Refreshed {len(renamed)} name(s) from kingshot_web")
            if footer_parts:
                response_message += "\n\n💾 " + " • ".join(footer_parts)

            if len(response_message) > 1900:
                response_message = response_message[:1900] + "\n…(truncated)"

            await interaction.followup.send(response_message)

        except Exception as e:
            error_message = f"❌ Error during redemption: {str(e)}"
            await interaction.followup.send(error_message)
            print(f"Error in redeem command: {e}")
