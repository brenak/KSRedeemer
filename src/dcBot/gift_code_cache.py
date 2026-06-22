import aiohttp
import asyncio
import random
import re
import discord
from discord.ext import tasks
from datetime import datetime
from typing import Dict, Any, Callable, List, Optional

from browser_automation.redeem import redeem_giftcode_for_all_players
from config.config import GIFT_CODE_CHECK_INTERVAL_HOURS

WIKI_URL = "https://kingshotwiki.com/giftcodes/"


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

    async def _fetch_wiki_codes(self, session: aiohttp.ClientSession) -> Optional[List[str]]:
        """
        Scrape active codes from the wiki gift code page.
        Returns a list of code strings on success, or None if the fetch failed
        (so callers can distinguish "no codes listed" from "fetch error").
        """
        try:
            async with session.get(
                WIKI_URL,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status != 200:
                    print(f"⚠️ Wiki gift code page returned HTTP {response.status}")
                    return None
                html = await response.text()

            # Find the "Active Codes" heading then the first <ul> after it
            match = re.search(
                r'Active\s+Codes.*?<ul[^>]*>(.*?)</ul>',
                html,
                re.DOTALL | re.IGNORECASE,
            )
            if not match:
                print("⚠️ Could not find 'Active Codes' section on wiki page")
                return []

            ul_html = match.group(1)
            items = re.findall(r'<li[^>]*>(.*?)</li>', ul_html, re.DOTALL)
            codes = []
            for item in items:
                # Strip inner HTML tags (copy buttons etc.) then trim whitespace
                text = re.sub(r'<[^>]+>', '', item).strip()
                # Validate: alphanumeric only, 3–30 chars (covers all known code formats)
                if text and re.fullmatch(r'[A-Za-z0-9]{3,30}', text):
                    codes.append(text)

            return codes

        except Exception as e:
            print(f"⚠️ Failed to fetch wiki gift codes: {e}")
            return None

    @tasks.loop(hours=1)
    async def check_codes(self):
        try:
            async with aiohttp.ClientSession() as session:
                # --- Fetch from API ---
                api_gift_codes = []
                try:
                    async with session.get(self.api_url) as response:
                        if response.status == 200:
                            data = await response.json()
                            api_gift_codes = data.get("data", {}).get("giftCodes", [])
                        else:
                            print(f"❌ Failed to check gift codes from API: HTTP {response.status}")
                except Exception as e:
                    print(f"❌ API gift code fetch error: {e}")

                # --- Fetch from wiki ---
                wiki_codes = await self._fetch_wiki_codes(session)
                # None  → fetch failed (be conservative, don't invalidate wiki codes)
                # []    → fetch succeeded but no active codes listed
                # [..] → codes found

                # --- Merge: API first, then wiki-only additions ---
                api_code_keys = {obj.get("code") for obj in api_gift_codes if obj.get("code")}
                gift_codes = list(api_gift_codes)
                wiki_only_count = 0
                if wiki_codes:
                    for code in wiki_codes:
                        if code not in api_code_keys:
                            gift_codes.append({"code": code, "expiresAt": None, "_source": "wiki"})
                            wiki_only_count += 1

                if not gift_codes:
                    print("⚠️ No gift codes found from any source — skipping cache update")
                    return

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
                    source = "wiki" if code_obj.get("_source") == "wiki" else "api"

                    cache[code] = {
                        "status": "invalid" if is_manually_expired else ("valid" if is_valid else "invalid"),
                        "expires": code_obj.get("expiresAt"),
                        "last_checked": current_time,
                        "source": source,
                        **({"manually_expired": True} if is_manually_expired else {}),
                    }

                    if was_new and is_valid and not is_manually_expired:
                        new_codes.append(code)

                # --- Invalidate codes that have disappeared from their source ---
                # If the wiki fetch failed, preserve wiki-only codes (don't invalidate them).
                # If the wiki fetch succeeded (even empty), codes absent from both are gone.
                wiki_fetch_ok = wiki_codes is not None
                all_seen = {obj.get("code") for obj in gift_codes if obj.get("code")}

                for cached_code in list(cache.keys()):
                    if cached_code in all_seen:
                        continue
                    cached_source = cache[cached_code].get("source", "api")
                    if cached_source == "wiki" and not wiki_fetch_ok:
                        continue  # wiki is down — preserve this code
                    if not cache[cached_code].get("manually_expired"):
                        cache[cached_code]["status"] = "invalid"

                # --- Auto-redeem new codes ---
                if new_codes:
                    players = self.bot_data.get("players", [])
                    if players:
                        print(f"🎁 Found {len(new_codes)} new code(s). Auto-redeeming for {len(players)} player(s)...")
                        redeemed_codes = self.bot_data.setdefault("redeemed_codes", {})

                        for code in new_codes:
                            await asyncio.sleep(random.uniform(5, 10))
                            result = await redeem_giftcode_for_all_players(players, code)

                            code_list = redeemed_codes.setdefault(code, [])
                            for item in result or []:
                                if item.get("errorCode") == "EXPIRED":
                                    cache[code]["status"] = "invalid"
                                    cache[code]["manually_expired"] = True
                                    print(f"⏰ Gift code [{code}] reported expired by game — marked invalid.")
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
                                    codes_str = ", ".join([f"`{c}`" for c in new_codes])
                                    await channel.send(
                                        f"🎁 **New Gift Code{'s' if len(new_codes) > 1 else ''}!**\n"
                                        f"{codes_str}\n"
                                        f"Auto-redeemed for {len(players)} player{'s' if len(players) > 1 else ''}."
                                    )
                        except Exception as e:
                            print(f"⚠️ Could not send gift code notification: {e}")

                self.save_data(self.bot_data)
                print(
                    f"✅ Gift code cache updated. "
                    f"API: {len(api_gift_codes)}, Wiki-only: {wiki_only_count}, "
                    f"New: {len(new_codes)}"
                )

        except Exception as e:
            print(f"❌ Error checking gift codes: {e}")

    @check_codes.before_loop
    async def before_check_codes(self):
        await self.bot.wait_until_ready()
