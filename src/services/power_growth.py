"""Power growth tracking -- who's spending instead of saving for KvK.

MightPulse only exposes *current* power, so every real /sheet sync records
a daily snapshot of every tracked alliance member's power (from the live
rosters it already fetched -- no extra API calls) into a hidden
`Power History` tab. Growth is then measured against:

- the checkpoint -- `/sheet checkpoint`, run right after KvK, stores each
  player's power as their baseline (botData["power_checkpoint"]). Anyone
  without one (joined later, or before the first checkpoint) uses their
  first snapshot on/after the checkpoint date instead;
- a week ago -- the latest snapshot at least 7 days old.

Everyone grows a little, and small accounts grow faster in % terms, so a
fixed threshold would flag the wrong people. Each member's growth % is
instead compared with their alliance's median (`vs Alliance`, a multiple);
FLAG_MULTIPLE x median *and* at least FLAG_MIN_GAIN absolute power gained
flags them as likely spending.
"""

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.config import SHEET_TIMEZONE

HISTORY_TAB = "Power History"
HISTORY_HEADER = ["Date", "Kingdom", "Alliance", "Player ID", "Name", "Power"]
HISTORY_DAYS = 90          # snapshots older than this are pruned

WEEK = 7
FLAG_MULTIPLE = 2.0        # vs Alliance at or above this ...
FLAG_MIN_GAIN = 5_000_000  # ... and at least this much power gained -> flagged
MEDIAN_FLOOR = 0.01        # median growth treated as at least 1%, so a flat
                           # alliance doesn't turn every small gain into an outlier
FLAG_COLOR = "e06666"      # Google Sheets "light red 1"

