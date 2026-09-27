"""Keeps registered players' nickname / kingdom / alliance in sync with
MightPulse, and pulls tracked alliances' rosters into the player list.

Quota strategy (60 req/min, 5,000/day): a tracked alliance's roster is one
request and covers every member, so a full refresh syncs rosters first and
only looks up players individually if no tracked roster covered them. None
of this runs on the routine gift-code check -- only when a new code appears,
on the daily alliance sync, or when an admin asks via /refreshname,
/syncalliance.

Player record fields maintained here (alongside player_id / player_nick /
kingdom):
  alliance_tag / alliance_name -- current alliance ("" = no alliance)
  info_updated_at              -- last time MightPulse data was applied
  nick_locked                  -- set by /setname or /add <name>; refreshes
                                  then keep the custom nick but still update
                                  kingdom and alliance
Kingdom matters beyond display: the gift-code API needs the player's
current kid, so keeping it fresh keeps redemptions working after a transfer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from services.mightpulse_client import MightPulseClient, MightPulseError, MightPulseRateLimited


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def info_from_player(player: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a /v1/players/{id} `player` object."""
    alliance = player.get("alliance") or {}
    return {
        "nick": player.get("nick_name"),
        "kid": player.get("kid"),
        "alliance_tag": alliance.get("abbr") or "",
        "alliance_name": alliance.get("name") or "",
    }


