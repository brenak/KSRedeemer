"""Tracked-alliance health over time: members, active, no growth (trending
inactive) and inactive (no map position), recorded per alliance per day.

Every real sheet sync records one row per synced alliance into a hidden
`Health History` tab (a re-sync the same day replaces that day's row). The
Analytics tab charts the totals across all tracked alliances by date; an
alliance that wasn't synced on a given day carries its latest earlier
numbers forward, so a single-tab /sheet sync doesn't make the totals dip.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

HEALTH_TAB = "Health History"
HEALTH_HEADER = ["Date", "Alliance", "Members", "Active", "No growth", "Inactive"]
HEALTH_DAYS = 365          # rows older than this are pruned
PIE_HEADER = ["Status", "Players"]

Counts = Tuple[int, int, int, int]   # members, active, no growth, inactive


def counts_from_result(result: Any) -> Counts:
    """(members, active, no growth, inactive) from a SyncResult."""
    members = int(getattr(result, "members", 0) or 0)
    stalled = int(getattr(result, "stalled", 0) or 0)
    inactive = int(getattr(result, "position_unknown", 0) or 0)
    return members, max(0, members - stalled - inactive), stalled, inactive


def merged_rows(existing: List[List[str]], today: str, counts: Dict[str, Counts]) -> List[List[Any]]:
    """History rows (no header) with today's rows for `counts` replacing any
    already there, older-than-HEALTH_DAYS rows dropped, sorted by date then
    alliance. Pure -- tested offline."""
    cutoff = (date.fromisoformat(today) - timedelta(days=HEALTH_DAYS)).isoformat()
    keep = [r for r in existing
            if len(r) >= 6 and r[0] and r[0] >= cutoff and not (r[0] == today and r[1] in counts)]
    keep += [[today, alliance, *c] for alliance, c in counts.items()]
    return sorted(keep, key=lambda r: (str(r[0]), str(r[1])))


def series(rows: List[List[Any]]) -> List[List[Any]]:
    """[date, total, active, no growth, inactive] per date across alliances,
    each alliance carrying its latest numbers forward. Pure."""
    def as_int(v: Any) -> int:
        try:
            return int(str(v).replace(",", ""))
        except ValueError:
            return 0

    by_date: Dict[str, Dict[str, Counts]] = {}
    for r in rows:
        if len(r) >= 6 and r[0]:
            by_date.setdefault(str(r[0]), {})[str(r[1])] = tuple(as_int(v) for v in r[2:6])
    latest: Dict[str, Counts] = {}
    out = []
    for day in sorted(by_date):
        latest.update(by_date[day])
        totals = [sum(c[i] for c in latest.values()) for i in range(4)]
        out.append([day, *totals])
    return out


def latest_by_alliance(rows: List[List[Any]]) -> Dict[str, Counts]:
    """Each alliance's most recent (members, active, no growth, inactive).
    Pure."""
    out: Dict[str, Tuple[str, Counts]] = {}
    for r in rows:
        if len(r) < 6 or not r[0]:
            continue
        try:
            counts = tuple(int(str(v).replace(",", "")) for v in r[2:6])
        except ValueError:
            continue
        day, alliance = str(r[0]), str(r[1])
        if alliance not in out or day >= out[alliance][0]:
            out[alliance] = (day, counts)
    return {a: c for a, (_d, c) in out.items()}


def pie_block(series_rows: List[List[Any]]) -> Tuple[List[List[Any]], str]:
    """(pie data -- header + Active / No growth / Inactive -- and its title
    with the total) for the latest day in `series_rows`; ([], "") if there's
    no history yet. Pure."""
    if not series_rows:
        return [], ""
    day, total, active, stalled, inactive = series_rows[-1]
    # The count goes in each label: the API can't print values on slices.
    rows = [PIE_HEADER, [f"Active: {active}", active], [f"No growth: {stalled}", stalled],
            [f"Inactive: {inactive}", inactive]]
    return rows, f"Activity - {total:,} players ({day})"


def _ws(sh, create: bool):
    import gspread

    try:
        return sh.worksheet(HEALTH_TAB)
    except gspread.exceptions.WorksheetNotFound:
        if not create:
            return None
        ws = sh.add_worksheet(title=HEALTH_TAB, rows=200, cols=len(HEALTH_HEADER))
        sh.batch_update({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "hidden": True}, "fields": "hidden"}}]})
        return ws


def load(sh) -> List[List[str]]:
    ws = _ws(sh, create=False)
    if ws is None:
        return []
    values = ws.get_all_values()
    return values[1:] if values else []


def record(sh, today: str, counts: Dict[str, Counts]) -> None:
    """Write today's rows for `counts` (replacing any from earlier today)."""
    if not counts:
        return
    ws = _ws(sh, create=True)
    values = ws.get_all_values()
    rows = merged_rows(values[1:] if values else [], today, counts)
    ws.clear()
    if len(rows) + 1 > ws.row_count:
        ws.add_rows(len(rows) + 1 - ws.row_count)
    ws.update(values=[HEALTH_HEADER] + rows, range_name="A1", raw=True)
