"""The `Analytics` tab: a per-alliance comparison across every /sheet target,
rewritten after each real /sheet sync (and by /sheet analytics).

Built from each alliance's live MightPulse roster (members + TC level +
power), one request per alliance -- reused from the sync when that tab was
just synced, so it usually costs nothing extra. Current members only, and
independent of each tab's column layout.

Layout: a summary table (one row per alliance) plus three charts that are
deleted and recreated on every update so their ranges always match the
table: a TG5 pie, a total-power bar, and a stacked bar of TC levels.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from services.mightpulse_client import MightPulseClient, MightPulseError, MightPulseRateLimited
from services.sheet_sync import (
    INACTIVE_COLOR,
    MATCH_COLOR,
    MISMATCH_COLOR,
    NOT_IN_ALLIANCE_COLOR,
    POWER_NUMBER_FORMAT,
    TAB_FONT,
    SheetSyncError,
    _hex_to_rgb_float,
    open_spreadsheet,
    roster_key,
    superseded_members,
    _col_letter,
)
from services.power_growth import (
    FLAG_COLOR,
    FLAG_MIN_GAIN,
    FLAG_MULTIPLE,
    HISTORY_DAYS,
    HISTORY_TAB,
    PowerContext,
    alliance_growth,
    now_local,
)

ANALYTICS_TAB = "Analytics"
LEGEND_TAB = "Legend"
RESERVED_TABS = (ANALYTICS_TAB, LEGEND_TAB, HISTORY_TAB)

# Legend rows are built from the same color constants the sync paints with,
# so the key can't drift from what's on the alliance tabs.
LEGEND_COLORS: List[Tuple[Optional[str], str, str, str]] = [
    (MATCH_COLOR, "Matched", "On the alliance roster, at the position recorded in x / y.", "Nothing to do."),
    (MISMATCH_COLOR, "Moved",
     "On the roster, but at a different position than x / y. The new position is in observed_x / observed_y.",
     "Check it, then update x / y by hand."),
    (NOT_IN_ALLIANCE_COLOR, "Left the alliance",
     "No longer on this alliance's roster. observed_tag shows where they went: a tag, \"none\" (no "
     "alliance), or \"left\" (MightPulse hasn't caught up yet). Moved below the 100 member slots "
     "(row 102 on).",
     "Follow up, or remove the row."),
    (INACTIVE_COLOR, "Inactive",
     "On the roster, but MightPulse has no map position for them — they haven't been playing. "
     "These rows are kept at the end of the member list.",
     "Follow up if needed."),
    (None, "No color", "notes says \"not found\": MightPulse doesn't know this Player ID. Moved below the "
     "member slots with the ex-members. Rows without a Player ID are skipped.", "Check the Player ID."),
    (FLAG_COLOR, "Growing fast (vs Alliance cell only)",
     f"Power growth since the checkpoint is at least {FLAG_MULTIPLE:g}× the alliance's median, and at least "
     f"{FLAG_MIN_GAIN / 1e6:g}M power gained — likely spending instead of saving for KvK.",
     "Check in with them."),
]
LEGEND_COLUMNS: List[Tuple[str, str]] = [
    ("Member #", "Automatic count 1…N from the top (renumbers after sorting)."),
    ("Kingdom, Current_Name, Rank, TC_Level, Power",
     "Live — refreshed from MightPulse on every sync. TC_Level shows True Gold tiers (55 = TG5, 54 = TG4.4)."),
    ("Original_Name", "The name when the member was first added. Never changed."),
    ("Current_Tag, x, y", "Recorded values — set when the member is added, then only changed by hand."),
    ("observed_tag, observed_x, observed_y",
     "The live value, filled in only while it differs from Current_Tag / x / y; cleared once they match again."),
    ("notes", "\"not found\" is written by the sync; otherwise the column is yours."),
    ("Growth %", "Power growth since the checkpoint (/sheet checkpoint, run right after KvK). Blank until "
     "there's a baseline for the player."),
    ("vs Alliance", "Growth % as a multiple of the alliance's median growth (1.0× = typical; the median counts "
     "as at least 1%). Red = growing much faster than everyone else."),
    ("7d Growth %", "Power growth over the last 7 days. Blank until a week of history exists."),
]
LEGEND_FOOTER = [
    "Tabs sync automatically once a day; run /sheet sync in Discord to update now.",
    "Members are listed by rank (Leader, R4, R3, R2, R1), inactive members after them, and ex-members from row 102.",
    f"The {ANALYTICS_TAB} tab compares alliances with at least 5 billion total power.",
    f"Power is recorded daily in the hidden '{HISTORY_TAB}' tab (last {HISTORY_DAYS} days) to measure growth.",
]
# Alliances below this total power are left off the table and charts (and
# noted under the table), so small alliances don't clutter the comparison.
MIN_TOTAL_POWER = 5_000_000_000

# TC buckets for the stacked bar, highest first. TG5 counts 55+ so a future
# TG6 isn't silently dropped.
TC_BUCKETS: List[Tuple[str, int, Optional[int]]] = [
    ("TG5", 55, None),
    ("TG4", 50, 54),
    ("TG3", 45, 49),
    ("TG2", 40, 44),
    ("TG1", 35, 39),
    ("Below TG1", 1, 34),
]
HEADER = (["Alliance", "Kingdom", "Members"] + [b[0] for b in TC_BUCKETS]
          + ["Total Power", "Avg Power", "Top Power", "Median Growth %"])
COL = {h: i for i, h in enumerate(HEADER)}
POWER_COLUMNS = ("Total Power", "Avg Power", "Top Power")

# "Fastest growers" table, one blank column to the right of the main table.
GROWERS_HEADER = ["Fastest growers", "Alliance", "Growth %", "Power gained"]
GROWERS_COL = len(HEADER) + 1
TOP_GROWERS = 10


@dataclass
class Grower:
    name: str
    alliance: str
    growth_pct: float
    gain: int
    flagged: bool

    def row(self) -> List[Any]:
        return [self.name, self.alliance, round(self.growth_pct, 4), self.gain]


@dataclass
class AllianceStats:
    label: str
    kid: str
    members: int
    buckets: Dict[str, int]
    total_power: int
    top_power: int
    median_growth: Optional[float] = None

    @property
    def avg_power(self) -> int:
        return round(self.total_power / self.members) if self.members else 0

    def row(self) -> List[Any]:
        return ([self.label, self.kid, self.members]
                + [self.buckets[b[0]] for b in TC_BUCKETS]
                + [self.total_power, self.avg_power, self.top_power]
                + [round(self.median_growth, 4) if self.median_growth is not None else ""])


def _bucket(level: Any) -> Optional[str]:
    try:
        n = int(level)
    except (TypeError, ValueError):
        return None
    for name, low, high in TC_BUCKETS:
        if n >= low and (high is None or n <= high):
            return name
    return None


def compute_stats(label: str, kid: str, roster: Dict[str, Any], exclude: Optional[set] = None) -> AllianceStats:
    """`exclude`: FIDs a fresher roster of another tracked alliance claims."""
    exclude = exclude or set()
    members = [m for m in roster.get("members") or []
               if int(m.get("fid") or m.get("governor_id") or 0) not in exclude]
    buckets = {b[0]: 0 for b in TC_BUCKETS}
    powers: List[int] = []
    for m in members:
        name = _bucket(m.get("town_center_level"))
        if name:
            buckets[name] += 1
        if m.get("power") is not None:
            powers.append(int(m["power"]))
    return AllianceStats(label, str(kid), len(members), buckets, sum(powers), max(powers, default=0))


def alliance_labels(targets: List[Dict[str, str]]) -> List[str]:
    """Tag as the chart label; add the kingdom only when two targets share
    a tag, so the categories stay distinct."""
    tags = [t["tag"].lower() for t in targets]
    return [t["tag"] if tags.count(t["tag"].lower()) == 1 else f"{t['tag']} ({t['kid']})" for t in targets]


# --- chart specs ----------------------------------------------------------

def _grid(sheet_id: int, col: int, rows: int) -> Dict[str, Any]:
    """Header row + `rows` data rows of one column."""
    return {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": rows + 1,
            "startColumnIndex": col, "endColumnIndex": col + 1}


CHART_W, CHART_H, WIDE_W, WIDE_H, CHART_GAP = 560, 340, 1140, 380, 20


def _anchor(sheet_id: int, row: int, x: int, y: int, width: int = CHART_W, height: int = CHART_H) -> Dict[str, Any]:
    """Every chart hangs off the same cell (column A of `row`) and is placed
    by pixel offset, so charts can't overlap however wide the columns are."""
    return {"overlayPosition": {"anchorCell": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": 0},
                                "offsetXPixels": x, "offsetYPixels": y,
                                "widthPixels": width, "heightPixels": height}}


