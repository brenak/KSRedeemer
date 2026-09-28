"""Sync a hand-maintained alliance roster Google Sheet tab against live
MightPulse data -- one tab per alliance, all in GOOGLE_SHEET_ID (the list of
alliance -> tab targets is managed by dcBot/commands/sheetCmd.py). Ported
from kingshot_web's map/services/sos_sheet_sync.py (same columns, colors and
rules); only the data source changed -- MightPulse instead of kingshot_web's
scanner City table.

Data sources, per run:
- One alliance roster request: who is in the alliance right now, their
  current nick. Authoritative for membership (fetched live, <=60 min cache).
- One player lookup per sheet row + per new member: current x/y, and the
  current alliance of anyone no longer on the roster. Player lookups can be
  up to ~a day old on MightPulse's side, so observed positions may lag.

For every sheet row with a Player ID (the in-game FID):
- Unknown to MightPulse and not on the roster -> `notes` = "not found", no
  color change (informational, not a flagged discrepancy).
- Known -> kept fresh regardless of the branches below (no color): the
  name/label column gets their current nick, and, when the tab has them,
  `Kingdom` (so a server transfer shows where they went), `Rank`
  (R1-R4/Leader; their rank in their new alliance if they left),
  `TC_Level` (TG-style, see tc_label) and `Power`.
- Tag: `Current_Tag` is a recorded value like x/y (seeded when the row is
  added, otherwise only changed by hand). Their live tag -- this alliance's
  if they're on the roster, else their new tag, "none" (no alliance) or
  "left" (lookup too stale to say where) -- goes into `observed_tag` when
  it differs from Current_Tag, and observed_tag is cleared when it
  matches. A tab without Current_Tag compares against the tab's alliance.
- Not on the roster -> row painted NOT_IN_ALLIANCE_COLOR. Position isn't
  compared.
- On the roster -> compare the sheet's `x`/`y` (recorded, never
  overwritten here) with the lookup's current x/y:
  - match -> clear observed_x/observed_y, paint MATCH_COLOR
  - differ -> write current x/y into observed_x/observed_y, MISMATCH_COLOR
  - no position available -> left untouched, counted as position_unknown

Roster members with no row at all are appended at the bottom with Player
ID, name, Original_Name, Kingdom, Current_Tag, and x/y seeded from their
current state (so they start out green with empty observed_* columns;
later changes then show up as above). Other columns (notes, anything
hand-curated) are left blank.

Tabs start blank: a missing tab is created and an empty one gets
DEFAULT_HEADER, after which every roster member is appended by the rule
above. (A dry run creates/writes nothing and just previews.)

Columns are resolved by header NAME (case-insensitive), not letter, so
reordering or adding columns is safe. Row highlight runs from column A to
the last of observed_x/observed_y -- extra columns a human adds past that
are never repainted.
"""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config.config import GOOGLE_SHEETS_CREDENTIALS_PATH, GOOGLE_SHEET_ID
from services.mightpulse_client import MightPulseClient, MightPulseError, MightPulseRateLimited

REQUIRED_COLUMNS = ("player id", "x", "y", "observed_x", "observed_y", "observed_tag",
                    "notes", "original_name")
LABEL_COLUMN_CANDIDATES = ("current_name", "label/name", "label", "name")
# Optional columns, used when present: "current_tag" (recorded tag that
# observed_tag is compared against) and "kingdom" (live kingdom number).

MATCH_COLOR = "4ea72e"
MISMATCH_COLOR = "fbbc04"
# Google Sheets' own standard palette swatch "light purple 1".
NOT_IN_ALLIANCE_COLOR = "8e7cc3"
NOT_FOUND_NOTE = "not found"
# Power shown as 235,248,429 -- applied to the Power column on every write.
POWER_NUMBER_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}
# Font applied to a new/blank tab (Sheets' own default is Arial 10).
TAB_FONT = {"fontFamily": "Arial", "fontSize": 11}

# Written into a blank/new tab. Highlight covers A..observed_y (K here);
# notes sits just past it and keeps its own formatting. "Member #" is
# written as a self-filling formula (MEMBER_NUMBER_FORMULA), not text.
MEMBER_NUMBER_HEADER = "Member #"
DEFAULT_HEADER = [MEMBER_NUMBER_HEADER, "Kingdom", "Player ID", "Original_Name", "Current_Name",
                  "Current_Tag", "Rank", "TC_Level", "Power", "x", "y",
                  "observed_tag", "observed_x", "observed_y", "notes"]

