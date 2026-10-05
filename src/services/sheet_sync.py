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
  "left" (lookup too stale to say where) -- goes into `o_tag` when
  it differs from Current_Tag, and o_tag is cleared when it
  matches. A tab without Current_Tag compares against the tab's alliance.
- Not on the roster -> row painted NOT_IN_ALLIANCE_COLOR. Position isn't
  compared.
- On the roster -> compare the sheet's `x`/`y` (recorded, never
  overwritten here) with the lookup's current x/y:
  - match -> clear o_x/o_y, paint MATCH_COLOR
  - differ -> write current x/y into o_x/o_y, MISMATCH_COLOR
  - no position available (MightPulse has no map location -- in practice
    an inactive player) -> INACTIVE_COLOR, and the row is moved below the
    active ones; counted as position_unknown

Roster members with no row at all are appended at the bottom with Player
ID, name, Original_Name, Kingdom, Current_Tag, and x/y seeded from their
current state (so they start out green with empty o_* columns;
later changes then show up as above). Other columns (notes, anything
hand-curated) are left blank.

Tabs start blank: a missing tab is created and an empty one gets
DEFAULT_HEADER, after which every roster member is appended by the rule
above. (A dry run creates/writes nothing and just previews.)

Columns are resolved by header NAME (case-insensitive), not letter, so
reordering or adding columns is safe. Row highlight runs from column A to
the last of o_x/o_y -- extra columns a human adds past that
are never repainted.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config.config import GOOGLE_SHEETS_CREDENTIALS_PATH, GOOGLE_SHEET_ID
from services.mightpulse_client import MightPulseClient, MightPulseError, MightPulseRateLimited
from services.power_growth import FLAG_COLOR, STALL_COLOR, STALL_DAYS, PowerContext, alliance_growth, now_local

REQUIRED_COLUMNS = ("player id", "x", "y", "observed_x", "observed_y", "observed_tag",
                    "notes", "original_name")
# Short header names on the sheet (old long name -> new). The code keeps the
# long names as its internal keys; resolve_columns accepts either, and a real
# sync renames old headers in place (header_renames). p_x / p_y are KSHive's
# planned-position columns -- renamed here too, KSHive moves them next to x / y.
HEADER_RENAMES = {"observed_tag": "o_tag", "observed_x": "o_x", "observed_y": "o_y",
                  "planned_x": "p_x", "planned_y": "p_y"}
LABEL_COLUMN_CANDIDATES = ("current_name", "label/name", "label", "name")
# Optional columns, used when present: "current_tag" (recorded tag that
# o_tag is compared against) and "kingdom" (live kingdom number).

MATCH_COLOR = "4ea72e"
MISMATCH_COLOR = "fbbc04"
# Google Sheets' own standard palette swatch "light purple 1".
NOT_IN_ALLIANCE_COLOR = "8e7cc3"
# On the roster but MightPulse has no map position for them -- in practice
# an inactive player. Google's "dark gray 1" swatch; these rows are also
# moved to the bottom of the tab (see _move_inactive_to_bottom).
INACTIVE_COLOR = "b7b7b7"
NOT_FOUND_NOTE = "not found"
# Power shown as 235,248,429 -- applied to the Power column on every write.
POWER_NUMBER_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}
# Horizontal alignment per column (resolved by header name), applied every
# write. Rank/TC left so "TG4.4" and plain levels like 30 line up.
COLUMN_ALIGNMENT = {
    "current_tag": "CENTER",
    "observed_tag": "CENTER",
    "alliance_rank": "LEFT",
    "tc_level": "LEFT",
}
# Member rows are ordered by alliance rank (then kept in their existing
# order within a rank). Anything MightPulse doesn't label sorts last.
RANK_ORDER = {"leader": 0, "r5": 0, "r4": 1, "r3": 2, "r2": 3, "r1": 4}
UNKNOWN_RANK = len(set(RANK_ORDER.values()))


def rank_sort_key(label: Any) -> int:
    return RANK_ORDER.get(str(label or "").strip().lower(), UNKNOWN_RANK)


# Alliances cap at 100 members (rows 2-101); ex-members -- people who left
# the alliance, or IDs MightPulse doesn't know -- are parked from here down.
DEPARTED_SECTION_ROW = 102
# Extra width per column on top of Google's auto-fit, for the header's
# filter dropdown button (auto-fit doesn't account for it).
FILTER_BUTTON_PX = 14
# Font applied to a new/blank tab (Sheets' own default is Arial 10).
TAB_FONT = {"fontFamily": "Arial", "fontSize": 11}

# Written into a blank/new tab. Highlight covers A..o_y; notes sits
# just past it and keeps its own formatting.
DEFAULT_HEADER = ["Kingdom", "Player ID", "Original_Name", "Current_Name",
                  "Current_Tag", "Rank", "TC_Level", "Power", "Growth %", "vs Alliance", "7d Growth %", "x", "y",
                  "o_tag", "o_x", "o_y", "notes"]