# Trending inactive: power never went *up* over the last STALL_DAYS days --
# from the newest snapshot at least that old, through every snapshot since,
# to today. Drops are ignored: being attacked doesn't make anyone active or
# inactive. So flat (0%) is inactive, flat-then-attacked (0% -> -%) is still
# inactive, but grew-then-attacked (+% -> -%) is active. Needs a snapshot
# that old, so new players are never flagged.
STALL_DAYS = 3
STALL_COLOR = "9fc5e8"     # Google Sheets "light blue 2"
# ...but only where MightPulse actually refreshed: if under this share of a
# kingdom's tracked members changed power over the window, its data is
# stale (no map update in days -- every power looks flat) and nobody there
# is flagged. Live data changes for most members (~60%+ seen).
STALL_MIN_CHANGED = 0.25


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(SHEET_TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def now_local() -> datetime:
    return datetime.now(tz())


def today_str() -> str:
    return now_local().date().isoformat()


def _fid(member: Dict[str, Any]) -> int:
    return int(member.get("fid") or member.get("governor_id") or 0)


@dataclass
class PowerContext:
    today: str
    baselines: Dict[int, int] = field(default_factory=dict)   # fid -> baseline power
    week_ago: Dict[int, int] = field(default_factory=dict)    # fid -> power >= 7 days ago
    stall_ago: Dict[int, int] = field(default_factory=dict)   # fid -> power >= STALL_DAYS days ago
    stall_recent: Dict[int, List[int]] = field(default_factory=dict)  # fid -> powers snapshotted since, in order
    stall_stale: set = field(default_factory=set)             # fids in kingdoms whose data didn't refresh
    checkpoint_label: Optional[str] = None                     # when the checkpoint was taken
    first_snapshot: Optional[str] = None                       # earliest date on record
    recorded: Optional[int] = None                             # rows added this run (None = read-only)
    history_days: int = 0                                      # distinct snapshot dates on record


@dataclass
class Growth:
    power: int
    baseline: Optional[int]
    growth_pct: Optional[float]       # fraction, 0.123 = 12.3%
    gain: Optional[int]
    vs_alliance: Optional[float]      # multiple of the alliance median
    week_pct: Optional[float]
    flagged: bool
    stalled: bool = False             # power never went up over STALL_DAYS days


def build_context(history: Iterable[List[str]], checkpoint: Optional[Dict[str, Any]], today: str) -> PowerContext:
    """Baselines and week-ago powers from history rows ([Date, Kingdom,
    Alliance, Player ID, Name, Power], oldest first) and the checkpoint."""
    ctx = PowerContext(today=today)
    cp_date = (checkpoint or {}).get("date")
    cp_power = {int(k): int(v) for k, v in ((checkpoint or {}).get("power") or {}).items()}
    if checkpoint:
        ctx.checkpoint_label = checkpoint.get("at") or cp_date
    week_cutoff = (date.fromisoformat(today) - timedelta(days=WEEK)).isoformat()
    stall_cutoff = (date.fromisoformat(today) - timedelta(days=STALL_DAYS)).isoformat()

    first_after_cp: Dict[int, int] = {}
    for row in history:
        try:
            day, fid, power = row[0], int(row[3]), int(str(row[5]).replace(",", ""))
        except (IndexError, ValueError):
            continue
        ctx.first_snapshot = ctx.first_snapshot or day
        if (not cp_date or day >= cp_date) and fid not in first_after_cp:
            first_after_cp[fid] = power
        if day <= week_cutoff:
            ctx.week_ago[fid] = power          # rows are oldest-first: keeps the latest <= cutoff
        if day <= stall_cutoff:
            ctx.stall_ago[fid] = power
            ctx.stall_recent.pop(fid, None)    # a newer base: only snapshots after it count
        else:
            ctx.stall_recent.setdefault(fid, []).append(power)
    ctx.baselines = {**first_after_cp, **cp_power}
    return ctx


def never_rose(powers: List[int]) -> bool:
    """True if no step in the sequence goes up (drops and flat are fine)."""
    return all(b <= a for a, b in zip(powers, powers[1:]))


def alliance_growth(members: List[Dict[str, Any]], ctx: PowerContext) -> Tuple[Dict[int, Growth], Optional[float]]:
    """Growth per member of one alliance, plus the alliance's median growth
    (fraction). Members with no baseline yet get None values, not zeros."""
    rows: Dict[int, Growth] = {}
    for m in members:
        fid, power = _fid(m), m.get("power")
        if not fid or power is None:
            continue
        power = int(power)
        base = ctx.baselines.get(fid)
        week = ctx.week_ago.get(fid)
        then = ctx.stall_ago.get(fid)
        pct = (power - base) / base if base else None
        rows[fid] = Growth(
            power=power, baseline=base, growth_pct=pct,
            gain=(power - base) if base else None, vs_alliance=None,
            week_pct=((power - week) / week) if week else None, flagged=False,
            stalled=bool(then) and never_rose([then, *ctx.stall_recent.get(fid, []), power])
                    and fid not in ctx.stall_stale,
        )
    pcts = [g.growth_pct for g in rows.values() if g.growth_pct is not None]
    median = statistics.median(pcts) if pcts else None
    if median is not None:
        floor = max(median, MEDIAN_FLOOR)
        for g in rows.values():
            if g.growth_pct is None:
                continue
            g.vs_alliance = g.growth_pct / floor
            g.flagged = g.vs_alliance >= FLAG_MULTIPLE and (g.gain or 0) >= FLAG_MIN_GAIN
    return rows, median


def snapshot_rows(
    rosters: Dict[Tuple[str, str], Optional[Dict[str, Any]]],
    superseded: Dict[Tuple[str, str], Dict[int, Any]],
    today: str,
    already: set,
) -> List[List[Any]]:
    """Today's history rows for every tracked member not yet recorded today
    (`already`: fids with a row for today). A player a fresher roster claims
    is recorded under that alliance only."""
    out: List[List[Any]] = []
    seen = set(already)
    for key, data in rosters.items():
        if not data or not data.get("alliance"):
            continue
        abbr = data["alliance"].get("abbr") or key[1]
        skip = superseded.get(key, {})
        for m in data.get("members") or []:
            fid = _fid(m)
            if not fid or fid in seen or fid in skip or m.get("power") is None:
                continue
            seen.add(fid)
            out.append([today, str(m.get("kid") or key[0]), abbr, str(fid), m.get("nick_name") or "", int(m["power"])])
    return out


# --- sheet IO (blocking; call via asyncio.to_thread) --------------------------

def _history_ws(sh, create: bool):
    import gspread

    try:
        return sh.worksheet(HISTORY_TAB)
    except gspread.exceptions.WorksheetNotFound:
        if not create:
            return None
        ws = sh.add_worksheet(title=HISTORY_TAB, rows=1000, cols=len(HISTORY_HEADER))
        ws.update(values=[HISTORY_HEADER], range_name="A1", raw=True)
        sh.batch_update({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "hidden": True, "gridProperties": {"frozenRowCount": 1}},
            "fields": "hidden,gridProperties.frozenRowCount"}}]})
        return ws