def chart_requests(sheet_id: int, rows: int, first_chart_row: int, growers: int = 0) -> List[Dict[str, Any]]:
    domain = {"sourceRange": {"sources": [_grid(sheet_id, COL["Alliance"], rows)]}}

    def series(name: str) -> Dict[str, Any]:
        return {"sourceRange": {"sources": [_grid(sheet_id, COL[name], rows)]}}

    tg5_pie = {
        "title": "TG5 players by alliance",
        "pieChart": {
            "legendPosition": "RIGHT_LEGEND",
            # Pie ranges skip the header row (no headerCount on pies).
            "domain": {"sourceRange": {"sources": [{**_grid(sheet_id, COL["Alliance"], rows), "startRowIndex": 1}]}},
            "series": {"sourceRange": {"sources": [{**_grid(sheet_id, COL["TG5"], rows), "startRowIndex": 1}]}},
        },
    }
    power_bar = {
        "title": "Total power by alliance",
        "basicChart": {
            "chartType": "BAR",
            "legendPosition": "NO_LEGEND",
            "headerCount": 1,
            "axis": [{"position": "BOTTOM_AXIS", "title": "Total power"}],
            "domains": [{"domain": domain}],
            "series": [{"series": series("Total Power"), "targetAxis": "BOTTOM_AXIS"}],
        },
    }
    tc_stacked = {
        "title": "Town center levels by alliance",
        "basicChart": {
            "chartType": "BAR",
            "stackedType": "STACKED",
            "legendPosition": "BOTTOM_LEGEND",
            "headerCount": 1,
            "axis": [{"position": "BOTTOM_AXIS", "title": "Members"}],
            "domains": [{"domain": domain}],
            "series": [{"series": series(b[0]), "targetAxis": "BOTTOM_AXIS"} for b in TC_BUCKETS],
        },
    }
    requests = [
        # Row 1: pie | power bar. Row 2: TC stacked (full width). Row 3: growers.
        {"addChart": {"chart": {"spec": tg5_pie, "position": _anchor(sheet_id, first_chart_row, 0, 0)}}},
        {"addChart": {"chart": {"spec": power_bar,
                                "position": _anchor(sheet_id, first_chart_row, CHART_W + CHART_GAP, 0)}}},
        {"addChart": {"chart": {"spec": tc_stacked,
                                "position": _anchor(sheet_id, first_chart_row, 0, CHART_H + CHART_GAP, WIDE_W, WIDE_H)}}},
    ]
    if growers:
        def g_grid(offset: int) -> Dict[str, Any]:
            return _grid(sheet_id, GROWERS_COL + offset, growers)
        growers_bar = {
            "title": "Fastest growers since the checkpoint",
            "basicChart": {
                "chartType": "BAR",
                "legendPosition": "NO_LEGEND",
                "headerCount": 1,
                "axis": [{"position": "BOTTOM_AXIS", "title": "Growth %"}],
                "domains": [{"domain": {"sourceRange": {"sources": [g_grid(0)]}}}],
                "series": [{"series": {"sourceRange": {"sources": [g_grid(2)]}}, "targetAxis": "BOTTOM_AXIS"}],
            },
        }
        requests.append({"addChart": {"chart": {"spec": growers_bar,
                                                "position": _anchor(sheet_id, first_chart_row, 0,
                                                                    CHART_H + WIDE_H + 2 * CHART_GAP,
                                                                    WIDE_W, WIDE_H)}}})
    return requests