# Optional live stat columns -- refreshed every sync when the tab has them.
# canonical key -> accepted header names (case-insensitive).
STAT_COLUMN_ALIASES = {
    "alliance_rank": ("alliance_rank", "rank"),
    "tc_level": ("tc_level", "tc", "town_center", "town_center_level"),
    "power": ("power",),
}
# Optional growth columns (services/power_growth.py), filled for members
# every sync; canonical key -> accepted header names.
GROWTH_COLUMN_ALIASES = {
    "growth_pct": ("growth %", "growth_pct", "growth"),
    "vs_alliance": ("vs alliance", "vs_alliance"),
    "week_pct": ("7d growth %", "7d_growth_pct", "7d growth"),
}
GROWTH_NUMBER_FORMATS = {
    "growth_pct": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}},
    "week_pct": {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}},
    "vs_alliance": {"numberFormat": {"type": "NUMBER", "pattern": '0.0"×"'}},
}


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
    stalled: int = 0                  # no power growth for STALL_DAYS+ days
    members: int = 0                  # on this alliance's live roster (health chart)
    misplaced_cleared: int = 0        # shifted copies of a member row cleared (see _misplaced_member_row)
    skipped_no_player_id: int = 0
    skipped_not_found: int = 0
    new_members_added: int = 0
    new_member_nicks: List[str] = field(default_factory=list)
    columns_added: List[str] = field(default_factory=list)   # optional columns inserted into an existing tab
    headers_renamed: List[str] = field(default_factory=list)  # "observed_x → o_x" -- long names shortened
    tab_gid: Optional[int] = None     # the tab's sheet id, for a link straight to it
    flagged_nicks: List[str] = field(default_factory=list)   # growing much faster than the alliance
    # "created" (tab didn't exist) / "initialized" (tab was blank) / ""
    tab_setup: str = ""
    dry_run: bool = False

    def compact_summary(self) -> str:
        """One line for the daily auto-sync post."""
        parts = [f"🟩{self.matched}", f"🟨{self.mismatched}", f"🟪{self.not_in_alliance}",
                 f"⬜{self.position_unknown}", f"➕{self.new_members_added}"]
        if self.stalled:
            parts.append(f"💤{self.stalled} no growth")
        if self.kingdoms_changed:
            parts.append(f"🌍{self.kingdoms_changed} transferred")
        if self.flagged_nicks:
            parts.append(f"🔴{len(self.flagged_nicks)} growing fast")
        if self.columns_added:
            parts.append(f"🧱 +{len(self.columns_added)} column(s)")
        if self.skipped_not_found:
            parts.append(f"⚠️{self.skipped_not_found} not found")
        if self.tab_setup:
            parts.append("🆕 new tab")
        return " ".join(parts)

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
        if self.columns_added:
            added = ", ".join(f"`{c}`" for c in self.columns_added)
            lines.append(f"🧱 {'Would add' if self.dry_run else 'Added'} column(s): {added}")
        if self.headers_renamed:
            lines.append(f"🔤 {'Would rename' if self.dry_run else 'Renamed'} header(s): "
                         + ", ".join(self.headers_renamed))
        if self.labels_updated:
            lines.append(f"🏷️ Names updated: {self.labels_updated}")
        if self.kingdoms_changed:
            lines.append(f"🌍 Kingdom changed (transferred): {self.kingdoms_changed}")
        if self.stalled:
            lines.append(f"💤 No power growth for {STALL_DAYS}+ days (trending inactive — moved down): {self.stalled}")
        if self.position_unknown:
            lines.append(f"⬜ Inactive (no map position — moved to the bottom): {self.position_unknown}")
        if self.skipped_not_found:
            lines.append(f"⚠️ Not found on MightPulse: {self.skipped_not_found}")
        if self.misplaced_cleared:
            lines.append(f"🧹 {'Would clear' if self.dry_run else 'Cleared'} misplaced row(s) "
                         f"(a member's data shifted to the right): {self.misplaced_cleared}")
        if self.skipped_no_player_id:
            lines.append(f"⏭️ Rows without a Player ID: {self.skipped_no_player_id}")
        if self.flagged_nicks:
            names = ", ".join(f"`{n}`" for n in self.flagged_nicks[:25])
            lines.append(f"🔴 Growing much faster than the alliance (likely spending): {names}")
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
    inactive_rows: List[int] = field(default_factory=list)   # existing rows (1-indexed) with no position
    departed_rows: List[int] = field(default_factory=list)   # existing rows not on the roster (left / not found)
    column_formats: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)  # (column letter, number format)
    flag_cells: List[Tuple[int, str]] = field(default_factory=list)                 # (row, column letter) -> FLAG_COLOR
    row_ranks: Dict[int, int] = field(default_factory=dict)       # existing member row -> rank_sort_key
    new_row_ranks: List[int] = field(default_factory=list)        # parallel to new_rows
    stalled_rows: List[int] = field(default_factory=list)         # existing rows: no growth for STALL_DAYS+ days
    clear_rows: List[int] = field(default_factory=list)           # misplaced copies to empty (values + color)


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
    for old, new in HEADER_RENAMES.items():   # o_x also answers to observed_x, etc.
        if new in norm and old not in norm:
            norm[old] = norm[new]
    missing = [HEADER_RENAMES.get(c, c) for c in REQUIRED_COLUMNS if c not in norm]
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
        for key, aliases in {**STAT_COLUMN_ALIASES, **GROWTH_COLUMN_ALIASES}.items()
        if any(a in norm for a in aliases)
    }
    # Canonical keys win over any header literally named the same thing.
    return {**norm, **stat_cols, "_label": label_col}


