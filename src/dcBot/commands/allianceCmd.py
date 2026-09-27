import discord
from discord import app_commands
from datetime import datetime
from typing import Callable, Dict, Any, Optional

from dcBot.alliance_sync import onboard_new_members
from dcBot.permissions import check_permissions, check_channel_only
from services.mightpulse_client import MightPulseClient, MightPulseError, MightPulseRateLimited
from services.player_sync import (
    SyncReport,
    find_tracked_alliance,
    sync_alliance,
    sync_tracked_alliances,
    tracked_alliances,
)


def register_alliance_commands(
    tree: app_commands.CommandTree,
    bot_data: Dict[str, Any],
    save_bot_data: Callable[[Dict[str, Any]], None],
    add_queue,
    mightpulse_client: MightPulseClient,
):
    @tree.command(
        name="syncalliance",
        description="Add every member of an alliance and keep it synced daily (omit args to sync all tracked)",
    )
    @app_commands.describe(
        kingdom="Kingdom number the alliance is in",
        tag="Alliance tag (case-sensitive, e.g. 2mk)",
    )
    async def sync_alliance_cmd(
        interaction: discord.Interaction,
        kingdom: Optional[str] = None,
        tag: Optional[str] = None,
    ):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        if bool(kingdom) != bool(tag):
            await interaction.response.send_message(
                "❌ Give both `kingdom` and `tag`, or neither to sync every tracked alliance.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        if not mightpulse_client.configured():
            await interaction.followup.send("❌ MightPulse isn't configured on this bot (MIGHTPULSE_API_KEY).")
            return

        if not kingdom and not tracked_alliances(bot_data):
            await interaction.followup.send("❌ No tracked alliances yet — use `/syncalliance <kingdom> <tag>`.")
            return

        target = f"`{kingdom}/{tag}`" if kingdom else f"{len(tracked_alliances(bot_data))} tracked alliance(s)"
        position = add_queue.position()
        if position > 0:
            await interaction.followup.send(f"⏳ Sync of {target} queued — {position} request(s) ahead.")

        async def do_sync():
            try:
                if kingdom:
                    report = SyncReport()
                    try:
                        alliance = await sync_alliance(
                            bot_data, mightpulse_client, kingdom, tag, report, add_new_members=True
                        )
                    except MightPulseRateLimited:
                        await interaction.followup.send("🚦 MightPulse rate limit hit — try again in a minute.")
                        return
                    except MightPulseError as e:
                        await interaction.followup.send(f"❌ MightPulse error: {e}")
                        return

                    if not alliance:
                        await interaction.followup.send(
                            f"❌ No alliance `{tag}` found in kingdom `{kingdom}` "
                            f"(tags are case-sensitive)."
                        )
                        return

                    entry = find_tracked_alliance(bot_data, kingdom, tag)
                    newly_tracked = entry is None
                    if newly_tracked:
                        entry = {"kid": str(alliance.get("kid") or kingdom), "tag": alliance.get("abbr") or tag}
                        tracked_alliances(bot_data).append(entry)
                    entry["name"] = alliance.get("name", "")
                    entry["member_count"] = alliance.get("count")
                    entry["last_synced"] = datetime.now().astimezone().isoformat(timespec="seconds")
                    header = (
                        f"🏰 **[{entry['tag']}] {entry['name']}** (kingdom `{entry['kid']}`)"
                        + (" — now tracked, synced daily" if newly_tracked else " — synced")
                    )
                else:
                    report = await sync_tracked_alliances(bot_data, mightpulse_client, add_new_members=True)
                    header = f"🏰 **Synced {target}**"

                save_bot_data(bot_data)

                lines = [header]
                lines.extend(report.summary_lines())
                if report.added:
                    lines.append("New: " + ", ".join(f"`{p['player_nick']}`" for p in report.added[:20])
                                 + (f" (+{len(report.added) - 20} more)" if len(report.added) > 20 else ""))
                    await _send(interaction, lines)
                    lines = await onboard_new_members(bot_data, report.added, save_bot_data)
                    if lines:
                        await _send(interaction, lines)
                else:
                    lines.append("No new members to add.")
                    await _send(interaction, lines)

            except Exception as e:
                await interaction.followup.send(f"❌ Error syncing alliance: {str(e)}")
                print(f"Error in syncalliance: {e}")

        await add_queue.enqueue(do_sync())

    @tree.command(name="untrackalliance", description="Stop syncing an alliance (its members stay registered)")
    @app_commands.describe(kingdom="Kingdom number", tag="Alliance tag")
    async def untrack_alliance(interaction: discord.Interaction, kingdom: str, tag: str):
        permission_error = check_permissions(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        entry = find_tracked_alliance(bot_data, kingdom, tag)
        if not entry:
            await interaction.response.send_message(
                f"❌ `{kingdom}/{tag}` isn't tracked. Use `/alliances` to see tracked alliances.",
                ephemeral=True,
            )
            return

        tracked_alliances(bot_data).remove(entry)
        save_bot_data(bot_data)
        await interaction.response.send_message(
            f"✅ Stopped tracking `[{entry['tag']}]` (kingdom `{entry['kid']}`). Its members stay registered."
        )

    @tree.command(name="alliances", description="List tracked alliances")
    async def list_alliances(interaction: discord.Interaction):
        permission_error = check_channel_only(interaction, bot_data)
        if permission_error:
            await interaction.response.send_message(permission_error, ephemeral=True)
            return

        alliances = tracked_alliances(bot_data)
        if not alliances:
            await interaction.response.send_message(
                "No tracked alliances. Add one with `/syncalliance <kingdom> <tag>`."
            )
            return

        players = bot_data.get("players", [])
        embed = discord.Embed(title="🏰 Tracked Alliances", color=discord.Color.gold())
        for a in alliances:
            registered = sum(
                1 for p in players
                if p.get("kingdom") == a["kid"] and p.get("alliance_tag") == a["tag"]
            )
            last = a.get("last_synced", "never")
            embed.add_field(
                name=f"[{a['tag']}] {a.get('name', '')}",
                value=(
                    f"Kingdom `{a['kid']}` · {registered} registered"
                    + (f" / {a['member_count']} members" if a.get("member_count") is not None else "")
                    + f"\nLast synced: `{last[:16].replace('T', ' ')}`"
                ),
                inline=False,
            )
        embed.set_footer(text="Synced automatically once a day and whenever a new gift code appears")
        await interaction.response.send_message(embed=embed)


async def _send(interaction: discord.Interaction, lines):
    message = "\n".join(lines)
    if len(message) > 1900:
        message = message[:1900] + "\n…(truncated)"
    await interaction.followup.send(message)
