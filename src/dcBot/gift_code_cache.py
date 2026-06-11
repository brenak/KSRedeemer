import aiohttp
import asyncio
import random
import discord
from discord.ext import tasks
from datetime import datetime
from typing import Dict, Any, Callable

from browser_automation.redeem import redeem_giftcode_for_all_players


class GiftCodeCacheManager:
    def __init__(self, bot: discord.Client, bot_data: Dict[str, Any], save_data_func: Callable[[Dict[str, Any]], None]):
        self.bot = bot
        self.bot_data = bot_data
        self.save_data = save_data_func
        self.api_url = "https://kingshot.net/api/gift-codes"
        self.check_codes.start()

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

    @tasks.loop(hours=6)
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

                            cache[code] = {
                                "status": "valid" if is_valid else "invalid",
                                "expires": code_obj.get("expiresAt"),
                                "last_checked": current_time
                            }

                            if was_new and is_valid:
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

                                    # Track successful redemptions
                                    code_list = redeemed_codes.setdefault(code, [])
                                    for item in result or []:
                                        if item.get("success"):
                                            player_id = item.get("player_id")
                                            if player_id and player_id not in code_list:
                                                code_list.append(player_id)

                        self.save_data(self.bot_data)
                        print(f"✅ Gift code cache updated. Found {len(gift_codes)} code(s), {len(new_codes)} new")
                    else:
                        print(f"❌ Failed to check gift codes: HTTP {response.status}")
        except Exception as e:
            print(f"❌ Error checking gift codes: {e}")

    @check_codes.before_loop
    async def before_check_codes(self):
        await self.bot.wait_until_ready()