# Columns the sync fills itself; an existing tab missing any of them gets
# them inserted (see missing_auto_columns). The required columns are never
# added automatically.
AUTO_COLUMNS = ("Kingdom", "Current_Tag", "Rank", "TC_Level", "Power", "Growth %", "vs Alliance", "7d Growth %")


# The status line: one cell in the header row, right after the last real
# column (the header row is frozen and never moves when rows are re-sorted). Rewritten by every real sync. The member count is a live
# formula over the member slots; the rest is what that sync found.
STATUS_PREFIX = "📊"
MEMBER_SLOTS = DEPARTED_SECTION_ROW - 2   # rows 2..101


def split_status(header: List[str]) -> Tuple[List[str], Optional[int]]:
    """(the real header -- without the status cell and any blank column(s)
    before it -- and the status cell's 0-based column, or None)."""
    idx = next((i for i, h in enumerate(header) if str(h).strip().startswith(STATUS_PREFIX)), None)
    cols = list(header[:idx] if idx is not None else header)
    while cols and not str(cols[-1]).strip():
        cols.pop()
    return cols, idx


def _status_body(result: "SyncResult", synced_at: str) -> str:
    """Everything after "📊 98/100 members · "."""
    parts = [f"🟩 {result.matched} matched", f"🟨 {result.mismatched} new position",
             f"⬜ {result.position_unknown} inactive", f"🟪 {result.not_in_alliance} left"]
    if result.stalled:
        parts.insert(2, f"💤 {result.stalled} no growth")
    if result.new_members_added:
        parts.append(f"➕ {result.new_members_added} new")
    if result.skipped_not_found:
        parts.append(f"⚠️ {result.skipped_not_found} not found")
    if result.skipped_no_player_id:
        parts.append(f"⏭️ {result.skipped_no_player_id} without Player ID")
    parts.append(f"synced {synced_at}")
    return " · ".join(parts)


def status_formula(result: "SyncResult", pid_letter: str, synced_at: str) -> str:
    """The status cell's formula, e.g. 📊 98/100 members · 🟩 90 matched · …"""
    text = _status_body(result, synced_at).replace('"', '""')
    slots = f"{pid_letter}2:{pid_letter}{DEPARTED_SECTION_ROW - 1}"
    return f'="{STATUS_PREFIX} "&COUNTA({slots})&"/{MEMBER_SLOTS} members · {text}"'


def status_width_px(result: "SyncResult", synced_at: str) -> int:
    """Column width that fits the status line: Google's auto-fit
    under-measures bold text and emoji and cut off the "synced" time.
    Measured like the Analytics tab's columns, emoji counted ~1.35em,
    with the widest member count (100/100) and room for the filter button."""
    from services.sheet_analytics import CELL_PADDING_PX, _FONT_PX, _text_px   # (imports this module)

    text = f"{STATUS_PREFIX} {MEMBER_SLOTS}/{MEMBER_SLOTS} members · {_status_body(result, synced_at)}"
    emoji = sum(1 for ch in text if ord(ch) >= 0x2190 and ch != "️")
    plain = "".join(ch for ch in text if ord(ch) < 0x2190)
    return int(_text_px(plain, bold=True) + emoji * 1.35 * _FONT_PX + CELL_PADDING_PX + FILTER_BUTTON_PX + 12)


def _synced_at() -> str:
    now = now_local()
    return f"{now:%b} {now.day} {now:%H:%M %Z}".strip()


def _column_present(norm: set, name: str) -> bool:
    """Whether a header set already has `name`, under any accepted alias."""
    key = name.strip().lower()
    for aliases in {**STAT_COLUMN_ALIASES, **GROWTH_COLUMN_ALIASES}.values():
        if key in aliases:
            return any(a in norm for a in aliases)
    return key in norm


def header_renames(header: List[str]) -> List[Tuple[int, str, str]]:
    """(column, old name, new name) for every header still using a long name."""
    out = []
    for i, h in enumerate(header):
        new = HEADER_RENAMES.get(h.strip().lower())
        if new:
            out.append((i, h.strip(), new))
    return out


def _rename_headers(ws, renames: List[Tuple[int, str, str]]) -> None:
    ws.batch_update([{"range": f"{_col_letter(i)}1", "values": [[new]]} for i, _old, new in renames],
                    value_input_option="RAW")


def missing_auto_columns(header: List[str]) -> List[str]:
    norm = {h.strip().lower() for h in header}
    return [c for c in AUTO_COLUMNS if not _column_present(norm, c)]


def _add_columns(ws, header: List[str], missing: List[str]) -> None:
    """Insert each missing column where it sits on a new tab -- right after
    the nearest DEFAULT_HEADER column that comes before it (else at the
    end) -- and write its header. Whole columns are inserted, so columns to
    the right (and formulas pointing at them) shift over intact."""
    working = list(header)
    requests: List[Dict[str, Any]] = []
    headers_to_write: List[Tuple[int, str]] = []
    for name in missing:
        pos = DEFAULT_HEADER.index(name)
        norm = [h.strip().lower() for h in working]
        index = len(working)
        for prev in reversed(DEFAULT_HEADER[:pos]):
            hits = [i for i, h in enumerate(norm) if _column_present({h}, prev)]
            if hits:
                index = hits[0] + 1
                break
        requests.append({"insertDimension": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1},
            "inheritFromBefore": index > 0,
        }})
        working.insert(index, name)
        headers_to_write = [(i + (1 if i >= index else 0), n) for i, n in headers_to_write] + [(index, name)]
    ws.client.batch_update(ws.spreadsheet_id, {"requests": requests})
    ws.batch_update([{"range": f"{_col_letter(i)}1", "values": [[n]]} for i, n in headers_to_write],
                    value_input_option="RAW")


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


