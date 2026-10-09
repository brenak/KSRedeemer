"""Name History tab: every name a player has gone by, kept forever.

The alliance tabs only hold Original_Name and Current_Name, so a second
rename would lose the name in between. Each real sync appends one row per
rename it sees (Current_Name on the sheet -> the live name):

    Player ID | Old Name | New Name | Alliance | Changed On

Never pruned, keyed by Player ID (so it outlives the member's row). When
the tab is first created it's backfilled from the Power History tab (a name
per player per day, last HISTORY_DAYS days), and every sync also adds an
Original_Name -> Current_Name row (Changed On "before tracking") for anyone
renamed before tracking started. /sheet whois searches it.
"""
from typing import Dict, Iterable, List, Optional, Set, Tuple

from services.power_growth import load_history

NAME_TAB = "Name History"
NAME_HEADER = ["Player ID", "Old Name", "New Name", "Alliance", "Changed On"]
BEFORE_TRACKING = "before tracking"

# (player id, old name, new name, alliance tag)
Rename = Tuple[int, str, str, str]


def _ws(sh, create: bool):
    import gspread

    try:
        return sh.worksheet(NAME_TAB), False
    except gspread.exceptions.WorksheetNotFound:
        if not create:
            return None, False
        ws = sh.add_worksheet(title=NAME_TAB, rows=1000, cols=len(NAME_HEADER))
        ws.update(values=[NAME_HEADER], range_name="A1", raw=True)
        sh.batch_update({"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}}]})
        return ws, True


def load(sh) -> List[List[str]]:
    ws, _ = _ws(sh, create=False)
    if ws is None:
        return []
    return [r + [""] * (len(NAME_HEADER) - len(r)) for r in ws.get_all_values()[1:] if r and r[0].strip()]


def renames_from_power_history(history: List[List[str]]) -> List[List[str]]:
    """Name changes between a player's consecutive daily snapshots
    (Date, Kingdom, Alliance, Player ID, Name, Power), oldest first."""
    last: Dict[str, str] = {}
    out = []
    for r in sorted((r for r in history if len(r) >= 5 and r[3].strip()), key=lambda r: r[0]):
        day, tag, pid, name = r[0], r[2], r[3].strip(), r[4].strip()
        if not name:
            continue
        prev = last.get(pid)
        if prev is not None and prev != name:
            out.append([pid, prev, name, tag, day])
        last[pid] = name
    return out


def new_rows(existing: List[List[str]], today: str, renames: Iterable[Rename],
             originals: Iterable[Rename], backfill: Iterable[List[str]] = ()) -> List[List[str]]:
    """Rows to append: today's renames, then backfilled ones, then an
    Original_Name -> current row for anyone whose original name the tab
    doesn't know yet. Nothing already there is added twice."""
    seen: Set[Tuple[str, str, str]] = {(r[0], r[1], r[2]) for r in existing}
    names: Dict[str, Set[str]] = {}
    for r in existing:
        names.setdefault(r[0], set()).update((r[1], r[2]))
    out: List[List[str]] = []

    def add(row: List[str]) -> None:
        key = (row[0], row[1], row[2])
        if row[1] and row[2] and row[1] != row[2] and key not in seen:
            seen.add(key)
            names.setdefault(row[0], set()).update((row[1], row[2]))
            out.append(row)

    for row in backfill:
        add(row)
    for pid, old, new, tag in renames:
        add([str(pid), old, new, tag, today])
    for pid, original, current, tag in originals:
        if original not in names.get(str(pid), ()):
            add([str(pid), original, current, tag, BEFORE_TRACKING])
    return out


def record(sh, today: str, renames: List[Rename], originals: List[Rename]) -> int:
    """Append today's renames (blocking). Returns how many rows were added."""
    ws, created = _ws(sh, create=True)
    existing = [] if created else [r + [""] * (len(NAME_HEADER) - len(r))
                                   for r in ws.get_all_values()[1:] if r and r[0].strip()]
    backfill = renames_from_power_history(load_history(sh)) if created else []
    rows = new_rows(existing, today, renames, originals, backfill)
    if rows:
        # RAW: Player IDs and dates stay text.
        ws.append_rows(rows, value_input_option="RAW", table_range="A1")
    return len(rows)


def latest_names(history: List[List[str]]) -> Dict[str, Tuple[str, str, str]]:
    """Player ID -> (name, alliance, kingdom) from their newest Power History row."""
    out: Dict[str, Tuple[str, str, str]] = {}
    for r in sorted((r for r in history if len(r) >= 5 and r[3].strip()), key=lambda r: r[0]):
        out[r[3].strip()] = (r[4].strip(), r[2], r[1])
    return out


def search(query: str, rows: List[List[str]], current: Dict[str, Tuple[str, str, str]],
           limit: int = 10) -> List[Tuple[str, Optional[Tuple[str, str, str]], List[List[str]]]]:
    """Players whose ID is `query`, or any of whose names (past or current)
    contains it (case-insensitive): [(player id, (name, alliance, kingdom)
    or None, their Name History rows oldest first)]."""
    q = query.strip().lower()
    if not q:
        return []
    by_pid: Dict[str, List[List[str]]] = {}
    for r in rows:
        by_pid.setdefault(r[0], []).append(r)
    hits: List[str] = []
    for pid in list(by_pid) + [p for p in current if p not in by_pid]:
        names = {n for r in by_pid.get(pid, []) for n in (r[1], r[2])}
        if pid in current:
            names.add(current[pid][0])
        if pid == q or any(q in n.lower() for n in names if n):
            hits.append(pid)
    # Exact name / ID matches first, then by current name.
    hits.sort(key=lambda p: (not (p == q or any(
        n.lower() == q for r in by_pid.get(p, []) for n in (r[1], r[2])) or (current.get(p, ("",))[0].lower() == q)),
        (current.get(p) or ("",))[0].lower()))
    order = lambda r: ("" if r[4] == BEFORE_TRACKING else r[4])   # noqa: E731
    return [(p, current.get(p), sorted(by_pid.get(p, []), key=order)) for p in hits[:limit]]
