"""Automatic daily /sheet sync: every tab + the Analytics tab.

Checked hourly, runs once SHEET_AUTO_SYNC_HOURS have passed since the last
run (persisted in botConfig, so restarts don't trigger extra syncs). Goes
through the add queue like the commands, then posts a compact summary to
the bot channel. A few minutes of slack keeps it at a steady time of day:
without it, the hourly tick that lands exactly N hours later is a hair
short and the run slips an hour every day.
"""

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

import discord
from discord.ext import tasks

from config.config import SHEET_AUTO_SYNC_HOURS
from dcBot.commands.sheetCmd import run_sheet_sync, sheet_targets
from services.mightpulse_client import MightPulseClient
from services.sheet_sync import configured

DUE_SLACK = timedelta(minutes=10)


def next_tick_after(tick: datetime, last: Optional[str]) -> datetime:
    """First hourly check (tick, tick + 1h, ...) at which a sync is due.
    Pure -- tested offline."""
    if not last:
        return tick
    try:
        due = datetime.fromisoformat(last) + timedelta(hours=SHEET_AUTO_SYNC_HOURS) - DUE_SLACK
    except (TypeError, ValueError):
        return tick
    while tick < due:
        tick += timedelta(hours=1)
    return tick


class SheetAutoSyncManager:
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
        if SHEET_AUTO_SYNC_HOURS > 0:
            self.auto_sync.start()

    def _due(self) -> bool:
        last = self.bot_data.get("botConfig", {}).get("last_sheet_auto_sync")
        if not last:
            return True
        try:
            elapsed = datetime.now().astimezone() - datetime.fromisoformat(last)
        except (TypeError, ValueError):
            return True
        return elapsed >= timedelta(hours=SHEET_AUTO_SYNC_HOURS) - DUE_SLACK

    def next_run(self) -> Optional[datetime]:
        """When the next auto-sync will start: the first hourly check at or
        after last run + SHEET_AUTO_SYNC_HOURS (minus the slack). None when
        auto-sync is off or not running."""
        if SHEET_AUTO_SYNC_HOURS <= 0 or not self.auto_sync.is_running():
            return None
        tick = self.auto_sync.next_iteration
        if tick is None:
            return None
        return next_tick_after(tick, self.bot_data.get("botConfig", {}).get("last_sheet_auto_sync"))

    @tasks.loop(hours=1)
    async def auto_sync(self):
        if not configured() or not self.mightpulse_client.configured():
            return
        if not sheet_targets(self.bot_data) or not self._due():
            return

        # Stamp before running so a failing sync waits a full interval
        # rather than retrying (and spending quota) every hour.
        self.bot_data.setdefault("botConfig", {})["last_sheet_auto_sync"] = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )
        self.save_data(self.bot_data)
        await self.add_queue.enqueue(self._run())

    async def _run(self):
        try:
            targets = list(sheet_targets(self.bot_data))
            print(f"📋 Daily sheet sync: {len(targets)} tab(s)")

            async def send(message: str):
                nxt = self.next_run()
                when = f"\n-# Next auto-sync <t:{int(nxt.timestamp())}:R>" if nxt else ""
                await self._notify("📋 **Daily sheet sync**\n" + message + when)

            await run_sheet_sync(self.bot_data, self.mightpulse_client, targets, False, send, compact=True)
        except Exception as e:
            print(f"❌ Daily sheet sync failed: {e}")

    async def _notify(self, message: str):
        try:
            channel_id = self.bot_data.get("botConfig", {}).get("allowed_channel")
            channel = self.bot.get_channel(channel_id) if channel_id else None
            if channel:
                await channel.send(message[:2000])
            else:
                print(message)
        except Exception as e:
            print(f"⚠️ Could not send sheet sync notification: {e}")

    @auto_sync.before_loop
    async def before_auto_sync(self):
        await self.bot.wait_until_ready()
