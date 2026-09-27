"""Async HTTP client for the MightPulse API (https://api.mightpulse.com/).

Used to look up a player (nickname, kingdom, alliance) by FID and to pull an
alliance's member roster. The API key is limited to 60 requests/minute and
5,000/day, so every request from the whole bot goes through one shared
throttle (one request per MIN_REQUEST_INTERVAL_SECONDS). A 429 is retried
once after backing off; if it's still 429 the call raises
MightPulseRateLimited so batch callers can stop instead of burning through
more of the quota.
"""

import asyncio
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp

from config.config import MIGHTPULSE_API_KEY

API_BASE = "https://api.mightpulse.com/v1"
HTTP_TIMEOUT_SECONDS = 15

# 60 requests/minute limit -- keep a little headroom.
MIN_REQUEST_INTERVAL_SECONDS = 1.1

# Honor Retry-After when sent, but never block the bot for longer than this;
# a long Retry-After most likely means the daily quota is gone.
DEFAULT_429_BACKOFF_SECONDS = 60
MAX_429_BACKOFF_SECONDS = 120


class MightPulseError(Exception):
    pass


class MightPulseRateLimited(MightPulseError):
    pass


def _retry_after_seconds(resp: aiohttp.ClientResponse) -> Optional[float]:
    value = resp.headers.get("Retry-After")
    if not value:
        return DEFAULT_429_BACKOFF_SECONDS
    try:
        seconds = float(value)
    except ValueError:
        return DEFAULT_429_BACKOFF_SECONDS
    if seconds > MAX_429_BACKOFF_SECONDS:
        return None
    return max(1.0, seconds)


class MightPulseClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    def configured(self) -> bool:
        return bool(MIGHTPULSE_API_KEY)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-Api-Key": MIGHTPULSE_API_KEY},
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
            )
        return self._session

    async def _get_json(self, path: str, **params: object) -> Optional[Dict[str, Any]]:
        """GET a MightPulse endpoint. Returns None on 404 (unknown player /
        alliance), raises MightPulseRateLimited if still 429 after one retry,
        MightPulseError for anything else that isn't a 2xx."""
        if not self.configured():
            raise MightPulseError("MIGHTPULSE_API_KEY not configured")

        session = await self._get_session()
        url = f"{API_BASE}{path}"
        query = {k: v for k, v in params.items() if v is not None}

        # Held across the backoff sleep on purpose: every caller shares the
        # same quota, so nobody else should fire while we're being limited.
        async with self._lock:
            for attempt in range(2):
                wait = self._last_request_at + MIN_REQUEST_INTERVAL_SECONDS - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request_at = time.monotonic()

                async with session.get(url, params=query) as resp:
                    if resp.status == 429:
                        backoff = _retry_after_seconds(resp)
                        if attempt == 0 and backoff is not None:
                            print(f"🚦 MightPulse rate limited on {path} — retrying in {backoff:.0f}s")
                            await asyncio.sleep(backoff)
                            continue
                        raise MightPulseRateLimited("MightPulse rate limit reached (HTTP 429)")

                    if resp.status == 404:
                        return None
                    if resp.status == 401:
                        raise MightPulseError("MightPulse rejected the API key (HTTP 401)")
                    if not resp.ok:
                        raise MightPulseError(f"MightPulse API error: HTTP {resp.status}")

                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        raise MightPulseError("MightPulse returned an unexpected response")
                    return data

        raise MightPulseRateLimited("MightPulse rate limit reached (HTTP 429)")

    async def get_player(self, fid: str) -> Optional[Dict[str, Any]]:
        """The `player` object for a governor ID / FID, or None if unknown."""
        data = await self._get_json(f"/players/{quote(str(fid), safe='')}")
        if not data:
            return None
        return data.get("player") or None

    async def get_alliance_roster(self, kid: str, tag: str) -> Optional[Dict[str, Any]]:
        """Full alliance response (`alliance` info + `members` list), or None
        if no alliance with that tag exists in that kingdom. Tag is
        case-sensitive."""
        return await self._get_json(
            f"/alliances/{quote(str(kid), safe='')}/{quote(tag, safe='')}",
            include="roster",
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
