import asyncio
import hashlib
import json
import random
import time
from typing import Any, Dict, List

import aiohttp

DEFAULT_KINGDOM = "1259"

API_BASE = "https://kingshot-giftcode.centurygame.com/api"

# Reverse-engineered from the gift-code page's client bundle
# (src_pages_home_index_vue.*.js -> ./src/utils/request.js's appendSign):
# sorted "key=value&..." query string of the request params (fid, cdk, kid,
# time), secret appended, MD5 hex digest. Verified against real captured
# requests (two live sign values reproduced exactly).
SIGN_SECRET = "mN4!pQs6JrYwV9"

HTTP_TIMEOUT_SECONDS = 15

SUCCESS_ERR_CODE = 20000

# err_code -> English message, reverse-engineered from the same bundle's
# errorMsgMap (home.js) + locale table (app.js). Cross-checked against the
# known modal text from the old DOM-scraping implementation (most of these
# are verbatim matches) and one live observation (40020, "Character info is
# incorrect..."). Codes not listed here fall back to the API's raw `msg`.
ERROR_MESSAGES: Dict[int, str] = {
    20000: "Redeemed successfully. Please check your mail for rewards!",
    40001: "Player ID not found!",
    40002: "Request too frequent, please try again later.",
    40003: "Please enter a Gift Code first.",
    40004: "Server busy. Please try again later.",
    40005: "Claim limit exceeded. Unable to claim.",
    40006: "Insufficient Town Center Level. Unable to claim.",
    40007: "Expired, unable to claim.",
    40008: "Gift has already been claimed!",
    40009: "Please sign in with the corresponding character before redeeming.",
    40011: "The same Gift Code type can only be redeemed once!",
    40012: "Your account registration duration does not meet the requirements.",
    40014: "Gift Code not found! Please check the capitalization.",
    40015: "Redemption Code Error",
    40016: "The server is busy. Rewards will be sent later. Please be patient.",
    40017: "Your account does not currently meet the redemption requirements.",
    40019: "You are performing too many actions at once. Try again later.",
    40020: "Character info is incorrect. Please confirm and try again.",
}

# Player already has this code -- treat as a successful outcome, matching
# the old modal-text implementation's handling of "already claimed" phrasings.
ALREADY_REDEEMED_ERR_CODES = {40005, 40008, 40011}

# Properties of the code itself, not the player -- abort the whole batch
# rather than repeating the same failure for every remaining player.
INVALID_CODE_ERR_CODE = 40014
EXPIRED_CODE_ERR_CODE = 40007


def _sign(params: Dict[str, str]) -> str:
    query = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hashlib.md5((query + SIGN_SECRET).encode("utf-8")).hexdigest()


async def perform_giftcode_redeem(
    player_id: str, kingdom: str, gift_code: str, session: aiohttp.ClientSession
) -> Dict[str, Any]:
    print(f"Trying to redeem [{gift_code}] for player: {player_id} (kingdom {kingdom})")

    params = {
        "fid": player_id,
        "cdk": gift_code,
        "kid": kingdom,
        "time": str(int(time.time())),
    }
    body = {"sign": _sign(params), **params}

    try:
        async with session.post(
            f"{API_BASE}/gift_code",
            data=body,
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
        ) as resp:
            status = resp.status
            text = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as e:
        return {"success": False, "message": f"Request failed: {e}", "err_code": None}

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        preview = text[:200] if text else "<empty body>"
        print(f"Non-JSON response (HTTP {status}) for [{gift_code}] {player_id}: {preview!r}")
        return {
            "success": False,
            "message": f"Unexpected response from server (HTTP {status}). Try again shortly.",
            "err_code": None,
        }

    err_code = payload.get("err_code")
    message = ERROR_MESSAGES.get(err_code) or payload.get("msg") or "Unknown response."
    print("Redemption result:", message, f"(err_code={err_code})")

    return {
        "success": err_code == SUCCESS_ERR_CODE,
        "message": message,
        "err_code": err_code,
    }


async def redeem_giftcode_for_all_players(players: List[Dict[str, str]], gift_code: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    async with aiohttp.ClientSession() as session:
        for i, player in enumerate(players):
            if i > 0:
                await asyncio.sleep(random.uniform(1, 2))  # polite pacing between requests

            player_id = player.get("player_id", "")
            kingdom = player.get("kingdom") or DEFAULT_KINGDOM
            stored_nick = player.get("player_nick")

            result = await perform_giftcode_redeem(player_id, kingdom, gift_code, session)
            err_code = result.get("err_code")

            if err_code == INVALID_CODE_ERR_CODE:
                results.append({
                    "success": False,
                    "errorCode": "INVALID_CODE",
                    "message": "Invalid gift code.",
                })
                return results

            elif err_code == EXPIRED_CODE_ERR_CODE:
                results.append({
                    "success": False,
                    "errorCode": "EXPIRED",
                    "message": "Gift code has expired.",
                })
                return results

            elif err_code in ALREADY_REDEEMED_ERR_CODES:
                results.append({
                    "player_id": player_id,
                    "stored_player_nick": stored_nick,
                    "result": result,
                    "success": True,
                })
            else:
                results.append({
                    "player_id": player_id,
                    "stored_player_nick": stored_nick,
                    "result": result,
                    "success": result.get("success", False),
                })

        return results
