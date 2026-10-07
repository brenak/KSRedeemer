"""Links to KSHive plans for the sheet sync's messages.

KSHive (the placement planner) builds plans from these same sheet tabs;
GET /api/tab-plans/?tab=... returns each tab's plans with their view-only
share path. Best effort: unconfigured, unreachable or refused just means
no links -- the sync never waits on it for long or fails because of it.
"""
from typing import Dict, Iterable, List, Tuple

import aiohttp

from config.config import KSHIVE_BOT_TOKEN, KSHIVE_URL

MAX_LINKS = 3   # per tab -- a tab rarely has more than one plan


async def plan_links(tabs: Iterable[str]) -> Dict[str, List[Tuple[str, str]]]:
    """{tab: [(plan name, share URL), ...]} for tabs that have a plan."""
    tabs = list(tabs)
    if not (KSHIVE_BOT_TOKEN and KSHIVE_URL and tabs):
        return {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{KSHIVE_URL}/api/tab-plans/", params=[("tab", t) for t in tabs],
                headers={"Authorization": f"Bearer {KSHIVE_BOT_TOKEN}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    print(f"⚠️ KSHive plan links: HTTP {resp.status}")
                    return {}
                data = await resp.json()
    except Exception as e:  # noqa: BLE001 -- links are a nicety
        print(f"⚠️ KSHive plan links unavailable: {str(e) or type(e).__name__}")
        return {}
    return {tab: [(p["name"], KSHIVE_URL + p["share_path"]) for p in plans[:MAX_LINKS]]
            for tab, plans in (data.get("tabs") or {}).items() if plans}


def links_text(links: List[Tuple[str, str]]) -> str:
    """ · 🐝 [1MK Hive](<url>) -- empty when the tab has no plan."""
    return "".join(f" · 🐝 [{name}](<{url}>)" for name, url in links)
