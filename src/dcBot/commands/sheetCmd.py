"""/sheet -- alliance roster Google Sheet sync (services/sheet_sync.py).

One tab per alliance, all in the same spreadsheet (GOOGLE_SHEET_ID). The
alliance -> tab list lives in botData.json["sheet_targets"], seeded from
SHEET_DEFAULT_KINGDOM / _TAG / _TAB the first time it's needed, and managed
with /sheet add / remove / list.
"""

import asyncio

import discord
from discord import app_commands
from typing import Any, Awaitable, Callable, Dict, List, Optional

from config.config import SHEET_AUTO_SYNC_HOURS, SHEET_DEFAULT_KINGDOM, SHEET_DEFAULT_TAB, SHEET_DEFAULT_TAG
from dcBot.interaction_reply import send_followup
from dcBot.permissions import check_channel_only, check_permissions
from services.mightpulse_client import MightPulseClient
from services.sheet_analytics import ANALYTICS_TAB, RESERVED_TABS, update_analytics
from services.power_growth import PowerContext, prepare_power, take_checkpoint
from services.sheet_sync import (
    SheetSyncError,
    configured,
    open_spreadsheet,
    prefetch_rosters,
    superseded_members,
    sync_alliance_sheet,
)

# Rough per-request cost, for the "this will take ~N min" estimate.
SECONDS_PER_LOOKUP = 1.1


def sheet_targets(bot_data: Dict[str, Any]) -> List[Dict[str, str]]:
    if "sheet_targets" not in bot_data:
        bot_data["sheet_targets"] = (
            [{"kid": SHEET_DEFAULT_KINGDOM, "tag": SHEET_DEFAULT_TAG, "tab": SHEET_DEFAULT_TAB}]
            if SHEET_DEFAULT_KINGDOM and SHEET_DEFAULT_TAG and SHEET_DEFAULT_TAB
            else []
        )
    return bot_data["sheet_targets"]


def _find_target(bot_data: Dict[str, Any], tab: str) -> Optional[Dict[str, str]]:
    return next((t for t in sheet_targets(bot_data) if t["tab"].lower() == tab.strip().lower()), None)


def _describe(t: Dict[str, str]) -> str:
    return f"tab `{t['tab']}` ← `[{t['tag']}]` kingdom `{t['kid']}`"


async def load_power(bot_data: Dict[str, Any], roster_cache: Dict[Any, Any], record: bool) -> Optional[PowerContext]:
    """Read the Power History tab (and, when `record`, add today's snapshot
    from the rosters already fetched) and build the growth context. Growth
    tracking is best-effort: a failure here is logged and the sync goes on
    without growth columns rather than failing."""
    try:
        sh = await asyncio.to_thread(open_spreadsheet)
        superseded = superseded_members(roster_cache)
        ctx = await asyncio.to_thread(prepare_power, sh, bot_data, roster_cache, superseded, record)
        if ctx.recorded is not None:
            what = (f"{ctx.recorded} players recorded" if ctx.recorded
                    else "already recorded today, nothing new")
            print(f"📈 Power snapshot {ctx.today}: {what} (history: {ctx.history_days} day(s))")
        return ctx
    except Exception as e:
        print(f"⚠️ Power growth tracking skipped: {e}")
        return None


async def run_analytics(
    bot_data: Dict[str, Any],
    client: MightPulseClient,
    roster_cache: Optional[Dict[Any, Any]] = None,
    power_ctx: Optional[PowerContext] = None,
) -> str:
    """Refresh the Analytics tab for every target; returns a status line.
    Without a power context (/sheet analytics on its own), fetches the
    rosters and reads growth history itself -- read-only, no snapshot."""
    try:
        if power_ctx is None:
            roster_cache = roster_cache if roster_cache is not None else {}
            await prefetch_rosters(client, list(sheet_targets(bot_data)), roster_cache)
            power_ctx = await load_power(bot_data, roster_cache, record=False)
        summary = await update_analytics(client, list(sheet_targets(bot_data)), roster_cache, power_ctx)
        print(summary.replace("`", ""))
        return summary
    except SheetSyncError as e:
        print(f"❌ Analytics: {e}")
        return f"❌ Analytics: {e}"
    except Exception as e:
        print(f"Error updating analytics: {e}")
        return f"❌ Analytics: {str(e)}"