# Optional live stat columns -- refreshed every sync when the tab has them.
# canonical key -> accepted header names (case-insensitive).
STAT_COLUMN_ALIASES = {
    "alliance_rank": ("alliance_rank", "rank"),
    "tc_level": ("tc_level", "tc", "town_center", "town_center_level"),
    "power": ("power",),
}
# Header cell that displays "Member #" and numbers every row with a Player
# ID 1..N top-down (renumbers after sorting/filtering). The column below it
# must stay empty for the array to fill -- which is why appended rows skip
# unset cells (None) instead of writing "".
MEMBER_NUMBER_FORMULA = '={{"{header}"; ARRAYFORMULA(IF({pid}2:{pid}="", , ROW({pid}2:{pid})-1))}}'


class SheetSyncError(Exception):
    """Anything that stops the sync before it writes: missing credentials,
    sheet/tab not found or not shared, missing column, unknown alliance,
    MightPulse unavailable. Per-row problems are counted, not fatal."""


@dataclass
class SyncResult:
    matched: int = 0
    mismatched: int = 0
    not_in_alliance: int = 0
    labels_updated: int = 0
    kingdoms_changed: int = 0
    position_unknown: int = 0
    skipped_no_player_id: int = 0
    skipped_not_found: int = 0
    new_members_added: int = 0
    new_member_nicks: List[str] = field(default_factory=list)
    # "created" (tab didn't exist) / "initialized" (tab was blank) / ""
    tab_setup: str = ""
    dry_run: bool = False

    def summary_lines(self) -> List[str]:
        lines = []
        if self.tab_setup == "created":
            lines.append("🆕 Tab doesn't exist yet — " + (
                "would be created with a header row" if self.dry_run else "created with a header row"))
        elif self.tab_setup == "initialized":
            lines.append("🆕 Tab is blank — " + (
                "would get a header row" if self.dry_run else "header row written"))
        lines += [
            f"🟩 Matched: {self.matched}",
            f"🟨 Position changed: {self.mismatched}",
            f"🟪 No longer in alliance: {self.not_in_alliance}",
            f"➕ New members {'to add' if self.dry_run else 'added'}: {self.new_members_added}",
        ]
        if self.labels_updated:
            lines.append(f"🏷️ Names updated: {self.labels_updated}")
        if self.kingdoms_changed:
            lines.append(f"🌍 Kingdom changed (transferred): {self.kingdoms_changed}")
        if self.position_unknown:
            lines.append(f"❔ Position unavailable: {self.position_unknown}")
        if self.skipped_not_found:
            lines.append(f"⚠️ Not found on MightPulse: {self.skipped_not_found}")
        if self.skipped_no_player_id:
            lines.append(f"⏭️ Rows without a Player ID: {self.skipped_no_player_id}")
        if self.new_member_nicks:
            names = ", ".join(f"`{n}`" for n in self.new_member_nicks[:25])
            more = f" (+{len(self.new_member_nicks) - 25} more)" if len(self.new_member_nicks) > 25 else ""
            lines.append(f"New: {names}{more}")
        return lines


@dataclass
class SyncPlan:
    result: SyncResult
    value_updates: List[Dict[str, Any]]   # gspread batch_update payload
    row_colors: List[Tuple[int, str]]     # (1-indexed row, hex color)
    new_rows: List[List[Optional[str]]]   # None = leave cell untouched
    new_row_colors: List[Optional[str]]   # parallel to new_rows (None = no color)
    highlight_end: str                    # last highlighted column letter
    power_col: Optional[str] = None       # Power column letter, if the tab has one


def _hex_to_rgb_float(hex_color: str) -> Dict[str, float]:
    return {
        "red": int(hex_color[0:2], 16) / 255,
        "green": int(hex_color[2:4], 16) / 255,
        "blue": int(hex_color[4:6], 16) / 255,
    }