def _growth_values(g: Any) -> Dict[str, Any]:
    """Sheet values for a power_growth.Growth: fractions / multiples as
    numbers (formatted as % / "x" by GROWTH_NUMBER_FORMATS), "" when there's
    no baseline yet so a stale value doesn't linger."""
    def num(v: Optional[float], digits: int) -> Any:
        return round(v, digits) if v is not None else ""
    return {"growth_pct": num(g.growth_pct, 4), "vs_alliance": num(g.vs_alliance, 2), "week_pct": num(g.week_pct, 4)}


def _same_cell(sheet_value: str, value: str) -> bool:
    # Power is displayed with separators ("66,331,112"); compare digits only.
    return sheet_value.replace(",", "") == value.replace(",", "")


def row_player_ids(data_rows: List[List[str]], col: Dict[str, int]) -> List[int]:
    return [pid for pid in (_parse_int_cell(r, col["player id"]) for r in data_rows) if pid is not None]


def _misplaced_member_row(row: List[str], pid_idx: int, known_ids: set) -> bool:
    """A member's row written shifted to the right -- Sheets' "append to
    table" once put new members' data from column L: nothing in the Player
    ID column, but elsewhere a kingdom number directly followed by a Player
    ID -- one this tab or roster knows, or (for a member no longer on the
    roster) an 8-10 digit ID followed by a name, the Kingdom / Player ID /
    Original_Name order of a member row. A note that mentions an ID never
    has that shape."""
    cells = [str(c).strip() for c in row]
    for i in range(len(cells) - 1):
        if i + 1 == pid_idx:
            continue
        k, p = cells[i], cells[i + 1]
        if not (k.isdigit() and len(k) <= 5 and p.isdigit()):
            continue
        name_follows = i + 2 < len(cells) and bool(cells[i + 2]) and not cells[i + 2].isdigit()
        if int(p) in known_ids or (8 <= len(p) <= 10 and name_follows):
            return True
    return False