def info_from_roster_member(member: Dict[str, Any], alliance: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one entry of a /v1/alliances/{kid}/{tag} `members` list."""
    return {
        "nick": member.get("nick_name"),
        "kid": member.get("kid") or alliance.get("kid"),
        "alliance_tag": alliance.get("abbr") or "",
        "alliance_name": alliance.get("name") or "",
    }


def apply_player_info(player: Dict[str, Any], info: Dict[str, Any]) -> List[str]:
    """Write MightPulse info onto a stored player dict. Returns a
    human-readable description of each visible change (empty if nothing
    changed)."""
    changes: List[str] = []

    nick = info.get("nick")
    if nick and not player.get("nick_locked") and player.get("player_nick") != nick:
        changes.append(f"name `{player.get('player_nick', 'N/A')}` → `{nick}`")
        player["player_nick"] = nick

    if info.get("kid") is not None:
        kid = str(info["kid"])
        if player.get("kingdom") != kid:
            changes.append(f"kingdom `{player.get('kingdom', 'N/A')}` → `{kid}`")
            player["kingdom"] = kid

    tag = info.get("alliance_tag") or ""
    if "alliance_tag" not in player or player.get("alliance_tag") != tag:
        if "alliance_tag" in player:
            changes.append(f"alliance `{player.get('alliance_tag') or 'none'}` → `{tag or 'none'}`")
        player["alliance_tag"] = tag
    player["alliance_name"] = info.get("alliance_name") or ""

    player["info_updated_at"] = _now()
    return changes


async def lookup_player(fid: str, client: MightPulseClient) -> Optional[Dict[str, Any]]:
    """Normalized info for one FID, or None if unknown / not configured.
    Raises MightPulseError (incl. MightPulseRateLimited) on API failure."""
    if not client.configured():
        return None
    try:
        int(fid)
    except (TypeError, ValueError):
        return None
    player = await client.get_player(fid)
    return info_from_player(player) if player else None


def tracked_alliances(bot_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    return bot_data.setdefault("tracked_alliances", [])


def find_tracked_alliance(bot_data: Dict[str, Any], kid: str, tag: str) -> Optional[Dict[str, Any]]:
    # Tags are case-sensitive in MightPulse, but two alliances in one kingdom
    # differing only by case is vanishingly unlikely -- match loosely so
    # /untrackalliance doesn't need exact casing.
    return next(
        (
            a for a in tracked_alliances(bot_data)
            if a.get("kid") == str(kid) and a.get("tag", "").lower() == tag.lower()
        ),
        None,
    )


@dataclass
class SyncReport:
    added: List[Dict[str, Any]] = field(default_factory=list)
    updated: List[Dict[str, Any]] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)
    missing_alliances: List[str] = field(default_factory=list)
    alliances_synced: int = 0
    players_looked_up: int = 0
    rate_limited: bool = False
    errors: List[str] = field(default_factory=list)

    def changed(self) -> bool:
        return bool(self.added or self.updated or self.alliances_synced)

    def summary_lines(self) -> List[str]:
        lines: List[str] = []
        if self.alliances_synced:
            lines.append(f"🏰 Synced {self.alliances_synced} alliance roster(s)")
        if self.added:
            lines.append(f"➕ Added {len(self.added)} new alliance member(s)")
        if self.players_looked_up:
            lines.append(f"🔍 Looked up {self.players_looked_up} player(s) individually")
        if self.updated:
            lines.append(f"💾 Updated {len(self.updated)} player(s)")
        if self.not_found:
            lines.append(f"⚠️ Not found on MightPulse: {', '.join(f'`{p}`' for p in self.not_found[:10])}"
                         + (f" (+{len(self.not_found) - 10} more)" if len(self.not_found) > 10 else ""))
        if self.missing_alliances:
            lines.append(f"⚠️ Alliance(s) not found (renamed/disbanded?): "
                         f"{', '.join(f'`{a}`' for a in self.missing_alliances)}")
        if self.rate_limited:
            lines.append("🚦 MightPulse rate limit hit — stopped early, the rest will catch up next time")
        for err in self.errors[:3]:
            lines.append(f"❌ {err}")
        return lines


def _new_player_from_info(fid: str, info: Dict[str, Any], source: str) -> Dict[str, Any]:
    player = {"player_id": fid, "player_nick": info.get("nick") or f"Player {fid}", "added_via": source}
    apply_player_info(player, info)
    return player


async def sync_alliance(
    bot_data: Dict[str, Any],
    client: MightPulseClient,
    kid: str,
    tag: str,
    report: SyncReport,
    add_new_members: bool,
    covered: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Pull one alliance roster: update registered members, optionally
    register new ones. Returns the alliance info dict, or None if the
    alliance wasn't found. Raises MightPulseError on API failure."""
    data = await client.get_alliance_roster(kid, tag)
    if not data or not data.get("alliance"):
        report.missing_alliances.append(f"{kid}/{tag}")
        return None

    alliance = data["alliance"]
    players = bot_data.setdefault("players", [])
    by_id = {p.get("player_id"): p for p in players}

    for member in data.get("members") or []:
        fid = member.get("fid") or member.get("governor_id")
        if not fid:
            continue
        fid = str(fid)
        info = info_from_roster_member(member, alliance)
        if covered is not None:
            covered.add(fid)

        existing = by_id.get(fid)
        if existing:
            if apply_player_info(existing, info):
                report.updated.append(existing)
        elif add_new_members:
            new_player = _new_player_from_info(fid, info, f"alliance:{alliance.get('kid', kid)}/{alliance.get('abbr', tag)}")
            players.append(new_player)
            by_id[fid] = new_player
            report.added.append(new_player)

    report.alliances_synced += 1
    return alliance


async def sync_tracked_alliances(
    bot_data: Dict[str, Any],
    client: MightPulseClient,
    add_new_members: bool,
    report: Optional[SyncReport] = None,
    covered: Optional[Set[str]] = None,
) -> SyncReport:
    """Sync every tracked alliance's roster. Never raises -- failures land on
    the report."""
    report = report or SyncReport()
    if not client.configured():
        return report

    for entry in list(tracked_alliances(bot_data)):
        try:
            alliance = await sync_alliance(
                bot_data, client, entry["kid"], entry["tag"], report, add_new_members, covered
            )
        except MightPulseRateLimited:
            report.rate_limited = True
            break
        except MightPulseError as e:
            report.errors.append(f"{entry['kid']}/{entry['tag']}: {e}")
            continue

        if alliance:
            entry["name"] = alliance.get("name") or entry.get("name", "")
            entry["member_count"] = alliance.get("count")
            entry["last_synced"] = _now()
    return report


async def refresh_all_players(
    bot_data: Dict[str, Any], client: MightPulseClient, add_new_members: bool
) -> SyncReport:
    """Roster-first refresh of every registered player: tracked alliance
    rosters (one request each), then individual lookups for whoever no
    roster covered (e.g. players outside tracked alliances, or members who
    left one -- their lookup picks up their new alliance). Never raises."""
    covered: Set[str] = set()
    report = await sync_tracked_alliances(bot_data, client, add_new_members, covered=covered)
    if report.rate_limited or not client.configured():
        return report

    for player in list(bot_data.get("players", [])):
        fid = player.get("player_id")
        if not fid or fid in covered:
            continue
        try:
            info = await lookup_player(fid, client)
        except MightPulseRateLimited:
            report.rate_limited = True
            break
        except MightPulseError as e:
            report.errors.append(f"{fid}: {e}")
            continue
        report.players_looked_up += 1
        if not info:
            report.not_found.append(fid)
            continue
        if apply_player_info(player, info):
            report.updated.append(player)
    return report
