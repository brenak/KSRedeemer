import discord
from discord import app_commands
from datetime import datetime
from typing import Callable, Dict, Any, Optional
import asyncio
import random

from dcBot.permissions import check_permissions
from browser_automation.redeem import redeem_giftcode_for_all_players


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
        gift_code_cache = bot_data.get("gift_code_cache", {})
        valid_codes = [
            code for code, data in gift_code_cache.items()
            if data.get("status") == "valid"
        ]

        if not valid_codes:
            await interaction.followup.send("❌ No active codes in cache to redeem.")
            return

        all_players = bot_data.get("players", [])
        if player_id:
            target = next((p for p in all_players if p.get("player_id") == player_id), None)
            if not target:
                await interaction.followup.send(
                    f"❌ Player `{player_id}` not found in the player list."
                )
                return
            players_to_check = [target]
        else:
            players_to_check = list(all_players)

        if not players_to_check:
            await interaction.followup.send("❌ No players registered.")
            return

        redeemed_codes = bot_data.get("redeemed_codes", {})

        # Build per-code list of players who still need it
        pending: Dict[str, list] = {}
        for code in valid_codes:
            already = set(redeemed_codes.get(code, []))
            missing = [p for p in players_to_check if p.get("player_id") not in already]
            if missing:
                pending[code] = missing

        if not pending:
            scope = f"player `{player_id}`" if player_id else "all players"
            await interaction.followup.send(
                f"✅ {scope.capitalize()} already has all active codes — nothing to redeem."
            )
            return

        total_redemptions = sum(len(v) for v in pending.values())
        scope = f"player `{player_id}`" if player_id else f"{len(players_to_check)} player(s)"

        position = add_queue.position()
        if position > 0:
            await interaction.followup.send(
                f"⏳ Catchup for {scope} queued — {position} request(s) ahead. "
                f"Will redeem {len(pending)} code(s) for up to {total_redemptions} redemption(s)."
            )

        async def do_catchup():
            try:
                redeemed = bot_data.setdefault("redeemed_codes", {})
                expired_codes = []
                code_results = []  # [(code, succeeded, failed_msgs)]
                nick_updated = False

                for code, players in pending.items():
                    await asyncio.sleep(random.uniform(5, 10))
                    result = await redeem_giftcode_for_all_players(players, code)

                    succeeded = 0
                    failed_msgs = []
                    code_list = redeemed.setdefault(code, [])

                    for item in result or []:
                        error_code = item.get("errorCode", "")

                        if error_code == "EXPIRED":
                            cache = bot_data.setdefault("gift_code_cache", {})
                            cache[code] = {
                                **cache.get(code, {}),
                                "status": "invalid",
                                "manually_expired": True,
                                "last_checked": datetime.now().isoformat(),
                            }
                            expired_codes.append(code)
                            break

                        if error_code == "INVALID_CODE":
                            failed_msgs.append("Invalid code")
                            break

                        pid = item.get("player_id")
                        if item.get("success"):
                            succeeded += 1
                            if pid and pid not in code_list:
                                code_list.append(pid)
                        else:
                            msg = item.get("result", {}).get("message") or item.get("message", "Unknown error")
                            nick = item.get("result", {}).get("player_nick") or pid or "?"
                            failed_msgs.append(f"`{nick}`: {msg}")

                        page_nick = item.get("page_player_nick")
                        if page_nick and pid:
                            player_obj = next(
                                (p for p in all_players if p.get("player_id") == pid), None
                            )
                            if player_obj and player_obj.get("player_nick") != page_nick:
                                player_obj["player_nick"] = page_nick
                                nick_updated = True

                    if code not in expired_codes:
                        code_results.append((code, succeeded, failed_msgs))

                save_bot_data(bot_data)

                # --- Build response ---
                lines = [f"🔄 **Catchup complete** for {scope}\n"]

                for code, succeeded, failed_msgs in code_results:
                    total = len(pending[code])
                    if succeeded == total:
                        lines.append(f"✅ `{code}` — {succeeded}/{total} redeemed")
                    elif succeeded > 0:
                        lines.append(f"⚠️ `{code}` — {succeeded}/{total} redeemed")
                        for f in failed_msgs:
                            lines.append(f"   • {f}")
                    else:
                        lines.append(f"❌ `{code}` — 0/{total} redeemed")
                        for f in failed_msgs:
                            lines.append(f"   • {f}")

                for code in expired_codes:
                    lines.append(f"⏰ `{code}` — expired, removed from active list")

                if nick_updated:
                    lines.append("\n💾 Updated player name(s) from game page")

                response = "\n".join(lines)
                if len(response) > 1900:
                    response = response[:1900] + "\n…(truncated)"

                await interaction.followup.send(response)

            except Exception as e:
                await interaction.followup.send(f"❌ Error during catchup: {str(e)}")
                print(f"Error in catchup worker: {e}")

        await add_queue.enqueue(do_catchup())
