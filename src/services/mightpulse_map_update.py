"""Press MightPulse's public "Update Map" button for a kingdom before a
sheet sync, so the sync reads fresh positions -- only when MightPulse says
an update is available.

These are the same two calls mightpulse.com/map makes (static/map.js):

  GET  /api/map/update/status?kid=<kid>&token=<token>
       -> updates_paused, update_available, cooldown_remaining_sec (the
          kingdom's 30-minute cooldown), ip_cooldown_remaining_sec (one
          update per connection per 30 minutes), job {job_id, status:
          queued / running / done}, request_token
  POST /api/map/update?kid=<kid>&token=<token>
       -> starts (queues) an update, or error "cooldown" / "ip_cooldown"

The token is just a random id the page invents and keeps (the server may
hand back a replacement); it isn't a login. The cooldowns are honored: the
status is checked first and nothing is requested while either one is
running, an update is already queued/running, or updates are paused -- the
sync simply goes on with the data MightPulse already has.
"""

import asyncio
import secrets
import time
from typing import Any, Dict, List, Optional

import aiohttp

SITE = "https://mightpulse.com"
HTTP_TIMEOUT_SECONDS = 30
# How long a sync waits for a requested update to finish before going on.
MAX_WAIT_SECONDS = 600
POLL_SECONDS = 15
USER_AGENT = "KSRedeemer alliance bot (checks and honors the Update Map cooldown)"

_token: Optional[str] = None


def _get_token() -> str:
    global _token
    if _token is None:
        _token = "t_" + secrets.token_hex(8)
    return _token


def _fmt(sec: float) -> str:
    sec = max(0, int(sec))
    m, s = divmod(sec, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def skip_reason(st: Dict[str, Any]) -> Optional[str]:
    """Why an update shouldn't be requested now, or None if it may be.
    Pure -- tested offline."""
    if st.get("updates_paused"):
        return "updates are paused on MightPulse"
    job = st.get("job") or {}
    if job.get("status") in ("queued", "running"):
        return f"an update is already {job['status']}"
    left = max(float(st.get("cooldown_remaining_sec") or 0), float(st.get("ip_cooldown_remaining_sec") or 0))
    if left > 0:
        return f"on cooldown ({_fmt(left)} left)"
    if st.get("update_available") is False:
        return "MightPulse says no update is available right now"
    return None


async def _status(session: aiohttp.ClientSession, kid: str) -> Dict[str, Any]:
    async with session.get(f"{SITE}/api/map/update/status", params={"kid": kid, "token": _get_token()}) as r:
        r.raise_for_status()
        st = await r.json(content_type=None)
    _remember_token(st)
    return st


def _remember_token(data: Dict[str, Any]) -> None:
    global _token
    if isinstance(data, dict) and data.get("request_token"):
        _token = data["request_token"]


async def update_map(kid: str, max_wait: float = MAX_WAIT_SECONDS) -> str:
    """Request a map update for `kid` if one is available, wait (up to
    max_wait) for it to finish, and return a one-line status for the sync
    report. Never raises -- any problem just means the sync goes on."""
    label = f"🗺️ Kingdom {kid}"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS), headers={"User-Agent": USER_AGENT},
        ) as session:
            before = await _status(session, kid)
            finished_before = float(before.get("last_finished_at") or 0)
            reason = skip_reason(before)
            if reason:
                return f"{label}: map update skipped — {reason}"

            async with session.post(f"{SITE}/api/map/update", params={"kid": kid, "token": _get_token()}) as r:
                data = await r.json(content_type=None) if r.content_type else {}
            _remember_token(data)
            if isinstance(data, dict) and data.get("error"):
                # e.g. the cooldown started between the check and the press
                return f"{label}: map update skipped — {data.get('message') or data['error']}"

            started = time.monotonic()
            while time.monotonic() - started < max_wait:
                await asyncio.sleep(POLL_SECONDS)
                st = await _status(session, kid)
                job = st.get("job") or {}
                status = job.get("status")
                if status in ("queued", "running"):
                    continue
                # Done: the job says so, or it's gone and the kingdom's last
                # update finished after we pressed (not just "not listed yet").
                if status == "done" or (not job and float(st.get("last_finished_at") or 0) > finished_before):
                    return f"{label}: map updated (took {_fmt(time.monotonic() - started)})"
                if status:
                    return f"{label}: map update ended with status '{status}' — syncing with what MightPulse has"
            return (f"{label}: map update requested, still running after {_fmt(max_wait)} — "
                    f"syncing now; the next sync will pick up the new positions")
    except Exception as e:  # noqa: BLE001 -- a map update must never stop a sync
        print(f"⚠️ MightPulse map update for {kid} failed: {type(e).__name__}: {e}")
        return f"{label}: map update skipped — couldn't reach MightPulse ({str(e) or type(e).__name__})"


async def update_maps(kids: List[str]) -> List[str]:
    """update_map for each distinct kingdom, in order."""
    lines = []
    for kid in dict.fromkeys(str(k) for k in kids if k):
        line = await update_map(kid)
        print(line)
        lines.append(line)
    return lines