def _col_letter(index: int) -> str:
    """0-based column index -> A1 letter(s) (0->A, 25->Z, 26->AA, ...)."""
    letters = ""
    n = index + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _parse_int_cell(row: List[str], idx: int) -> Optional[int]:
    if idx >= len(row):
        return None
    raw = row[idx].strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def resolve_columns(header: List[str]) -> Dict[str, int]:
    norm = {h.strip().lower(): i for i, h in enumerate(header)}
    missing = [c for c in REQUIRED_COLUMNS if c not in norm]
    if missing:
        raise SheetSyncError(
            f"Sheet header is missing required column(s): {', '.join(missing)}. "
            f"Found columns: {', '.join(header)}"
        )
    label_col = next((norm[c] for c in LABEL_COLUMN_CANDIDATES if c in norm), None)
    if label_col is None:
        raise SheetSyncError(
            f"Sheet header has no name column (tried: {', '.join(LABEL_COLUMN_CANDIDATES)}). "
            f"Found columns: {', '.join(header)}"
        )
    stat_cols = {
        key: next(norm[a] for a in aliases if a in norm)
        for key, aliases in STAT_COLUMN_ALIASES.items()
        if any(a in norm for a in aliases)
    }
    # Canonical keys win over any header literally named the same thing.
    return {**norm, **stat_cols, "_label": label_col}


def _live_kingdom(member: Optional[Dict[str, Any]], player: Optional[Dict[str, Any]]) -> str:
    """Roster kid (live) first, else the player lookup's; "" if unknown."""
    kid = (member or {}).get("kid") or (player or {}).get("kid")
    return str(kid) if kid else ""


def tc_label(level: Any) -> str:
    """Town center level as shown in game (same mapping as kingshot_web's
    formatCityLevel): 1-34 plain, then 5 levels per True Gold tier --
    35 -> TG1, 36..39 -> TG1.1..TG1.4, 40 -> TG2, ... 54 -> TG4.4, 55 -> TG5."""
    try:
        n = int(level)
    except (TypeError, ValueError):
        return ""
    if n < 35:
        return str(n)
    tier, sub = divmod(n - 35, 5)
    return f"TG{tier + 1}" if sub == 0 else f"TG{tier + 1}.{sub}"