def plan_sync(
    header: List[str],
    data_rows: List[List[str]],
    abbr: str,
    roster: Dict[int, Dict[str, Any]],
    players: Dict[int, Optional[Dict[str, Any]]],
    moved_to: Optional[Dict[int, Dict[str, Any]]] = None,
    growth: Optional[Dict[int, Any]] = None,
) -> SyncPlan:
    """Pure decision logic. `roster` maps FID -> roster member for the
    target alliance; `players` maps FID -> MightPulse `player` object (None
    = unknown to MightPulse). `moved_to` maps FID -> {"abbr", "member"} for
    players a *fresher* roster of another tracked alliance also lists (see
    superseded_members): they count as having left this one."""
    moved_to = moved_to or {}
    growth = growth or {}
    col = resolve_columns(header)
    result = SyncResult()
    result.members = sum(1 for fid in roster if fid not in moved_to)
    value_updates: List[Dict[str, Any]] = []
    row_colors: List[Tuple[int, str]] = []
    flag_cells: List[Tuple[int, str]] = []
    row_ranks: Dict[int, int] = {}
    new_row_ranks: List[int] = []
    inactive_rows: List[int] = []
    stalled_rows: List[int] = []
    clear_rows: List[int] = []
    # Every Player ID this tab or roster knows -- to recognise a member's row
    # that got written shifted to the right (no Player ID in its column).
    known_ids = set(roster) | set(moved_to) | {p for p in (_parse_int_cell(r, col["player id"]) for r in data_rows) if p}
    departed_rows: List[int] = []
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
            if _misplaced_member_row(row, col["player id"], known_ids):
                result.misplaced_cleared += 1
                clear_rows.append(row_num)
                continue
            # Only rows someone put something in: empty slots in the member
            # list (which this sync may fill with new members) aren't problems.
            if any(str(c).strip() for c in row):
                result.skipped_no_player_id += 1
            continue
        seen.add(pid)

        moved = moved_to.get(pid)
        member = None if moved else roster.get(pid)
        player = players.get(pid)
        if member is None and player is None and not moved:
            result.skipped_not_found += 1
            put("notes", row_num, NOT_FOUND_NOTE)
            departed_rows.append(row_num)
            continue

        # Roster nick is fresher than a possibly day-old player lookup.
        roster_entry = member or (moved or {}).get("member") or {}
        nick = roster_entry.get("nick_name") or (player or {}).get("nick_name")
        sheet_label = row[col["_label"]].strip() if col["_label"] < len(row) else ""
        if nick and nick != sheet_label:
            result.labels_updated += 1
            put("_label", row_num, nick)

        # Kingdom is live, like the name: a transfer shows where they went.
        kid = _live_kingdom(roster_entry or None, player)
        if kid and "kingdom" in col and cell(row, "kingdom") != kid:
            if cell(row, "kingdom"):
                result.kingdoms_changed += 1
            put("kingdom", row_num, kid)

        # Rank / TC / power: live, only written when they actually changed.
        # A player a fresher roster claims gets them from that roster.
        if member is not None:
            row_ranks[row_num] = rank_sort_key(member.get("alliance_rank_label"))
        for key, value in _live_stats(roster_entry or None, player).items():
            if key in col and not _same_cell(cell(row, key), value):
                put(key, row_num, value)

        # Growth since the checkpoint / over the week (members only): live,
        # rewritten every sync; the vs Alliance cell turns red when flagged.
        g = growth.get(pid) if member is not None else None
        if g is not None:
            for key, value in _growth_values(g).items():
                if key in col:
                    put(key, row_num, value)
            if g.flagged:
                result.flagged_nicks.append(nick or str(pid))
                if "vs_alliance" in col:
                    flag_cells.append((row_num, _col_letter(col["vs_alliance"])))

        # Tag is compared against the recorded Current_Tag (like x/y), and
        # the live one surfaces in o_tag only when they differ.
        if member is not None:
            live_tag = abbr
        elif moved:
            live_tag = moved["abbr"]
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
            departed_rows.append(row_num)
            continue

        cur_x, cur_y = (player or {}).get("x"), (player or {}).get("y")
        if cur_x is None or cur_y is None:
            result.position_unknown += 1
            row_colors.append((row_num, INACTIVE_COLOR))
            inactive_rows.append(row_num)
            continue

        sheet_x = _parse_int_cell(row, col["x"])
        sheet_y = _parse_int_cell(row, col["y"])
        if sheet_x == cur_x and sheet_y == cur_y:
            result.matched += 1
            put("observed_x", row_num, "")
            put("observed_y", row_num, "")
            color = MATCH_COLOR
        else:
            result.mismatched += 1
            put("observed_x", row_num, cur_x)
            put("observed_y", row_num, cur_y)
            color = MISMATCH_COLOR
        # Trending inactive: own color (o_x / o_y above still tell whether
        # they moved) and moved down below the active members.
        if g is not None and g.stalled:
            result.stalled += 1
            color = STALL_COLOR
            stalled_rows.append(row_num)
        row_colors.append((row_num, color))

    new_rows: List[List[Optional[str]]] = []
    new_row_colors: List[Optional[str]] = []
    for fid, member in roster.items():
        if fid in seen or fid in moved_to:   # already on the tab / really in another alliance now
            continue
        player = players.get(fid) or {}
        nick = member.get("nick_name") or player.get("nick_name") or ""
        # None = skip the cell (JSON null), so appending never blanks out a
        # formula column or anything a human added.
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
        if fid in growth:
            for key, value in _growth_values(growth[fid]).items():
                if key in col and value != "":
                    new_row[col[key]] = value
        if player.get("x") is not None and player.get("y") is not None:
            # Seed x/y with where they are now, so they start out matched
            # and a later move flags gold.
            new_row[col["x"]] = str(player["x"])
            new_row[col["y"]] = str(player["y"])
            stalled = fid in growth and growth[fid].stalled
            if stalled:
                result.stalled += 1
            new_row_colors.append(STALL_COLOR if stalled else MATCH_COLOR)
        else:
            new_row_colors.append(INACTIVE_COLOR)
            result.position_unknown += 1
        new_rows.append(new_row)
        new_row_ranks.append(rank_sort_key(member.get("alliance_rank_label")))
        result.new_members_added += 1
        result.new_member_nicks.append(nick or f"fid={fid}")

    # Row color runs A..o_y -- and past it over KSHive's p_x / p_y when they
    # sit there (their spot before they moved next to x / y). Otherwise
    # those cells keep whatever color they had when inserted -- a returning
    # member's planned cells stayed purple.
    highlight_end = _col_letter(max(col[k] for k in ("observed_x", "observed_y", "p_x", "p_y", "planned_x", "planned_y") if k in col))
    power_col = _col_letter(col["power"]) if "power" in col else None
    column_formats = [(_col_letter(col[k]), f) for k, f in GROWTH_NUMBER_FORMATS.items() if k in col]
    return SyncPlan(result, value_updates, row_colors, new_rows, new_row_colors, highlight_end, power_col,
                    inactive_rows, departed_rows, column_formats, flag_cells, row_ranks, new_row_ranks,
                    stalled_rows, clear_rows)


def roster_key(kid: str, abbr: str) -> Tuple[str, str]:
    """Cache key for an alliance roster within one run."""
    return (str(kid), abbr)


async def prefetch_rosters(
    client: MightPulseClient,
    targets: List[Dict[str, str]],
    cache: Dict[Tuple[str, str], Optional[Dict[str, Any]]],
) -> None:
    """Fetch every tracked alliance's roster into `cache` (one request each,
    skipping ones already there) so superseded_members can see all of them,
    even when only one tab is being synced. Failures are left for the tab
    that needs that roster to report."""
    for t in targets:
        key = roster_key(t["kid"], t["tag"])
        if key in cache:
            continue
        try:
            cache[key] = await client.get_alliance_roster(t["kid"], t["tag"])
        except MightPulseRateLimited:
            return
        except MightPulseError:
            continue