# --- column widths ----------------------------------------------------------

# Arial at 11pt (~14.7px) character widths, from Arial's advance widths
# (em fractions). Anything not listed uses an average.
_ARIAL_EM = {
    **{d: 0.556 for d in "0123456789"},
    " ": 0.278, ",": 0.278, ".": 0.278, ":": 0.278, ";": 0.278, "'": 0.191, "|": 0.26,
    "i": 0.222, "j": 0.222, "l": 0.222, "f": 0.278, "t": 0.278, "r": 0.333, "I": 0.278,
    "m": 0.833, "w": 0.722, "M": 0.833, "W": 0.944, "%": 0.889, "(": 0.333, ")": 0.333,
    "-": 0.333, "—": 1.0, "×": 0.584,
}
_FONT_PX = 11 * 96 / 72
CELL_PADDING_PX = 18


def _text_px(text: str, bold: bool = False) -> float:
    em = 0.0
    for ch in str(text):
        if ch in _ARIAL_EM:
            em += _ARIAL_EM[ch]
        elif ch.isupper():
            em += 0.667
        elif ch.islower():
            em += 0.5
        else:
            em += 0.6
    return em * _FONT_PX * (1.1 if bold else 1.0)


def _display(column: str, value: Any) -> str:
    """How Sheets will show a table value, for sizing its column."""
    if value in ("", None):
        return ""
    if column in POWER_COLUMNS or column == "Power gained":
        return f"{int(value):,}"
    if column in ("Median Growth %", "Growth %"):
        return f"{float(value) * 100:.1f}%"
    return str(value)