def _live_stats(member: Optional[Dict[str, Any]], player: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Current alliance rank / TC level / power, roster first (live), else
    the player lookup. A key is omitted when the value is unknown (so the
    cell is left alone); "" means known-empty (e.g. rank when in no alliance)."""
    m, p = member or {}, player or {}
    stats: Dict[str, str] = {}

    if member is not None:
        rank = m.get("alliance_rank_label")
    elif player is not None:
        alliance = p.get("alliance")
        rank = (alliance or {}).get("rank_label") if alliance else ""
    else:
        rank = None
    if rank is not None:
        stats["alliance_rank"] = rank

    level = m.get("town_center_level") if m.get("town_center_level") is not None else p.get("town_center_level")
    if level is not None:
        stats["tc_level"] = tc_label(level)

    power = m.get("power") if m.get("power") is not None else p.get("power")
    if power is not None:
        stats["power"] = str(int(power))
    return stats


def _same_cell(sheet_value: str, value: str) -> bool:
    # Power is displayed with separators ("66,331,112"); compare digits only.
    return sheet_value.replace(",", "") == value.replace(",", "")


def row_player_ids(data_rows: List[List[str]], col: Dict[str, int]) -> List[int]:
    return [pid for pid in (_parse_int_cell(r, col["player id"]) for r in data_rows) if pid is not None]


def plan_sync(
    header: List[str],
    data_rows: List[List[str]],
    abbr: str,
    roster: Dict[int, Dict[str, Any]],
    players: Dict[int, Optional[Dict[str, Any]]],
) -> SyncPlan:
    """Pure decision logic. `roster` maps FID -> roster member for the
    target alliance; `players` maps FID -> MightPulse `player` object (None
    = unknown to MightPulse)."""
    col = resolve_columns(header)
    result = SyncResult()
    value_updates: List[Dict[str, Any]] = []
    row_colors: List[Tuple[int, str]] = []
    seen: set = set()

    def put(col_key: Any, row_num: int, value: Any) -> None:
        idx = col[col_key] if isinstance(col_key, str) else col_key
        value_updates.append({"range": f"{_col_letter(idx)}{row_num}", "values": [[value]]})

    def cell(row: List[str], col_key: str) -> str:
        idx = col[col_key]
        return row[idx].strip() if idx < len(row) else ""

    def put_if_changed(row: List[str], col_key: str, row_num: int, value: str) -> None:
        if col_key in col and cell(row, col_key) != value:
            put(col_key, row_num, value)

    for offset, row in enumerate(data_rows):
        row_num = offset + 2  # 1-indexed, +1 for the header row
        pid = _parse_int_cell(row, col["player id"])
        if pid is None:
            result.skipped_no_player_id += 1
            continue
        seen.add(pid)

        member = roster.get(pid)
        player = players.get(pid)
        if member is None and player is None:
            result.skipped_not_found += 1
            put("notes", row_num, NOT_FOUND_NOTE)
            continue

        # Roster nick is fresher than a possibly day-old player lookup.
        nick = (member or {}).get("nick_name") or (player or {}).get("nick_name")
        sheet_label = row[col["_label"]].strip() if col["_label"] < len(row) else ""
        if nick and nick != sheet_label:
            result.labels_updated += 1
            put("_label", row_num, nick)

        # Kingdom is live, like the name: a transfer shows where they went.
        kid = _live_kingdom(member, player)
        if kid and "kingdom" in col and cell(row, "kingdom") != kid:
            if cell(row, "kingdom"):
                result.kingdoms_changed += 1
            put("kingdom", row_num, kid)

        # Rank / TC / power: live, only written when they actually changed.
        for key, value in _live_stats(member, player).items():
            if key in col and not _same_cell(cell(row, key), value):
                put(key, row_num, value)

        # Tag is compared against the recorded Current_Tag (like x/y), and
        # the live one surfaces in observed_tag only when they differ.
        if member is not None:
            live_tag = abbr
        else:
            live_tag = ((player or {}).get("alliance") or {}).get("abbr") or ""
            if not live_tag:
                live_tag = "none"
            elif live_tag == abbr:
                live_tag = "left"  # off the live roster; lookup not caught up yet
        recorded_tag = cell(row, "current_tag") if "current_tag" in col else abbr
        put_if_changed(row, "observed_tag", row_num, "" if live_tag == recorded_tag else live_tag)

        if member is None:
            result.not_in_alliance += 1
            row_colors.append((row_num, NOT_IN_ALLIANCE_COLOR))
            continue

        cur_x, cur_y = (player or {}).get("x"), (player or {}).get("y")
        if cur_x is None or cur_y is None:
            result.position_unknown += 1
            continue

        sheet_x = _parse_int_cell(row, col["x"])
        sheet_y = _parse_int_cell(row, col["y"])
        if sheet_x == cur_x and sheet_y == cur_y:
            result.matched += 1
            put("observed_x", row_num, "")
            put("observed_y", row_num, "")
            row_colors.append((row_num, MATCH_COLOR))
        else:
            result.mismatched += 1
            put("observed_x", row_num, cur_x)
            put("observed_y", row_num, cur_y)
            row_colors.append((row_num, MISMATCH_COLOR))

    new_rows: List[List[Optional[str]]] = []
    new_row_colors: List[Optional[str]] = []
    for fid, member in roster.items():
        if fid in seen:
            continue
        player = players.get(fid) or {}
        nick = member.get("nick_name") or player.get("nick_name") or ""
        # None = skip the cell (JSON null), so appending never blanks out a
        # formula column like Member # or anything a human added.
        new_row: List[Optional[str]] = [None] * len(header)
        new_row[col["player id"]] = str(fid)
        # Original_Name is only ever written here, at first sight.
        new_row[col["_label"]] = nick
        new_row[col["original_name"]] = nick
        if "current_tag" in col:
            new_row[col["current_tag"]] = abbr
        kid = _live_kingdom(member, player)
        if kid and "kingdom" in col:
            new_row[col["kingdom"]] = kid
        for key, value in _live_stats(member, players.get(fid)).items():
            if key in col and value != "":
                new_row[col[key]] = value
        if player.get("x") is not None and player.get("y") is not None:
            # Seed x/y with where they are now, so they start out matched
            # and a later move flags gold.
            new_row[col["x"]] = str(player["x"])
            new_row[col["y"]] = str(player["y"])
            new_row_colors.append(MATCH_COLOR)
        else:
            new_row_colors.append(None)
        new_rows.append(new_row)
        result.new_members_added += 1
        result.new_member_nicks.append(nick or f"fid={fid}")

    highlight_end = _col_letter(max(col["observed_x"], col["observed_y"]))
    power_col = _col_letter(col["power"]) if "power" in col else None
    return SyncPlan(result, value_updates, row_colors, new_rows, new_row_colors, highlight_end, power_col)


def roster_key(kid: str, abbr: str) -> Tuple[str, str]:
    """Cache key for an alliance roster within one run."""
    return (str(kid), abbr)


def configured() -> bool:
    return bool(GOOGLE_SHEETS_CREDENTIALS_PATH and GOOGLE_SHEET_ID)


def open_spreadsheet():
    """Authenticated gspread Spreadsheet for GOOGLE_SHEET_ID (blocking)."""
    if not GOOGLE_SHEETS_CREDENTIALS_PATH:
        raise SheetSyncError("GOOGLE_SHEETS_CREDENTIALS_PATH is not set.")
    if not GOOGLE_SHEET_ID:
        raise SheetSyncError("GOOGLE_SHEET_ID is not set.")

    import gspread  # lazy: only /sheet needs it

    try:
        gc = gspread.service_account(filename=GOOGLE_SHEETS_CREDENTIALS_PATH)
        return gc.open_by_key(GOOGLE_SHEET_ID)
    except FileNotFoundError as exc:
        raise SheetSyncError(
            f"Google credentials file not found at `{GOOGLE_SHEETS_CREDENTIALS_PATH}`."
        ) from exc
    except gspread.exceptions.SpreadsheetNotFound as exc:
        raise SheetSyncError(
            "Spreadsheet not found or not shared with the service account "
            "(share it with the key's client_email as Editor)."
        ) from exc


def _read_sheet(tab: str):
    """(spreadsheet, worksheet or None if the tab doesn't exist, all values).
    A missing tab is only created at write time (_create_tab), after every
    MightPulse request has succeeded -- a typo'd tag never leaves an empty
    tab behind, and a dry run never touches the spreadsheet."""
    import gspread

    sh = open_spreadsheet()
    try:
        ws = sh.worksheet(tab)
    except gspread.exceptions.WorksheetNotFound:
        return sh, None, []
    return sh, ws, ws.get_all_values()


def _create_tab(sh, tab: str):
    return sh.add_worksheet(title=tab, rows=200, cols=len(DEFAULT_HEADER))


def _is_blank(values: List[List[Any]]) -> bool:
    """gspread returns [[]] (not []) for a tab with no values at all, and
    padded rows of "" for one that was cleared -- neither is a header."""
    return not any(str(c).strip() for r in values for c in r)


def _header_row_to_write(header: List[str]) -> List[str]:
    """DEFAULT_HEADER as written: Member # becomes its numbering formula."""
    norm = [h.strip().lower() for h in header]
    row = list(header)
    if MEMBER_NUMBER_HEADER.lower() in norm and "player id" in norm:
        pid = _col_letter(norm.index("player id"))
        row[norm.index(MEMBER_NUMBER_HEADER.lower())] = MEMBER_NUMBER_FORMULA.format(
            header=MEMBER_NUMBER_HEADER, pid=pid
        )
    return row


def _init_header(ws, header: List[str]) -> None:
    # Whole tab (every row/column, so appended rows match): Arial 11 instead
    # of Sheets' Arial 10 default. Field mask touches only font family/size.
    ws.client.batch_update(ws.spreadsheet_id, {"requests": [{"repeatCell": {
        "range": {"sheetId": ws.id},
        "cell": {"userEnteredFormat": {"textFormat": dict(TAB_FONT)}},
        "fields": "userEnteredFormat.textFormat(fontFamily,fontSize)",
    }}]})
    # USER_ENTERED so the Member # formula is evaluated, not stored as text.
    ws.update(values=[_header_row_to_write(header)], range_name="A1", raw=False)
    ws.freeze(rows=1)
    # ws.format replaces the whole textFormat, so the font must ride along
    # with bold or the header would fall back to the default font.
    ws.format(f"A1:{_col_letter(len(header) - 1)}1", {"textFormat": {"bold": True, **TAB_FONT}})
    # Whole-sheet basic filter (no range = every row, incl. ones appended
    # later), so the header gets filter/sort dropdowns like the original tab.
    ws.set_basic_filter()


def _appended_start_row(response: Any) -> Optional[int]:
    """First row number of an append_rows call, from its updatedRange
    (e.g. "'1MK'!A2:I23" -> 2)."""
    try:
        updated = response["updates"]["updatedRange"]
    except (TypeError, KeyError):
        return None
    m = re.search(r"![A-Z]+(\d+)", updated)
    return int(m.group(1)) if m else None


def _player_id_column(all_values: List[List[str]], pid_idx: int) -> List[str]:
    return [(r[pid_idx].strip() if pid_idx < len(r) else "") for r in all_values[1:]]


def _write_plan(ws, plan: SyncPlan, header: List[str], fresh: bool,
                pid_idx: int, expected_ids: List[str]) -> None:
    # The MightPulse lookups take minutes; if rows were inserted, deleted or
    # re-sorted meanwhile, every planned cell address would be off. Re-read
    # and refuse to write rather than paint the wrong rows.
    current = ws.get_all_values()
    changed = (not _is_blank(current)) if fresh else (
        _player_id_column(current, pid_idx)[: len(expected_ids)] != expected_ids
    )
    if changed:
        raise SheetSyncError(
            "The tab changed while the sync was running (rows added, removed or "
            "re-sorted) — nothing was written. Run /sheet sync again."
        )

    if fresh:
        _init_header(ws, header)

    # Batched: one call for all values, one append, one for all colors --
    # a full roster is a handful of API calls, not one per row.
    if plan.value_updates:
        ws.batch_update(plan.value_updates, value_input_option="USER_ENTERED")

    colors = list(plan.row_colors)
    if plan.new_rows:
        response = ws.append_rows(plan.new_rows, value_input_option="USER_ENTERED")
        start = _appended_start_row(response)
        if start is not None:
            colors += [(start + i, c) for i, c in enumerate(plan.new_row_colors) if c]

    formats = [
        {"range": f"A{row_num}:{plan.highlight_end}{row_num}",
         "format": {"backgroundColor": _hex_to_rgb_float(color)}}
        for row_num, color in colors
    ]
    if plan.power_col:
        # Every run, not just on new tabs, so a hand-added Power column gets
        # thousands separators too. Only the number format field is touched.
        formats.append({"range": f"{plan.power_col}2:{plan.power_col}", "format": POWER_NUMBER_FORMAT})
    if formats:
        ws.batch_format(formats)


async def sync_alliance_sheet(
    client: MightPulseClient,
    kid: str,
    abbr: str,
    tab: str,
    dry_run: bool = False,
    player_cache: Optional[Dict[int, Optional[Dict[str, Any]]]] = None,
    roster_cache: Optional[Dict[Tuple[str, str], Optional[Dict[str, Any]]]] = None,
) -> SyncResult:
    """Read one tab, fetch roster + per-player data from MightPulse, write
    the result back. Nothing is written unless every MightPulse request
    succeeded, so a rate-limited run never leaves the tab half-updated.
    Pass the same `player_cache` dict when syncing several tabs in one run
    so a player appearing on more than one tab is only looked up once, and
    a `roster_cache` to keep the rosters (keyed by roster_key) for reuse --
    the analytics tab builds on them without re-fetching."""
    if not client.configured():
        raise SheetSyncError("MightPulse isn't configured (MIGHTPULSE_API_KEY).")

    # gspread is blocking -- keep it off the event loop.
    sh, ws, all_values = await asyncio.to_thread(_read_sheet, tab)
    fresh = _is_blank(all_values)  # new or blank tab: gets DEFAULT_HEADER + every member
    if fresh:
        header, data_rows = list(DEFAULT_HEADER), []
    else:
        header, data_rows = all_values[0], all_values[1:]
    col = resolve_columns(header)  # fail fast before spending API quota

    try:
        key = roster_key(kid, abbr)
        if roster_cache is not None and key in roster_cache:
            data = roster_cache[key]
        else:
            data = await client.get_alliance_roster(kid, abbr)
            if roster_cache is not None:
                roster_cache[key] = data
        if not data or not data.get("alliance"):
            raise SheetSyncError(f"No alliance `{abbr}` in kingdom `{kid}` on MightPulse (tag is case-sensitive).")
        roster_abbr = data["alliance"].get("abbr") or abbr
        roster = {}
        for m in data.get("members") or []:
            fid = m.get("fid") or m.get("governor_id")
            if fid:
                roster[int(fid)] = m

        cache = player_cache if player_cache is not None else {}
        players: Dict[int, Optional[Dict[str, Any]]] = {}
        for fid in dict.fromkeys(row_player_ids(data_rows, col) + list(roster)):
            if fid not in cache:
                cache[fid] = await client.get_player(str(fid))
            players[fid] = cache[fid]
    except MightPulseRateLimited as exc:
        raise SheetSyncError("MightPulse rate limit hit — nothing was written. Try again in a few minutes.") from exc
    except MightPulseError as exc:
        raise SheetSyncError(f"MightPulse error — nothing was written: {exc}") from exc

    plan = plan_sync(header, data_rows, roster_abbr, roster, players)
    plan.result.dry_run = dry_run
    if fresh:
        plan.result.tab_setup = "created" if ws is None else "initialized"
    if not dry_run:
        if ws is None:
            ws = await asyncio.to_thread(_create_tab, sh, tab)
        pid_idx = col["player id"]
        await asyncio.to_thread(
            _write_plan, ws, plan, header, fresh, pid_idx, _player_id_column(all_values, pid_idx)
        )
    return plan.result
