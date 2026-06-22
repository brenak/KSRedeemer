import aiohttp
import asyncio
import random
import discord
from discord.ext import tasks
from datetime import datetime
from typing import Dict, Any, Callable

from browser_automation.redeem import redeem_giftcode_for_all_players
from config.config import GIFT_CODE_CHECK_INTERVAL_HOURS


class GiftCodeCacheManager:
    def __init__(self, bot: discord.Client, bot_data: Dict[str, Any], save_data_func: Callable[[Dict[str, Any]], None]):
        self.bot = bot
        self.bot_data = bot_data
        self.save_data = save_data_func
        self.api_url = "https://kingshot.net/api/gift-codes"

        self.check_codes.change_interval(hours=self._get_stored_interval())
        self.check_codes.start()

    def _get_stored_interval(self) -> int:
        config = self.bot_data.get("botConfig", {})
        stored = config.get("gift_code_check_interval_hours")
        if stored is not None:
            return max(1, int(stored))
        return GIFT_CODE_CHECK_INTERVAL_HOURS

    def set_interval(self, hours: int) -> None:
        hours = max(1, int(hours))
        if "botConfig" not in self.bot_data:
            self.bot_data["botConfig"] = {}
        self.bot_data["botConfig"]["gift_code_check_interval_hours"] = hours
        self.save_data(self.bot_data)
        self.check_codes.change_interval(hours=hours)

    def unload(self):
        self.check_codes.cancel()

    def _is_code_valid(self, code_data: Dict[str, Any]) -> bool:
        expires_at = code_data.get("expiresAt")
        if expires_at is None:
            return True
        try:
            expiry_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            return expiry_time > datetime.now(expiry_time.tzinfo)
        except (ValueError, AttributeError):
            return False

    @tasks.loop(hours=1)
    async def check_codes(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.api_url) as response:
                    if response.status == 200:
                        data = await response.json()
                        gift_codes = data.get("data", {}).get("giftCodes", [])

                        if "gift_code_cache" not in self.bot_data:
                            self.bot_data["gift_code_cache"] = {}

                        cache = self.bot_data["gift_code_cache"]
                        current_time = datetime.now(datetime.now().astimezone().tzinfo).isoformat()
                        new_codes = []

                        for code_obj in gift_codes:
                            code = code_obj.get("code")
                            if not code:
                                continue

                            is_valid = self._is_code_valid(code_obj)
                            was_new = code not in cache
                            is_manually_expired = cache.get(code, {}).get("manually_expired", False)

                            cache[code] = {
                                "status": "invalid" if is_manually_expired else ("valid" if is_valid else "invalid"),
                                "expires": code_obj.get("expiresAt"),
                                "last_checked": current_time,
                                **({"manually_expired": True} if is_manually_expired else {}),
                            }

                            if was_new and is_valid and not is_manually_expired:
                                new_codes.append(code)

                        # Mark codes that disappeared from the API as invalid
                        codes_from_api = {code_obj.get("code") for code_obj in gift_codes if code_obj.get("code")}
                        for cached_code in list(cache.keys()):
                            if cached_code not in codes_from_api:
                                cache[cached_code]["status"] = "invalid"

                        # Auto-redeem new codes for all players
                        if new_codes:
                            players = self.bot_data.get("players", [])
                            if players:
                                print(f"🎁 Found {len(new_codes)} new code(s). Auto-redeeming for {len(players)} player(s)...")
                                redeemed_codes = self.bot_data.setdefault("redeemed_codes", {})

                                for code in new_codes:
                                    await asyncio.sleep(random.uniform(5, 10))
                                    result = await redeem_giftcode_for_all_players(players, code)

                                    # Track successful redemptions and sync player nicknames
                                    code_list = redeemed_codes.setdefault(code, [])
                                    expired_via_redeem = False
                                    for item in result or []:
                                        if item.get("errorCode") == "EXPIRED":
                                            cache[code]["status"] = "invalid"
                                            cache[code]["manually_expired"] = True
                                            print(f"⏰ Gift code [{code}] reported expired by game — marked invalid.")
                                            expired_via_redeem = True
                                            break

                                        if item.get("success"):
                                            player_id = item.get("player_id")
                                            if player_id and player_id not in code_list:
                                                code_list.append(player_id)

                                        page_nick = item.get("page_player_nick")
                                        item_player_id = item.get("player_id")
                                        if page_nick and item_player_id:
                                            player_to_update = next(
                                                (p for p in players if p.get("player_id") == item_player_id),
                                                None,
                                            )
                                            if player_to_update and player_to_update.get("player_nick") != page_nick:
                                                player_to_update["player_nick"] = page_nick

                                # Send Discord notification
                                try:
                                    config = self.bot_data.get("botConfig", {})
                                    channel_id = config.get("allowed_channel")
                                    if channel_id:
                                        channel = self.bot.get_channel(channel_id)
                                        if channel:
                                            codes_str = ", ".join([f"`{code}`" for code in new_codes])
                                            await channel.send(
                                                f"🎁 **New Gift Code{'s' if len(new_codes) > 1 else ''}!**\n"
                                                f"{codes_str}\n"
                                                f"Auto-redeemed for {len(players)} player{'s' if len(players) > 1 else ''}."
                                            )
                                except Exception as e:
                                    print(f"⚠️ Could not send gift code notification: {e}")

                        self.save_data(self.bot_data)
                        print(f"✅ Gift code cache updated. Found {len(gift_codes)} code(s), {len(new_codes)} new")
                    else:
                        print(f"❌ Failed to check gift codes: HTTP {response.status}")
        except Exception as e:
            print(f"❌ Error checking gift codes: {e}")

    @check_codes.before_loop
    async def before_check_codes(self):
        await self.bot.wait_until_ready()