# --- sheet writes -----------------------------------------------------------

def _existing_chart_ids(sh, sheet_id: int) -> List[int]:
    meta = sh.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),charts(chartId))"})
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == sheet_id:
            return [c["chartId"] for c in sheet.get("charts", [])]
    return []


def _get_or_create(sh, title: str, index: int, rows: int, cols: int):
    import gspread

    try:
        return sh.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols, index=index)


def write_legend(sh) -> None:
    """(Re)write the Legend tab, kept second (right after Analytics)."""
    ws = _get_or_create(sh, LEGEND_TAB, index=1, rows=30, cols=3)
    color_rows = [[label, meaning, action] for _, label, meaning, action in LEGEND_COLORS]
    col_start = 1 + len(color_rows) + 2          # 0-based row of the "Column" header
    values = ([["Color", "Meaning", "What to do"]] + color_rows + [[]]
              + [["Column", "Meaning"]] + [[c, m] for c, m in LEGEND_COLUMNS] + [[]]
              + [[f] for f in LEGEND_FOOTER])
    ws.clear()
    ws.update(values=values, range_name="A1", raw=True)

    def cells(r0: int, r1: int, c0: int, c1: int) -> Dict[str, Any]:
        return {"sheetId": ws.id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    def fmt(rng: Dict[str, Any], cell_format: Dict[str, Any], fields: str) -> Dict[str, Any]:
        return {"repeatCell": {"range": rng, "cell": {"userEnteredFormat": cell_format}, "fields": fields}}

    requests: List[Dict[str, Any]] = [
        # Reset everything (old colors included), then Arial 11 + wrapped, top-aligned text.
        fmt({"sheetId": ws.id},
            {"textFormat": dict(TAB_FONT), "wrapStrategy": "WRAP", "verticalAlignment": "TOP"},
            "userEnteredFormat"),
        fmt(cells(0, 1, 0, 3), {"textFormat": {"bold": True, **TAB_FONT}}, "userEnteredFormat.textFormat"),
        fmt(cells(col_start, col_start + 1, 0, 2), {"textFormat": {"bold": True, **TAB_FONT}},
            "userEnteredFormat.textFormat"),
    ]
    for i, (color, *_rest) in enumerate(LEGEND_COLORS):
        if color:
            requests.append(fmt(cells(1 + i, 2 + i, 0, 1), {"backgroundColor": _hex_to_rgb_float(color)},
                                "userEnteredFormat.backgroundColor"))
    for c, px in enumerate((260, 560, 240)):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    requests.append({"updateSheetProperties": {"properties": {"sheetId": ws.id, "index": 1}, "fields": "index"}})
    sh.batch_update({"requests": requests})


def write_analytics(stats: List[AllianceStats], notes: List[str], growers: Optional[List[Grower]] = None,
                    growth_note: str = "") -> None:
    """Rewrite the Analytics tab (blocking): tables, formatting, charts."""
    growers = growers or []
    width = GROWERS_COL + len(GROWERS_HEADER)
    sh = open_spreadsheet()
    ws = _get_or_create(sh, ANALYTICS_TAB, index=0, rows=60, cols=width)
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)
    rows = len(stats)
    updated = now_local().strftime("%Y-%m-%d %H:%M %Z")
    footer = [[f"Updated {updated} — current alliance members, from MightPulse rosters"]]
    if growth_note:
        footer.append([growth_note])
    footer += [[n] for n in notes]

    ws.clear()
    ws.update(values=[HEADER] + [s.row() for s in stats], range_name="A1", raw=False)
    if growers:
        ws.update(values=[GROWERS_HEADER] + [g.row() for g in growers],
                  range_name=f"{_col_letter(GROWERS_COL)}1", raw=False)

    requests: List[Dict[str, Any]] = [
        # Reset the whole tab to Arial 11, then bold the header (same font).
        {"repeatCell": {"range": {"sheetId": ws.id},
                        "cell": {"userEnteredFormat": {"textFormat": dict(TAB_FONT)}},
                        "fields": "userEnteredFormat.textFormat(fontFamily,fontSize)"}},
        {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": width},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True, **TAB_FONT}}},
                        "fields": "userEnteredFormat.textFormat"}},
        # Clear last run's flag colors and alignment before re-applying (rows
        # shift when the number of alliances changes).
        {"repeatCell": {"range": {"sheetId": ws.id},
                        "cell": {"userEnteredFormat": {}},
                        "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment"}},
        {"updateSheetProperties": {"properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                                   "fields": "gridProperties.frozenRowCount"}},
        # Keep Analytics as the first tab (no-op if it already is).
        {"updateSheetProperties": {"properties": {"sheetId": ws.id, "index": 0}, "fields": "index"}},
    ]
    for name in POWER_COLUMNS:
        c = COL[name]
        requests.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": rows + 1,
                      "startColumnIndex": c, "endColumnIndex": c + 1},
            "cell": {"userEnteredFormat": POWER_NUMBER_FORMAT},
            "fields": "userEnteredFormat.numberFormat"}})
    percent = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}
    for c, number_format, n in ((COL["Median Growth %"], percent, rows),
                                (GROWERS_COL + 2, percent, len(growers)),
                                (GROWERS_COL + 3, POWER_NUMBER_FORMAT, len(growers))):
        if n:
            requests.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": n + 1,
                          "startColumnIndex": c, "endColumnIndex": c + 1},
                "cell": {"userEnteredFormat": number_format}, "fields": "userEnteredFormat.numberFormat"}})
    for i, g in enumerate(growers, start=1):
        if g.flagged:
            requests.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": i, "endRowIndex": i + 1,
                          "startColumnIndex": GROWERS_COL, "endColumnIndex": width},
                "cell": {"userEnteredFormat": {"backgroundColor": _hex_to_rgb_float(FLAG_COLOR)}},
                "fields": "userEnteredFormat.backgroundColor"}})
    # Alliance names right-aligned -- header + table rows only, so the
    # footer below stays left-aligned and can spill to the right.
    requests.append({"repeatCell": {
        "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": rows + 1,
                  "startColumnIndex": COL["Alliance"], "endColumnIndex": COL["Alliance"] + 1},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
        "fields": "userEnteredFormat.horizontalAlignment"}})
    requests += [{"deleteEmbeddedObject": {"objectId": cid}} for cid in _existing_chart_ids(sh, ws.id)]
    if rows:
        requests += chart_requests(ws.id, rows, first_chart_row=rows + 4 + len(footer), growers=len(growers))
    # Explicit widths from the text Sheets will display (bold headers, power
    # with separators, percentages). Google's auto-fit under-measures bold
    # text and formatted numbers, cutting off "Median Growth %" and big
    # power totals.
    columns: Dict[int, List[Tuple[str, bool]]] = {}
    for i, h in enumerate(HEADER):
        columns.setdefault(i, []).append((h, True))
    for st in stats:
        for i, v in enumerate(st.row()):
            columns[i].append((_display(HEADER[i], v), False))
    if growers:
        for j, h in enumerate(GROWERS_HEADER):
            columns.setdefault(GROWERS_COL + j, []).append((h, True))
        for g in growers:
            for j, v in enumerate(g.row()):
                columns[GROWERS_COL + j].append((_display(GROWERS_HEADER[j], v), False))
    for c, texts in columns.items():
        px = max(_text_px(t, bold) for t, bold in texts) + CELL_PADDING_PX
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": c, "endIndex": c + 1},
            "properties": {"pixelSize": int(px)}, "fields": "pixelSize"}})
    sh.batch_update({"requests": requests})
    # Footer only after the auto-fit above: fitting sizes a column to its
    # longest text, so the "Updated ..." line would otherwise stretch column
    # A. Written afterwards it just spills over the empty cells to its right.
    ws.update(values=footer, range_name=f"A{rows + 3}", raw=False)
    # Legend goes second, after Analytics has claimed index 0.
    write_legend(sh)


