"""Redeem every currently-active cached code a set of players hasn't received
yet. Shared by /catchup and by alliance sync (newly-added members get all
active codes, same as /add)."""

import asyncio
import random
from datetime import datetime
from typing import Any, Callable, Dict, List, Tuple

from giftcode_api.redeem import redeem_giftcode_for_all_players

# (code, succeeded, failed_msgs)
CodeResult = Tuple[str, int, List[str]]


def active_codes(bot_data: Dict[str, Any]) -> List[str]:
    return [
        code for code, data in bot_data.get("gift_code_cache", {}).items()
        if data.get("status") == "valid"
    ]


def build_pending(bot_data: Dict[str, Any], players: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Per active code, the players who still need it."""
    redeemed_codes = bot_data.get("redeemed_codes", {})
    pending: Dict[str, List[Dict[str, Any]]] = {}
    for code in active_codes(bot_data):
        already = set(redeemed_codes.get(code, []))
        missing = [p for p in players if p.get("player_id") not in already]
        if missing:
            pending[code] = missing
    return pending


async def redeem_pending(
    bot_data: Dict[str, Any],
    pending: Dict[str, List[Dict[str, Any]]],
    save_bot_data: Callable[[Dict[str, Any]], None],
) -> Tuple[List[CodeResult], List[str]]:
    """Redeem each pending code for its players. Returns (per-code results,
    codes found to be expired). Saves after every code."""
    redeemed = bot_data.setdefault("redeemed_codes", {})
    expired_codes: List[str] = []
    code_results: List[CodeResult] = []

    for code, players in pending.items():
        await asyncio.sleep(random.uniform(5, 10))
        result = await redeem_giftcode_for_all_players(players, code)

        succeeded = 0
        failed_msgs: List[str] = []
        code_list = redeemed.setdefault(code, [])

        for item in result or []:
            error_code = item.get("errorCode", "")

            if error_code == "EXPIRED":
                cache = bot_data.setdefault("gift_code_cache", {})
                cache[code] = {
                    **cache.get(code, {}),
                    "status": "invalid",
                    "manually_expired": True,
                    "last_checked": datetime.now().isoformat(),
                }
                expired_codes.append(code)
                break

            if error_code == "INVALID_CODE":
                failed_msgs.append("Invalid code")
                break

            if error_code == "RATE_LIMITED":
                failed_msgs.append(item.get("message", "Rate limited by the gift-code server."))
                break

            pid = item.get("player_id")
            if item.get("success"):
                succeeded += 1
                if pid and pid not in code_list:
                    code_list.append(pid)
            else:
                msg = item.get("result", {}).get("message") or item.get("message", "Unknown error")
                nick = item.get("stored_player_nick") or pid or "?"
                failed_msgs.append(f"`{nick}`: {msg}")

        if code not in expired_codes:
            code_results.append((code, succeeded, failed_msgs))
        save_bot_data(bot_data)

    return code_results, expired_codes


def format_code_results(
    code_results: List[CodeResult],
    expired_codes: List[str],
    pending: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    lines: List[str] = []
    for code, succeeded, failed_msgs in code_results:
        total = len(pending[code])
        if succeeded == total:
            lines.append(f"✅ `{code}` — {succeeded}/{total} redeemed")
        else:
            icon = "⚠️" if succeeded > 0 else "❌"
            lines.append(f"{icon} `{code}` — {succeeded}/{total} redeemed")
            for f in failed_msgs:
                lines.append(f"   • {f}")
    for code in expired_codes:
        lines.append(f"⏰ `{code}` — expired, removed from active list")
    return lines
