from typing import Any, Dict, List, Optional

from services.kingshot_client import KingshotClient, KingshotAPIError

# kingshot_web's own default/primary kingdom (map/views/_shared.py DEFAULT_KID).
# Nickname lookup only works here: kingshot_web only tracks City/nick data for
# kingdoms it actively scans, and this bot's roster is 1259-first.
LOOKUP_KINGDOM = "1259"


async def lookup_player_nick(fid: str, kingdom: str, client: KingshotClient) -> Optional[str]:
    """Look up a player's current in-game nickname by FID via kingshot_web.
    Returns None if the feature isn't configured, the player isn't kingdom
    1259, the FID hasn't been Intel-confirmed in kingshot_web yet, or the
    request fails for any reason -- this is a best-effort nicety, never
    something /add or /refreshname should fail over.
    """
    if kingdom != LOOKUP_KINGDOM or not client.configured():
        return None

    try:
        fid_int = int(fid)
    except (TypeError, ValueError):
        return None

    try:
        data = await client.search_cities(fid, kid=LOOKUP_KINGDOM)
    except KingshotAPIError as e:
        print(f"kingshot_web lookup failed for fid {fid}: {e}")
        return None

    # search also substring-matches nick/abbr, so filter explicitly for the
    # FID (displayed_player_id) rather than trusting the first hit.
    matches = [
        c for c in data.get("cities", [])
        if c.get("displayed_player_id") == fid_int
    ]
    if len(matches) == 1:
        return matches[0].get("nick") or None
    return None


async def refresh_player_nicks(
    players: List[Dict[str, Any]], client: KingshotClient
) -> List[Dict[str, Any]]:
    """Best-effort kingdom-1259 nickname refresh for a batch of players just
    involved in a redemption. Mutates matching player dicts in place (same
    objects the caller already holds a reference to in bot_data["players"]).
    Never raises -- a lookup failure for one player just leaves their nick
    unchanged and moves on to the next; redemption itself must never be
    blocked by this. Returns the list of player dicts that were renamed.
    """
    updated: List[Dict[str, Any]] = []
    for player in players:
        if player.get("kingdom", LOOKUP_KINGDOM) != LOOKUP_KINGDOM:
            continue
        player_id = player.get("player_id")
        if not player_id:
            continue
        try:
            looked_up = await lookup_player_nick(player_id, LOOKUP_KINGDOM, client)
        except Exception as e:
            print(f"kingshot_web nick refresh failed for {player_id}: {e}")
            continue
        if looked_up and player.get("player_nick") != looked_up:
            player["player_nick"] = looked_up
            updated.append(player)
    return updated