def superseded_members(
    rosters: Dict[Tuple[str, str], Optional[Dict[str, Any]]]
) -> Dict[Tuple[str, str], Dict[int, Dict[str, Any]]]:
    """Players listed on more than one tracked roster at once -- MightPulse
    caches each alliance's roster separately (up to ~an hour), so right
    after someone switches alliance the old roster can still list them.
    The most recently refreshed roster (`cached_at`) wins; for every other
    roster listing them this returns {roster key: {fid: {"abbr", "member"}}}
    naming where they really are. Equal timestamps are left alone."""
    claims: Dict[int, List[Tuple[float, Tuple[str, str], str, Dict[str, Any]]]] = {}
    for key, data in rosters.items():
        if not data or not data.get("alliance"):
            continue
        abbr = data["alliance"].get("abbr") or key[1]
        ts = float(data.get("cached_at") or 0)
        for m in data.get("members") or []:
            fid = m.get("fid") or m.get("governor_id")
            if fid:
                claims.setdefault(int(fid), []).append((ts, key, abbr, m))

    result: Dict[Tuple[str, str], Dict[int, Dict[str, Any]]] = {}
    for fid, listed in claims.items():
        if len(listed) < 2:
            continue
        newest = max(listed, key=lambda c: c[0])
        for ts, key, _abbr, _m in listed:
            if ts < newest[0]:
                result.setdefault(key, {})[fid] = {"abbr": newest[2], "member": newest[3]}
    return result


def sheet_url(gid: Optional[int] = None) -> str:
    """The roster spreadsheet, or one tab of it (gid = the tab's sheet id)."""
    base = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit"
    return f"{base}#gid={gid}" if gid is not None else base


def sheet_link(text: str, gid: Optional[int] = None) -> str:
    """A Discord link to the sheet / a tab; the <> keeps Discord from adding
    a preview card under the message."""
    return f"[{text}](<{sheet_url(gid)}>)"


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


def _init_header(ws, header: List[str]) -> None:
    # Whole tab (every row/column, so appended rows match): Arial 11 instead
    # of Sheets' Arial 10 default. Field mask touches only font family/size.
    ws.client.batch_update(ws.spreadsheet_id, {"requests": [{"repeatCell": {
        "range": {"sheetId": ws.id},
        "cell": {"userEnteredFormat": {"textFormat": dict(TAB_FONT)}},
        "fields": "userEnteredFormat.textFormat(fontFamily,fontSize)",
    }}]})
    ws.update(values=[list(header)], range_name="A1", raw=True)
    ws.freeze(rows=1)
    # ws.format replaces the whole textFormat, so the font must ride along
    # with bold or the header would fall back to the default font.
    ws.format(f"A1:{_col_letter(len(header) - 1)}1", {"textFormat": {"bold": True, **TAB_FONT}})
    # Whole-sheet basic filter (no range = every row, incl. ones appended
    # later), so the header gets filter/sort dropdowns like the original tab.
    ws.set_basic_filter()


def _player_id_column(all_values: List[List[str]], pid_idx: int) -> List[str]:
    return [(r[pid_idx].strip() if pid_idx < len(r) else "") for r in all_values[1:]]


