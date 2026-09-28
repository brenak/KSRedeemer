"""/sheet -- alliance roster Google Sheet sync (services/sheet_sync.py).

One tab per alliance, all in the same spreadsheet (GOOGLE_SHEET_ID). The
alliance -> tab list lives in botData.json["sheet_targets"], seeded from
SHEET_DEFAULT_KINGDOM / _TAG / _TAB the first time it's needed, and managed
with /sheet add / remove / list.
"""

import discord
from discord import app_commands
from typing import Any, Callable, Dict, List, Optional

from config.config import SHEET_DEFAULT_KINGDOM, SHEET_DEFAULT_TAB, SHEET_DEFAULT_TAG
from dcBot.interaction_reply import send_followup
from dcBot.permissions import check_channel_only, check_permissions
from services.mightpulse_client import MightPulseClient
from services.sheet_sync import SheetSyncError, configured, sync_alliance_sheet

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
            player_cache: Dict[int, Any] = {}  # shared so no one is looked up twice
            for t in targets:
                try:
                    result = await sync_alliance_sheet(
                        mightpulse_client, t["kid"], t["tag"], t["tab"],
                        dry_run=dry_run, player_cache=player_cache,
                    )
                    header = (
                        f"📋 **[dry run — nothing written] {_describe(t)}**"
                        if dry_run else f"📋 **Synced {_describe(t)}**"
                    )
                    message = "\n".join([header] + result.summary_lines())
                except SheetSyncError as e:
                    message = f"❌ {_describe(t)}: {e}"
                except Exception as e:
                    message = f"❌ {_describe(t)}: {str(e)}"
                    print(f"Error in sheet sync ({t['tab']}): {e}")
                if len(message) > 1900:
                    message = message[:1900] + "\n…(truncated)"
                await send_followup(interaction, message)

        await add_queue.enqueue(do_sync())

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
        await interaction.response.send_message(
            "📋 **Sheet sync targets**\n" + "\n".join(f"• {_describe(t)}" for t in targets)
        )

    tree.add_command(group)