async def run_sheet_sync(
    bot_data: Dict[str, Any],
    client: MightPulseClient,
    targets: List[Dict[str, str]],
    dry_run: bool,
    send: Callable[[str], Awaitable[None]],
    compact: bool = False,
) -> None:
    """Sync each target tab in turn, then (unless dry run) the Analytics
    tab. Used by /sheet sync (a full message per tab, sent as it finishes)
    and the daily auto-sync (compact: one combined message at the end).
    Never raises -- per-tab failures are reported and the rest continue."""
    player_cache: Dict[int, Any] = {}  # shared so no one is looked up twice
    roster_cache: Dict[Any, Any] = {}  # reused by the Analytics tab below
    lines: List[str] = []
    # Every tracked roster up front -- even for a one-tab sync -- so someone
    # who switched between two tracked alliances isn't left active on the
    # old tab while its cached roster still lists them.
    await prefetch_rosters(client, list(sheet_targets(bot_data)), roster_cache)
    # Today's power snapshot (real runs only) + growth baselines.
    power_ctx = await load_power(bot_data, roster_cache, record=not dry_run)
    for t in targets:
        try:
            result = await sync_alliance_sheet(
                client, t["kid"], t["tag"], t["tab"],
                dry_run=dry_run, player_cache=player_cache, roster_cache=roster_cache, power_ctx=power_ctx,
            )
            print(f"📋 Sheet sync {t['tab']}{' (dry run)' if dry_run else ''}: {result.compact_summary()}")
            if compact:
                lines.append(f"✅ `{t['tab']}` — {result.compact_summary()}")
                continue
            header = (
                f"📋 **[dry run — nothing written] {_describe(t)}**"
                if dry_run else f"📋 **Synced {_describe(t)}**"
            )
            message = "\n".join([header] + result.summary_lines())
        except SheetSyncError as e:
            message = f"❌ {_describe(t)}: {e}"
            print(f"❌ Sheet sync {t['tab']}: {e}")
        except Exception as e:
            message = f"❌ {_describe(t)}: {str(e)}"
            print(f"Error in sheet sync ({t['tab']}): {e}")
        if compact:
            lines.append(message)
            continue
        if len(message) > 1900:
            message = message[:1900] + "\n…(truncated)"
        await send(message)

    # Analytics covers every target, not just the ones synced now; rosters
    # fetched above are reused, the rest cost one call each.
    if not dry_run:
        analytics = await run_analytics(bot_data, client, roster_cache, power_ctx)
        if compact:
            lines.append(analytics)
        else:
            await send(analytics)

    if compact and lines:
        message = "\n".join(lines)
        await send(message[:1900] + ("\n…(truncated)" if len(message) > 1900 else ""))