def _write_plan(ws, plan: SyncPlan, header: List[str], fresh: bool,
                pid_idx: int, expected_ids: List[str], old_status_col: Optional[int] = None) -> None:
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
    inactive = list(plan.inactive_rows)
    stalled = list(plan.stalled_rows)
    departed = set(plan.departed_rows)
    # The member list ends at the last member's row -- not the last row with
    # any value (a number-only column like =SEQUENCE(100) fills rows with
    # no member in them), and not an ex-member parked below the list.
    if plan.clear_rows:
        # Empty the misplaced copies -- values and row color -- across the
        # whole row; the member is synced from their real row (or re-added).
        last_col = _col_letter(max(ws.col_count, len(header)) - 1)
        ws.batch_clear([f"A{r}:{last_col}{r}" for r in plan.clear_rows])
        ws.client.batch_update(ws.spreadsheet_id, {"requests": [
            {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": r - 1, "endRowIndex": r},
                            "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat.backgroundColor"}}
            for r in plan.clear_rows]})
        current = [([""] * len(r) if i + 1 in plan.clear_rows else r) for i, r in enumerate(current)]
    last_member_row = _last_member_row(current, pid_idx, exclude=departed)
    if plan.new_rows:
        new_at = _place_new_rows(ws, plan.new_rows, current, last_member_row)
        colors += [(new_at[i], c) for i, c in enumerate(plan.new_row_colors) if c]
        inactive += [new_at[i] for i, c in enumerate(plan.new_row_colors) if c == INACTIVE_COLOR]
        stalled += [new_at[i] for i, c in enumerate(plan.new_row_colors) if c == STALL_COLOR]

    formats = [
        {"range": f"A{row_num}:{plan.highlight_end}{row_num}",
         "format": {"backgroundColor": _hex_to_rgb_float(color)}}
        for row_num, color in colors
    ]
    if plan.power_col:
        # Every run, not just on new tabs, so a hand-added Power column gets
        # thousands separators too. Only the number format field is touched.
        formats.append({"range": f"{plan.power_col}2:{plan.power_col}", "format": POWER_NUMBER_FORMAT})
    for letter, number_format in plan.column_formats:
        formats.append({"range": f"{letter}2:{letter}", "format": number_format})
    # After the row colors, so a flagged vs Alliance cell stays red.
    formats += [{"range": f"{letter}{row_num}", "format": {"backgroundColor": _hex_to_rgb_float(FLAG_COLOR)}}
                for row_num, letter in plan.flag_cells]
    if formats:
        ws.batch_format(formats)

    # Row order (members, then ex-members from row 102), then column
    # alignment + auto-fit -- one request, every run so existing tabs match.
    pid_rows = [i for i, row in enumerate(current[1:], start=2) if pid_idx < len(row) and row[pid_idx].strip()]
    member_rows = [r for r in pid_rows if r not in departed]
    member_rows += new_at if plan.new_rows else []
    ranks = dict(plan.row_ranks)
    if plan.new_rows:
        ranks.update({new_at[i]: rk for i, rk in enumerate(plan.new_row_ranks)})

    def by_rank(rows: List[int]) -> List[int]:
        # Leader, R4, R3, R2, R1; ties keep their current order (row number).
        return sorted(set(rows), key=lambda r: (ranks.get(r, UNKNOWN_RANK), r))

    # Active members by rank, then trending-inactive (no growth), then
    # inactive (no map position) -- each group by rank.
    held_back = set(inactive) | set(stalled)
    active = by_rank([r for r in member_rows if r not in held_back])
    inactive = by_rank([r for r in stalled if r not in set(inactive)]) + by_rank(inactive)
    moves, rows_needed = _arrange_rows(ws.id, active, inactive, sorted(departed))
    if rows_needed > ws.row_count:
        ws.add_rows(rows_needed - ws.row_count)
    # Status line: header row, right after the last real column.
    status_col = len(header)
    if status_col + 1 > ws.col_count:
        ws.add_cols(status_col + 1 - ws.col_count)
    synced_at = _synced_at()
    status = [{"range": f"{_col_letter(status_col)}1",
               "values": [[status_formula(plan.result, _col_letter(pid_idx), synced_at)]]}]
    if old_status_col is not None and old_status_col != status_col:
        status.append({"range": f"{_col_letter(old_status_col)}1", "values": [[""]]})
    ws.batch_update(status, value_input_option="USER_ENTERED")

    # Explicit width (auto-fit cut the line off); set after the auto-fit.
    fit_status = {"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": status_col, "endIndex": status_col + 1},
        "properties": {"pixelSize": status_width_px(plan.result, synced_at)}, "fields": "pixelSize"}}
    ws.client.batch_update(ws.spreadsheet_id, {"requests": moves + _layout_requests(ws.id, header) + [fit_status]})
    _pad_for_filter_buttons(ws, list(range(len(header))))


def _last_member_row(values: List[List[str]], pid_idx: int, exclude: Optional[set] = None) -> int:
    """1-based row of the last row with a Player ID, ignoring `exclude`d
    rows (ex-members); 1 = header only."""
    last = 1
    for i, row in enumerate(values[1:], start=2):
        if pid_idx < len(row) and row[pid_idx].strip() and i not in (exclude or ()):
            last = i
    return last


def _place_new_rows(ws, new_rows: List[List[Optional[str]]], current: List[List[str]],
                    last_member_row: int) -> List[int]:
    """Write each new member into the next empty row after the last member
    (skipping any row that has something in it -- someone's note, say) and
    return the row each one went to, in order. Always written at an explicit
    column-A range: Sheets' "append to table" guessed where the table starts
    and once put a member's data from column L. Cells we have no value for
    are sent as null (skipped), so formula columns are untouched. Rows are
    put in rank order afterwards (_arrange_rows)."""
    rows: List[int] = []
    r = last_member_row + 1
    for _ in new_rows:
        while r - 1 < len(current) and any(str(cell).strip() for cell in current[r - 1]):
            r += 1
        rows.append(r)
        r += 1
    if rows and rows[-1] > ws.row_count:
        ws.add_rows(rows[-1] - ws.row_count)
    ws.batch_update([{"range": f"A{row}", "values": [values]} for row, values in zip(rows, new_rows)],
                    value_input_option="USER_ENTERED")
    return rows


def _pad_for_filter_buttons(ws, cols: List[int]) -> None:
    """Google's auto-fit ignores the filter dropdown in each header cell,
    so the end of the header (e.g. the "x" of o_x) hides behind it.
    Read back the fitted widths and widen each column by the button."""
    meta = ws.client.fetch_sheet_metadata(
        ws.spreadsheet_id, params={"fields": "sheets(properties(sheetId),data(columnMetadata(pixelSize)))"}
    )
    sheet = next((s for s in meta.get("sheets", []) if s.get("properties", {}).get("sheetId") == ws.id), None)
    columns = ((sheet or {}).get("data") or [{}])[0].get("columnMetadata", [])
    requests = [
        {"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
            "properties": {"pixelSize": columns[c]["pixelSize"] + FILTER_BUTTON_PX},
            "fields": "pixelSize"}}
        for c in cols if c < len(columns) and columns[c].get("pixelSize")
    ]
    if requests:
        ws.client.batch_update(ws.spreadsheet_id, {"requests": requests})