async def update_analytics(
    client: MightPulseClient,
    targets: List[Dict[str, str]],
    roster_cache: Optional[Dict[Tuple[str, str], Optional[Dict[str, Any]]]] = None,
    power_ctx: Optional[PowerContext] = None,
) -> str:
    """Refresh the Analytics tab for every target. Rosters already fetched
    this run come from `roster_cache`; the rest cost one request each. An
    alliance whose roster can't be fetched is left out (and noted) rather
    than failing the whole tab. Returns a one-line summary."""
    if not targets:
        raise SheetSyncError("No sheet targets configured.")

    cache = roster_cache if roster_cache is not None else {}
    stats: List[AllianceStats] = []
    notes: List[str] = []
    below_min = 0
    labels = alliance_labels(targets)
    failed: Dict[Tuple[str, str], str] = {}
    for target in targets:
        key = roster_key(target["kid"], target["tag"])
        try:
            if key not in cache:
                cache[key] = await client.get_alliance_roster(target["kid"], target["tag"])
        except MightPulseRateLimited as exc:
            raise SheetSyncError("MightPulse rate limit hit — Analytics not updated.") from exc
        except MightPulseError as exc:
            failed[key] = str(exc)

    # Someone listed on two tracked rosters (one of them stale) counts only
    # for the alliance whose roster is fresher.
    superseded = superseded_members(cache)
    growers: List[Grower] = []
    for target, label in zip(targets, labels):
        key = roster_key(target["kid"], target["tag"])
        if key in failed:
            notes.append(f"{label}: skipped ({failed[key]})")
            continue
        roster = cache.get(key)
        if not roster or not roster.get("alliance"):
            notes.append(f"{label}: skipped (alliance not found on MightPulse)")
            continue
        excluded = set(superseded.get(key, {}))
        s = compute_stats(label, target["kid"], roster, exclude=excluded)
        if s.total_power < MIN_TOTAL_POWER:
            below_min += 1
            notes.append(f"{label}: not shown (total power {s.total_power:,} is under "
                         f"{MIN_TOTAL_POWER / 1e9:g} billion)")
            continue
        if power_ctx is not None:
            members = [m for m in roster.get("members") or []
                       if int(m.get("fid") or m.get("governor_id") or 0) not in excluded]
            growth, s.median_growth = alliance_growth(members, power_ctx)
            names = {int(m.get("fid") or m.get("governor_id") or 0): m.get("nick_name") or "" for m in members}
            growers += [Grower(names.get(fid) or str(fid), label, g.growth_pct, g.gain or 0, g.flagged)
                        for fid, g in growth.items() if g.growth_pct is not None and g.gain]
        stats.append(s)

    growers = sorted(growers, key=lambda g: g.growth_pct, reverse=True)[:TOP_GROWERS]
    growth_note = ""
    if power_ctx is not None:
        if power_ctx.checkpoint_label:
            growth_note = f"Growth since the checkpoint ({power_ctx.checkpoint_label})"
        else:
            growth_note = (f"Growth since tracking began ({power_ctx.first_snapshot or 'today'}) — "
                           f"run /sheet checkpoint right after KvK to measure from there")
    await asyncio.to_thread(write_analytics, stats, notes, growers, growth_note)
    summary = f"📊 `{ANALYTICS_TAB}` tab updated {now_local().strftime('%H:%M %Z')} — {len(stats)} alliance(s)"
    if below_min:
        summary += f", {below_min} under {MIN_TOTAL_POWER / 1e9:g}B power not shown"
    if len(notes) > below_min:
        summary += f", {len(notes) - below_min} skipped"
    return summary
