"""Tracked-alliance sync: the daily automatic roster sync, plus onboarding
(redeeming every active code) for members it newly registers.

The daily check runs hourly but only acts once 24h have passed since the
last run (persisted in botConfig), so bot restarts don't trigger extra syncs.
The sync itself costs one MightPulse request per tracked alliance.
"""

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List

import discord
from discord.ext import tasks

from dcBot.active_codes import build_pending, format_code_results, redeem_pending
from services.mightpulse_client import MightPulseClient
from services.player_sync import sync_tracked_alliances, tracked_alliances

AUTO_SYNC_INTERVAL = timedelta(hours=24)


async def onboard_new_members(
    bot_data: Dict[str, Any],
    new_players: List[Dict[str, Any]],
    save_bot_data: Callable[[Dict[str, Any]], None],
) -> List[str]:
    """Redeem every active code for newly-registered players (same as /add).
    Returns report lines (empty if nothing to redeem)."""
    if not new_players:
        return []
    pending = build_pending(bot_data, new_players)
    if not pending:
        return []
    code_results, expired_codes = await redeem_pending(bot_data, pending, save_bot_data)
    return ["🎁 Active codes for new members:"] + format_code_results(code_results, expired_codes, pending)


class AllianceSyncManager:
    def __init__(
        self,
        bot: discord.Client,
        bot_data: Dict[str, Any],
        save_data_func: Callable[[Dict[str, Any]], None],
        mightpulse_client: MightPulseClient,
        add_queue,
    ):
        self.bot = bot
        self.bot_data = bot_data
        self.save_data = save_data_func
        self.mightpulse_client = mightpulse_client
        self.add_queue = add_queue
        self.daily_sync.start()

    def _due(self) -> bool:
        last = self.bot_data.get("botConfig", {}).get("last_alliance_auto_sync")
        if not last:
            return True
        try:
            return datetime.now().astimezone() - datetime.fromisoformat(last) >= AUTO_SYNC_INTERVAL
        except (TypeError, ValueError):
            return True

    @tasks.loop(hours=1)
    async def daily_sync(self):
        if not tracked_alliances(self.bot_data) or not self.mightpulse_client.configured():
            return
        if not self._due():
            return

        # Stamp before running so a failing sync waits a day rather than
        # retrying (and spending quota) every hour.
        self.bot_data.setdefault("botConfig", {})["last_alliance_auto_sync"] = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
        self.save_data(self.bot_data)
        # Through the add queue so onboarding redemptions don't overlap
        # /add, /catchup etc. hitting the gift-code API at the same time.
        await self.add_queue.enqueue(self._run())

    async def _run(self):
        try:
            report = await sync_tracked_alliances(self.bot_data, self.mightpulse_client, add_new_members=True)
            self.save_data(self.bot_data)
            onboard_lines = await onboard_new_members(self.bot_data, report.added, self.save_data)
            print(f"🏰 Daily alliance sync: {report.alliances_synced} synced, {len(report.added)} added")

            if report.added or report.missing_alliances or report.errors or report.rate_limited:
                lines = ["🏰 **Daily alliance sync**"] + report.summary_lines()
                if report.added:
                    lines.append("New: " + ", ".join(f"`{p['player_nick']}`" for p in report.added[:20])
                                 + (f" (+{len(report.added) - 20} more)" if len(report.added) > 20 else ""))
                lines.extend(onboard_lines)
                await self._notify("\n".join(lines))
        except Exception as e:
            print(f"❌ Daily alliance sync failed: {e}")

    async def _notify(self, message: str):
        try:
            channel_id = self.bot_data.get("botConfig", {}).get("allowed_channel")
            channel = self.bot.get_channel(channel_id) if channel_id else None
            if channel:
                if len(message) > 1900:
                    message = message[:1900] + "\n…(truncated)"
                await channel.send(message)
        except Exception as e:
            print(f"⚠️ Could not send alliance sync notification: {e}")

    @daily_sync.before_loop
    async def before_daily_sync(self):
        await self.bot.wait_until_ready()