def _arrange_rows(
    sheet_id: int, active_rows: List[int], inactive_rows: List[int], departed_rows: List[int]
) -> Tuple[List[Dict[str, Any]], int]:
    """moveDimension requests laying the tab out as:

      row 2 on          active members (current order), then inactive ones,
                        no gaps -- with 20 members an inactive one is row 21
      row 102 on        ex-members (left the alliance, or not found), below
                        the 100 member slots -- or right after the members
                        if there are ever more than 100

    Rows are 1-indexed. Whole rows move (values, hand-kept columns and colors
    together). Rows without a Player ID end up in the space between the two
    blocks -- including number-only rows from something like =SEQUENCE(100).
    Returns (requests, rows the grid needs); no requests when the tab is
    already in order, so a settled tab costs nothing."""
    # Callers pass rows already in the order they want (by rank); keep it.
    members = list(dict.fromkeys(list(active_rows) + list(inactive_rows)))
    departed = sorted(set(departed_rows) - set(members))
    departed_start = max(DEPARTED_SECTION_ROW, len(members) + 2) - 1   # 0-based
    in_order = (members == list(range(2, 2 + len(members)))
                and departed == list(range(departed_start + 1, departed_start + 1 + len(departed))))
    if in_order or not (members or departed):
        return [], 0

    def move(start: int, end: int, destination: int) -> Dict[str, Any]:
        # Sheets' destinationIndex is in pre-removal coordinates.
        return {"moveDimension": {
            "source": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": start, "endIndex": end},
            "destinationIndex": destination,
        }}

    # 1) Pull members, then ex-members, into one tight block from row 2.
    #    Every move is upward (everything above is already placed), so no
    #    move disturbs a row placed earlier.
    order = list(range(1, max(members + departed) + 1))   # 0-based position -> original 1-based row
    requests = []
    for target, row in enumerate(members + departed, start=1):
        current = order.index(row)
        if current != target:
            requests.append(move(current, current + 1, target))
            order.insert(target, order.pop(current))

    # 2) Slide the ex-members as one block down to row 102 (below the 100
    #    member slots), leaving the rows without a Player ID in between.
    block = 1 + len(members)
    if departed and departed_start > block:
        requests.append(move(block, block + len(departed), departed_start + len(departed)))
    return requests, departed_start + len(departed) if departed else len(order)


def _layout_requests(sheet_id: int, header: List[str]) -> List[Dict[str, Any]]:
    """Whole-column horizontal alignment for the tag / rank / TC columns,
    then fit every column to its contents (last, so it sees the final
    values)."""
    col = resolve_columns(header)
    requests: List[Dict[str, Any]] = []
    for key, align in COLUMN_ALIGNMENT.items():
        if key in col:
            c = col[key]
            requests.append({"repeatCell": {
                # No row bounds = the whole column, header and future rows included.
                "range": {"sheetId": sheet_id, "startColumnIndex": c, "endColumnIndex": c + 1},
                "cell": {"userEnteredFormat": {"horizontalAlignment": align}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }})
    requests.append({"autoResizeDimensions": {"dimensions": {
        "sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(header)}}})
    return requests


async def sync_alliance_sheet(
    client: MightPulseClient,
    kid: str,
    abbr: str,
    tab: str,
    dry_run: bool = False,
    player_cache: Optional[Dict[int, Optional[Dict[str, Any]]]] = None,
    roster_cache: Optional[Dict[Tuple[str, str], Optional[Dict[str, Any]]]] = None,
    power_ctx: Optional[PowerContext] = None,
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
    status_col = None
    if fresh:
        header, data_rows = list(DEFAULT_HEADER), []
    else:
        (header, status_col), data_rows = split_status(all_values[0]), all_values[1:]
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

    # Existing tab missing some of the bot-filled columns: insert them (real
    # runs only, and only now that MightPulse has answered), then re-read so
    # every planned cell address matches the new layout.
    # Long header names (o_x, planned_x, ...) -> short ones, in place.
    renames = [] if fresh else header_renames(header)
    if renames and not dry_run:
        await asyncio.to_thread(_rename_headers, ws, renames)
        header = list(header)
        for i, _old, new in renames:
            header[i] = new

    columns_added = [] if fresh else missing_auto_columns(header)
    if columns_added and not dry_run:
        await asyncio.to_thread(_add_columns, ws, header, columns_added)
        all_values = await asyncio.to_thread(ws.get_all_values)
        (header, status_col), data_rows = split_status(all_values[0]), all_values[1:]
        col = resolve_columns(header)

    # Members a fresher roster of another tracked alliance also lists have
    # really moved there (needs the other rosters in roster_cache -- the
    # /sheet commands prefetch every tracked alliance for exactly this).
    moved_to = superseded_members(roster_cache).get(key, {}) if roster_cache else {}
    # Power growth vs this alliance's median, over current members only.
    growth = None
    if power_ctx is not None:
        growth, _median = alliance_growth([m for f, m in roster.items() if f not in moved_to], power_ctx)
    plan = plan_sync(header, data_rows, roster_abbr, roster, players, moved_to, growth)
    plan.result.dry_run = dry_run
    plan.result.columns_added = columns_added
    plan.result.headers_renamed = [f"{old} → {new}" for _i, old, new in renames]
    if fresh:
        plan.result.tab_setup = "created" if ws is None else "initialized"
    if not dry_run:
        if ws is None:
            ws = await asyncio.to_thread(_create_tab, sh, tab)
        pid_idx = col["player id"]
        await asyncio.to_thread(
            _write_plan, ws, plan, header, fresh, pid_idx, _player_id_column(all_values, pid_idx), status_col
        )
    if ws is not None:
        plan.result.tab_gid = ws.id
    return plan.result