def register_sheet_commands(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
    add_queue,
    mightpulse_client: MightPulseClient,
):
    group = app_commands.Group(name="sheet", description="Alliance roster Google Sheet sync")

    async def tab_autocomplete(interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{t['tab']} ([{t['tag']}] {t['kid']})", value=t["tab"])
            for t in sheet_targets(bot_data)
            if current.lower() in t["tab"].lower()
        ][:25]

    @group.command(name="sync", description="Sync alliance roster tab(s) against live MightPulse data")
    @app_commands.describe(
        tab="Only sync this tab (default: every configured tab)",
        dry_run="Preview the counts without writing anything to the sheet",
    )
    @app_commands.autocomplete(tab=tab_autocomplete)
    async def sheet_sync(interaction: discord.Interaction, tab: Optional[str] = None, dry_run: bool = False):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        if not configured():
            await send_followup(interaction,
                "❌ Sheet sync isn't configured on this bot (GOOGLE_SHEETS_CREDENTIALS_PATH / GOOGLE_SHEET_ID)."
            )
            return
        if not mightpulse_client.configured():
            await send_followup(interaction, "❌ MightPulse isn't configured on this bot (MIGHTPULSE_API_KEY).")
            return

        if tab:
            target = _find_target(bot_data, tab)
            if not target:
                await send_followup(interaction, f"❌ No sheet target for tab `{tab}`. See `/sheet list`.")
                return
            targets = [target]
        else:
            targets = list(sheet_targets(bot_data))
            if not targets:
                await send_followup(interaction, "❌ No sheet targets configured. Add one with `/sheet add`.")
                return

        position = add_queue.position()
        queued = f" — {position} request(s) ahead" if position > 0 else ""
        tabs = ", ".join("`" + t["tab"] + "`" for t in targets)
        await send_followup(interaction,
            f"📋 {'Dry run of ' if dry_run else ''}sheet sync started for {tabs}{queued}. "
            f"One MightPulse lookup per member (~{SECONDS_PER_LOOKUP:.0f}s each), "
            f"so expect a couple of minutes per tab."
        )

        async def do_sync():
            async def send(message: str):
                await send_followup(interaction, message)

            await run_sheet_sync(bot_data, mightpulse_client, targets, dry_run, send)

        await add_queue.enqueue(do_sync())

    async def _run_analytics() -> str:
        return await run_analytics(bot_data, mightpulse_client)

    @group.command(name="analytics", description=f"Refresh the {ANALYTICS_TAB} tab (one roster request per alliance)")
    async def sheet_analytics(interaction: discord.Interaction):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        if not configured():
            await send_followup(interaction,
                "❌ Sheet sync isn't configured on this bot (GOOGLE_SHEETS_CREDENTIALS_PATH / GOOGLE_SHEET_ID)."
            )
            return
        if not mightpulse_client.configured():
            await send_followup(interaction, "❌ MightPulse isn't configured on this bot (MIGHTPULSE_API_KEY).")
            return

        async def do_analytics():
            await send_followup(interaction, await _run_analytics())

        await add_queue.enqueue(do_analytics())

    @group.command(name="checkpoint", description="Start measuring power growth from now (run right after KvK)")
    async def sheet_checkpoint(interaction: discord.Interaction):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        if not mightpulse_client.configured():
            await send_followup(interaction, "❌ MightPulse isn't configured on this bot (MIGHTPULSE_API_KEY).")
            return
        targets = list(sheet_targets(bot_data))
        if not targets:
            await send_followup(interaction, "❌ No sheet targets configured. Add one with `/sheet add`.")
            return

        async def do_checkpoint():
            try:
                rosters: Dict[Any, Any] = {}
                await prefetch_rosters(mightpulse_client, targets, rosters)
                missing = [t["tag"] for t in targets
                           if not (rosters.get((str(t["kid"]), t["tag"])) or {}).get("alliance")]
                previous = (bot_data.get("power_checkpoint") or {}).get("at")
                count = take_checkpoint(bot_data, rosters, superseded_members(rosters))
                save_bot_data(bot_data)
                print(f"📍 Power checkpoint {bot_data['power_checkpoint']['at']}: {count} players")
                lines = [f"📍 **Checkpoint taken** — {bot_data['power_checkpoint']['at']}",
                         f"Recorded power for {count} player(s). `Growth %` now counts from here "
                         f"(shows on the next `/sheet sync` or daily sync)."]
                if previous:
                    lines.append(f"-# Replaces the previous checkpoint from {previous}.")
                if missing:
                    lines.append(f"⚠️ Couldn't load: {', '.join(f'`{m}`' for m in missing)} — "
                                 f"those members fall back to their first snapshot after today.")
                await send_followup(interaction, "\n".join(lines))
            except Exception as e:
                await send_followup(interaction, f"❌ Checkpoint failed: {str(e)}")
                print(f"Error in sheet checkpoint: {e}")

        await add_queue.enqueue(do_checkpoint())

    @group.command(name="add", description="Sync an alliance into a tab of the roster sheet")
    @app_commands.describe(
        kingdom="Kingdom number the alliance is in",
        tag="Alliance tag (case-sensitive, e.g. 1MK)",
        tab="Sheet tab name (defaults to the tag)",
    )
    async def sheet_add(interaction: discord.Interaction, kingdom: str, tag: str, tab: Optional[str] = None):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        tab = (tab or tag).strip()
        if tab.lower() in (t.lower() for t in RESERVED_TABS):
            await interaction.response.send_message(
                f"❌ `{tab}` is reserved for the bot's own tab — pick another tab name.", ephemeral=True
            )
            return
        target = {"kid": kingdom.strip(), "tag": tag.strip(), "tab": tab}
        existing = _find_target(bot_data, tab)
        if existing:
            existing.update(target)
            verb = "Updated"
        else:
            sheet_targets(bot_data).append(target)
            verb = "Added"
        save_bot_data(bot_data)
        await interaction.response.send_message(
            f"✅ {verb} {_describe(target)}.\n"
            f"-# If the tab doesn't exist or is blank, the next `/sheet sync` creates it with a header row "
            f"and adds every member. Preview with `/sheet sync tab:{tab} dry_run:True`."
        )

    @group.command(name="remove", description="Stop syncing a tab (the tab itself is left alone)")
    @app_commands.describe(tab="Sheet tab name")
    @app_commands.autocomplete(tab=tab_autocomplete)
    async def sheet_remove(interaction: discord.Interaction, tab: str):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        target = _find_target(bot_data, tab)
        if not target:
            await interaction.response.send_message(f"❌ No sheet target for tab `{tab}`.", ephemeral=True)
            return
        sheet_targets(bot_data).remove(target)
        save_bot_data(bot_data)
        await interaction.response.send_message(f"✅ Removed {_describe(target)}. The tab itself wasn't touched.")

    @group.command(name="list", description="List which alliances sync into which sheet tabs")
    async def sheet_list(interaction: discord.Interaction):
        permission_error = check_channel_only(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        targets = sheet_targets(bot_data)
        if not targets:
            await interaction.response.send_message("No sheet targets configured. Add one with `/sheet add`.")
            return
        if SHEET_AUTO_SYNC_HOURS > 0:
            last = bot_data.get("botConfig", {}).get("last_sheet_auto_sync")
            auto = (f"Auto-sync every {SHEET_AUTO_SYNC_HOURS}h · last run "
                    + (f"`{last[:16].replace('T', ' ')}`" if last else "not yet"))
        else:
            auto = "Auto-sync is off (SHEET_AUTO_SYNC_HOURS=0)"
        checkpoint = (bot_data.get("power_checkpoint") or {}).get("at")
        growth = (f"Growth measured from checkpoint {checkpoint}" if checkpoint
                  else "No checkpoint yet — growth counts from the first snapshot; run /sheet checkpoint after KvK")
        await interaction.response.send_message(
            "📋 **Sheet sync targets**\n" + "\n".join(f"• {_describe(t)}" for t in targets)
            + f"\n-# {auto}\n-# {growth}"
        )

    tree.add_command(group)
