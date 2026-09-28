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
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from services.mightpulse_client import MightPulseClient, MightPulseError, MightPulseRateLimited
from services.sheet_sync import (
    POWER_NUMBER_FORMAT,
    TAB_FONT,
    SheetSyncError,
    open_spreadsheet,
    roster_key,
)

ANALYTICS_TAB = "Analytics"
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
          + ["Total Power", "Avg Power", "Top Power"])
COL = {h: i for i, h in enumerate(HEADER)}
POWER_COLUMNS = ("Total Power", "Avg Power", "Top Power")


@dataclass
class AllianceStats:
    label: str
    kid: str
    members: int
    buckets: Dict[str, int]
    total_power: int
    top_power: int

    @property
    def avg_power(self) -> int:
        return round(self.total_power / self.members) if self.members else 0

    def row(self) -> List[Any]:
        return ([self.label, self.kid, self.members]
                + [self.buckets[b[0]] for b in TC_BUCKETS]
                + [self.total_power, self.avg_power, self.top_power])


def _bucket(level: Any) -> Optional[str]:
    try:
        n = int(level)
    except (TypeError, ValueError):
        return None
    for name, low, high in TC_BUCKETS:
        if n >= low and (high is None or n <= high):
            return name
    return None


def compute_stats(label: str, kid: str, roster: Dict[str, Any]) -> AllianceStats:
    members = roster.get("members") or []
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


def _anchor(sheet_id: int, row: int, col: int, width: int = 560, height: int = 340) -> Dict[str, Any]:
    return {"overlayPosition": {"anchorCell": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col},
                                "widthPixels": width, "heightPixels": height}}


def chart_requests(sheet_id: int, rows: int, first_chart_row: int) -> List[Dict[str, Any]]:
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
    return [
        {"addChart": {"chart": {"spec": tg5_pie, "position": _anchor(sheet_id, first_chart_row, 0)}}},
        {"addChart": {"chart": {"spec": power_bar, "position": _anchor(sheet_id, first_chart_row, 7)}}},
        {"addChart": {"chart": {"spec": tc_stacked, "position": _anchor(sheet_id, first_chart_row + 18, 0, 900, 380)}}},
    ]


# --- sheet writes -----------------------------------------------------------

def _get_or_create_tab(sh):
    import gspread

    try:
        return sh.worksheet(ANALYTICS_TAB)
    except gspread.exceptions.WorksheetNotFound:
        return sh.add_worksheet(title=ANALYTICS_TAB, rows=60, cols=len(HEADER), index=0)


def _existing_chart_ids(sh, sheet_id: int) -> List[int]:
    meta = sh.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),charts(chartId))"})
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == sheet_id:
            return [c["chartId"] for c in sheet.get("charts", [])]
    return []


def write_analytics(stats: List[AllianceStats], notes: List[str]) -> None:
    """Rewrite the Analytics tab (blocking): table, formatting, charts."""
    sh = open_spreadsheet()
    ws = _get_or_create_tab(sh)
    rows = len(stats)
    updated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    footer = [[f"Updated {updated} — current alliance members, from MightPulse rosters"]]
    footer += [[n] for n in notes]

    ws.clear()
    ws.update(values=[HEADER] + [s.row() for s in stats], range_name="A1", raw=False)
    ws.update(values=footer, range_name=f"A{rows + 3}", raw=False)

    requests: List[Dict[str, Any]] = [
        # Reset the whole tab to Arial 11, then bold the header (same font).
        {"repeatCell": {"range": {"sheetId": ws.id},
                        "cell": {"userEnteredFormat": {"textFormat": dict(TAB_FONT)}},
                        "fields": "userEnteredFormat.textFormat(fontFamily,fontSize)"}},
        {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                                  "startColumnIndex": 0, "endColumnIndex": len(HEADER)},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True, **TAB_FONT}}},
                        "fields": "userEnteredFormat.textFormat"}},
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
    requests += [{"deleteEmbeddedObject": {"objectId": cid}} for cid in _existing_chart_ids(sh, ws.id)]
    if rows:
        requests += chart_requests(ws.id, rows, first_chart_row=rows + 4 + len(notes))
    requests.append({"autoResizeDimensions": {"dimensions": {
        "sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(HEADER)}}})
    sh.batch_update({"requests": requests})


async def update_analytics(
    client: MightPulseClient,
    targets: List[Dict[str, str]],
    roster_cache: Optional[Dict[Tuple[str, str], Optional[Dict[str, Any]]]] = None,
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
    for target, label in zip(targets, alliance_labels(targets)):
        key = roster_key(target["kid"], target["tag"])
        try:
            if key not in cache:
                cache[key] = await client.get_alliance_roster(target["kid"], target["tag"])
        except MightPulseRateLimited as exc:
            raise SheetSyncError("MightPulse rate limit hit — Analytics not updated.") from exc
        except MightPulseError as exc:
            notes.append(f"{label}: skipped ({exc})")
            continue
        roster = cache[key]
        if not roster or not roster.get("alliance"):
            notes.append(f"{label}: skipped (alliance not found on MightPulse)")
            continue
        s = compute_stats(label, target["kid"], roster)
        if s.total_power < MIN_TOTAL_POWER:
            below_min += 1
            notes.append(f"{label}: not shown (total power {s.total_power:,} is under "
                         f"{MIN_TOTAL_POWER / 1e9:g} billion)")
            continue
        stats.append(s)

    await asyncio.to_thread(write_analytics, stats, notes)
    summary = f"📊 `{ANALYTICS_TAB}` tab updated — {len(stats)} alliance(s)"
    if below_min:
        summary += f", {below_min} under {MIN_TOTAL_POWER / 1e9:g}B power not shown"
    if len(notes) > below_min:
        summary += f", {len(notes) - below_min} skipped"
    return summary