def load_history(sh) -> List[List[str]]:
    ws = _history_ws(sh, create=False)
    if ws is None:
        return []
    values = ws.get_all_values()
    return [r for r in values[1:] if r and r[0].strip()]


def record_snapshot(sh, rows: List[List[Any]], history: List[List[str]], today: str) -> None:
    """Append today's rows and prune snapshots older than HISTORY_DAYS
    (they're at the top -- rows are appended oldest-first)."""
    ws = _history_ws(sh, create=True)
    if rows:
        # RAW: the ISO date stays text (USER_ENTERED would turn it into a
        # locale date like 9/28/2026 and break the comparisons above);
        # Power is sent as a JSON number, so it's still stored as a number.
        ws.append_rows(rows, value_input_option="RAW")
    cutoff = (date.fromisoformat(today) - timedelta(days=HISTORY_DAYS)).isoformat()
    old = 0
    for r in history:
        if r[0] < cutoff:
            old += 1
        else:
            break
    if old:
        sh.batch_update({"requests": [{"deleteDimension": {"range": {
            "sheetId": ws.id, "dimension": "ROWS", "startIndex": 1, "endIndex": 1 + old}}}]})


def mark_stale_kingdoms(ctx: PowerContext, rosters) -> None:
    """Fill ctx.stall_stale: every tracked member of a kingdom where under
    STALL_MIN_CHANGED of members changed power since their STALL_DAYS-old
    snapshot -- MightPulse didn't refresh it, so "no growth" means nothing."""
    by_kid: Dict[str, List[Tuple[int, bool]]] = {}
    for key, data in (rosters or {}).items():
        kid = str(key[0])
        for m in (data or {}).get("members") or []:
            fid, power = _fid(m), m.get("power")
            then = ctx.stall_ago.get(fid)
            if fid and power is not None and then:
                by_kid.setdefault(kid, []).append((fid, int(power) != then))
    for kid, rows in by_kid.items():
        changed = sum(1 for _f, c in rows if c)
        if changed < STALL_MIN_CHANGED * len(rows):
            ctx.stall_stale.update(f for f, _c in rows)
            print(f"💤 Kingdom {kid}: only {changed}/{len(rows)} powers changed in {STALL_DAYS} days — "
                  f"MightPulse data looks stale, not flagging anyone as no-growth")


def prepare_power(sh, bot_data: Dict[str, Any], rosters, superseded, record: bool) -> PowerContext:
    """Load history, optionally record today's snapshot, build the context."""
    today = today_str()
    history = load_history(sh)
    new_rows: List[List[Any]] = []
    if record:
        already = {int(r[3]) for r in history if r[0] == today and len(r) > 3 and r[3].isdigit()}
        new_rows = snapshot_rows(rosters, superseded, today, already)
        record_snapshot(sh, new_rows, history, today)
        history = history + [[str(c) for c in r] for r in new_rows]
    ctx = build_context(history, bot_data.get("power_checkpoint"), today)
    mark_stale_kingdoms(ctx, rosters)
    ctx.recorded = len(new_rows) if record else None
    ctx.history_days = len({r[0] for r in history})
    return ctx


def take_checkpoint(bot_data: Dict[str, Any], rosters, superseded) -> int:
    """Store every tracked member's current power as the growth baseline.
    Returns how many players were recorded."""
    power: Dict[str, int] = {}
    for key, data in rosters.items():
        if not data or not data.get("alliance"):
            continue
        skip = superseded.get(key, {})
        for m in data.get("members") or []:
            fid = _fid(m)
            if fid and fid not in skip and m.get("power") is not None:
                power[str(fid)] = int(m["power"])
    now = now_local()
    bot_data["power_checkpoint"] = {
        "date": now.date().isoformat(),
        "at": now.strftime("%Y-%m-%d %H:%M %Z"),
        "power": power,
    }
    return len(power)
