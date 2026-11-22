"""
title: Calendar Planner
author: homelab (own code, no external dependencies)
version: 1.0.0
description: Renders a calendar (month / week / day / agenda) inline in the chat from a list of events, marks double-bookings, shows proposed plan items as dashed blocks, and finds free time slots. Pair it with the Google Workspace tools (get_events / manage_event): read the calendar, find_free_slots, render the proposed plan, and create the events once the user agrees.
"""

import html
import json
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

PALETTE = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#dc2626"]
HOUR_PX = 44


# ---------------------------------------------------------------------------------------------- parsing
def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _parse_dt(value) -> Optional[tuple[datetime, bool]]:
    """-> (naive wall-clock datetime, is_date_only). Time zones are dropped on purpose: the calendar tools return
    times already in the calendar's own zone, and a planner view should show that wall-clock time."""
    if isinstance(value, dict):  # Google shape {"dateTime": ...} / {"date": ...}
        value = _first(value, "dateTime", "date")
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return datetime.combine(date.fromisoformat(s), time.min), True
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    return dt.replace(tzinfo=None), False


def _load_events(raw) -> list[dict]:
    """Accepts a JSON string (or list) of events; tolerant of key names so a small model can paste what it has."""
    if isinstance(raw, str):
        text = raw.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
        raw = json.loads(text) if text else []
    if isinstance(raw, dict):
        raw = _first(raw, "events", "items", "plan") or [raw]
    out = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            continue
        start = _parse_dt(_first(e, "start", "start_time", "from", "begin"))
        if not start:
            continue
        end = _parse_dt(_first(e, "end", "end_time", "to", "until"))
        all_day = start[1]
        s = start[0]
        if end:
            en = end[0]  # an all-day end is exclusive, as in Google Calendar (end = the day after the last day)
        else:
            en = s + (timedelta(days=1) if all_day else timedelta(hours=1))
        if en <= s:
            en = s + (timedelta(days=1) if all_day else timedelta(minutes=30))
        out.append({
            "id": i,
            "title": str(_first(e, "title", "summary", "name", "subject") or "(no title)"),
            "start": s, "end": en, "all_day": all_day or bool(e.get("all_day")),
            "location": str(_first(e, "location", "where") or ""),
            "calendar": str(_first(e, "calendar", "calendar_name") or ""),
            "proposed": bool(_first(e, "proposed", "tentative", "draft")),
        })
    return sorted(out, key=lambda x: (x["start"], x["end"]))


def _overlaps(a, b) -> bool:
    return a["start"] < b["end"] and b["start"] < a["end"]


def _conflicts(events) -> list[tuple[dict, dict]]:
    timed = [e for e in events if not e["all_day"]]
    return [(a, b) for i, a in enumerate(timed) for b in timed[i + 1:] if _overlaps(a, b)]


# ---------------------------------------------------------------------------------------------- html
def _color(ev, cal_colors) -> str:
    key = ev["calendar"] or "default"
    if key not in cal_colors:
        cal_colors[key] = PALETTE[len(cal_colors) % len(PALETTE)]
    return cal_colors[key]


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _fmt_t(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _chip(ev, color, clash) -> str:
    cls = "chip" + (" proposed" if ev["proposed"] else "") + (" clash" if clash else "")
    label = ev["title"] if ev["all_day"] else f"{_fmt_t(ev['start'])} {ev['title']}"
    tip = f"{ev['title']}\n{ev['start']:%a %d %b %H:%M}-{ev['end']:%H:%M}" + (f"\n{ev['location']}" if ev["location"] else "")
    return f'<div class="{cls}" style="--c:{color}" title="{_esc(tip)}">{_esc(label)}</div>'


def _days_of(ev):
    d = ev["start"].date()
    last = (ev["end"] - timedelta(seconds=1)).date() if ev["end"] > ev["start"] else d
    while d <= last:
        yield d
        d += timedelta(days=1)


def _lanes(day_events):
    """Assign side-by-side lanes to overlapping timed events -> list of (event, lane, lane_count)."""
    placed, group, group_end = [], [], None

    def flush():
        if not group:
            return
        n = max(l for _, l in group) + 1
        placed.extend((e, l, n) for e, l in group)

    lanes_end = []
    for e in day_events:
        if group_end is not None and e["start"] >= group_end:
            flush()
            group.clear()
            lanes_end = []
            group_end = None
        for li, end in enumerate(lanes_end):
            if e["start"] >= end:
                lanes_end[li] = e["end"]
                lane = li
                break
        else:
            lanes_end.append(e["end"])
            lane = len(lanes_end) - 1
        group.append((e, lane))
        group_end = e["end"] if group_end is None else max(group_end, e["end"])
    flush()
    return placed


def _month_html(events, focus: date, monday_first: bool, cal_colors, clash_ids) -> str:
    first = focus.replace(day=1)
    offset = first.weekday() if monday_first else (first.weekday() + 1) % 7
    start = first - timedelta(days=offset)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] if monday_first else ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    by_day: dict[date, list] = {}
    for e in events:
        for d in _days_of(e):
            by_day.setdefault(d, []).append(e)
    rows = []
    d = start
    while True:
        cells = []
        for _ in range(7):
            evs = by_day.get(d, [])
            evs = sorted(evs, key=lambda x: (not x["all_day"], x["start"]))
            chips = "".join(_chip(e, _color(e, cal_colors), e["id"] in clash_ids) for e in evs[:3])
            more = f'<div class="more">+{len(evs) - 3} more</div>' if len(evs) > 3 else ""
            cls = "cell" + (" other" if d.month != focus.month else "") + (" today" if d == date.today() else "")
            cells.append(f'<div class="{cls}"><div class="dn">{d.day}</div>{chips}{more}</div>')
            d += timedelta(days=1)
        rows.append("".join(cells))
        if d.month != focus.month and d > first:
            break
        if len(rows) >= 6:
            break
    head = "".join(f'<div class="dh">{n}</div>' for n in names)
    return f'<div class="month"><div class="mhead">{head}</div>{"".join(f"<div class=mrow>{r}</div>" for r in rows)}</div>'


def _grid_html(events, days: list[date], cal_colors, clash_ids) -> str:
    timed = [e for e in events if not e["all_day"]]
    in_range = [e for e in timed if any(d in days for d in _days_of(e))]
    # visible hours come from each event's slice ON a shown day (an overnight event ends at midnight on its first day)
    seg_lo, seg_hi = [8], [18]
    for e in in_range:
        for d in _days_of(e):
            if d in days:
                day0 = datetime.combine(d, time.min)
                s_ = max(e["start"], day0)
                e_ = min(e["end"], day0 + timedelta(days=1))
                seg_lo.append(s_.hour)
                seg_hi.append(24 if e_ >= day0 + timedelta(days=1) else (e_ - timedelta(seconds=1)).hour + 1)
    lo, hi = max(0, min(seg_lo)), min(24, max(seg_hi))
    height = (hi - lo) * HOUR_PX
    hours = "".join(f'<div class="hr" style="height:{HOUR_PX}px">{h:02d}:00</div>' for h in range(lo, hi))

    allday_cols, cols = [], []
    for d in days:
        ad = [e for e in events if e["all_day"] and d in set(_days_of(e))]
        allday_cols.append("".join(_chip(e, _color(e, cal_colors), False) for e in ad))
        dayevs = []
        for e in in_range:
            if d in set(_days_of(e)):
                s = max(e["start"], datetime.combine(d, time.min))
                en = min(e["end"], datetime.combine(d, time.min) + timedelta(days=1))
                dayevs.append({**e, "start": s, "end": en, "orig": e})
        dayevs.sort(key=lambda x: (x["start"], x["end"]))
        blocks = []
        for e, lane, n in _lanes(dayevs):
            top = max(0.0, (e["start"].hour + e["start"].minute / 60 - lo)) * HOUR_PX
            h = max(18.0, ((e["end"] - e["start"]).total_seconds() / 3600) * HOUR_PX - 2)
            o = e["orig"]
            cls = "blk" + (" proposed" if o["proposed"] else "") + (" clash" if o["id"] in clash_ids else "")
            tip = f"{o['title']}\n{o['start']:%a %d %b %H:%M}-{o['end']:%H:%M}" + (f"\n{o['location']}" if o["location"] else "")
            loc = f'<span class="loc">{_esc(o["location"])}</span>' if o["location"] and h > 40 else ""
            blocks.append(
                f'<div class="{cls}" style="--c:{_color(o, cal_colors)};top:{top:.1f}px;height:{h:.1f}px;'
                f'left:calc({lane}*100%/{n} + 1px);width:calc(100%/{n} - 3px)" title="{_esc(tip)}">'
                f'<b>{_fmt_t(e["start"])}</b> {_esc(o["title"])}{loc}</div>'
            )
        lines = "".join(f'<div class="ln" style="top:{i * HOUR_PX}px"></div>' for i in range(hi - lo))
        today = " today" if d == date.today() else ""
        cols.append(f'<div class="col{today}" style="height:{height}px">{lines}{"".join(blocks)}</div>')
    ncol = len(days)
    heads = "".join(f'<div class="dhead{" today" if d == date.today() else ""}">{d:%a} <b>{d.day}</b></div>' for d in days)
    allday = "".join(f'<div class="adcell">{c}</div>' for c in allday_cols)
    has_ad = any(allday_cols)
    return (
        f'<div class="grid" style="--n:{ncol}"><div class="gcorner"></div>{heads}'
        + (f'<div class="adlabel">all-day</div>{allday}' if has_ad else "")
        + f'<div class="hours">{hours}</div>{"".join(cols)}</div>'
    )


def _agenda_html(events, cal_colors, clash_ids) -> str:
    if not events:
        return '<div class="empty">No events in this range.</div>'
    by_day: dict[date, list] = {}
    for e in events:
        for d in _days_of(e):
            by_day.setdefault(d, []).append(e)
    parts = []
    for d in sorted(by_day):
        items = []
        for e in sorted(by_day[d], key=lambda x: (not x["all_day"], x["start"])):
            when = "all day" if e["all_day"] else f"{_fmt_t(e['start'])}-{_fmt_t(e['end'])}"
            cls = "ag" + (" proposed" if e["proposed"] else "") + (" clash" if e["id"] in clash_ids else "")
            loc = f' <span class="loc">{_esc(e["location"])}</span>' if e["location"] else ""
            tag = ' <span class="tag">proposed</span>' if e["proposed"] else ""
            items.append(f'<div class="{cls}" style="--c:{_color(e, cal_colors)}"><span class="when">{when}</span> {_esc(e["title"])}{tag}{loc}</div>')
        parts.append(f'<div class="agday{" today" if d == date.today() else ""}"><div class="agd">{d:%A %d %B}</div>{"".join(items)}</div>')
    return f'<div class="agenda">{"".join(parts)}</div>'


CSS = """
:root{--bg:#fff;--fg:#111827;--mut:#6b7280;--line:#e5e7eb;--soft:#f9fafb;--today:#eff6ff;--clash:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--fg:#e5e7eb;--mut:#9ca3af;--line:#2a2f3a;--soft:#171a21;--today:#16233b;--clash:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.35 system-ui,-apple-system,Segoe UI,sans-serif}
.wrap{padding:12px}.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.top h2{margin:0;font-size:16px}.top .sub{color:var(--mut);font-size:12px}
.legend{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 0;color:var(--mut);font-size:12px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;background:var(--c);margin-right:4px;vertical-align:-1px}
.legend .pp i{background:transparent;border:2px dashed var(--mut)}.legend .cl i{background:transparent;border:2px solid var(--clash)}
.chip{--c:#2563eb;background:color-mix(in srgb,var(--c) 18%,var(--bg));border-left:3px solid var(--c);border-radius:3px;padding:1px 4px;margin-top:2px;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.proposed{border-style:dashed!important;background:repeating-linear-gradient(135deg,color-mix(in srgb,var(--c) 14%,var(--bg)) 0 6px,var(--bg) 6px 12px)!important;border:1px dashed var(--c);border-left:3px dashed var(--c)}
.clash{outline:2px solid var(--clash);outline-offset:-1px}
.month{border:1px solid var(--line);border-radius:6px;overflow:hidden}.mhead,.mrow{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}
.dh{background:var(--soft);padding:4px 6px;font-weight:600;color:var(--mut);font-size:11px;border-bottom:1px solid var(--line)}
.cell{min-height:84px;padding:3px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);min-width:0}
.cell:nth-child(7n){border-right:0}.cell.other{background:var(--soft);color:var(--mut)}.cell.today{background:var(--today)}
.dn{font-weight:600;font-size:12px}.more{color:var(--mut);font-size:11px;margin-top:2px}
.grid{display:grid;grid-template-columns:44px repeat(var(--n),minmax(0,1fr));border:1px solid var(--line);border-radius:6px;overflow:hidden}
.gcorner,.adlabel{background:var(--soft);border-bottom:1px solid var(--line)}.adlabel{font-size:10px;color:var(--mut);padding:4px 2px}
.dhead{background:var(--soft);text-align:center;padding:4px 0;border-bottom:1px solid var(--line);border-left:1px solid var(--line);font-size:12px}
.dhead.today{background:var(--today)}.adcell{border-left:1px solid var(--line);border-bottom:1px solid var(--line);padding:2px;min-width:0}
.hours .hr{font-size:10px;color:var(--mut);text-align:right;padding-right:4px;border-top:1px solid var(--line)}
.col{position:relative;border-left:1px solid var(--line)}.col.today{background:var(--today)}.ln{position:absolute;left:0;right:0;border-top:1px solid var(--line)}
.blk{--c:#2563eb;position:absolute;overflow:hidden;font-size:11px;padding:2px 4px;border-radius:4px;border-left:3px solid var(--c);background:color-mix(in srgb,var(--c) 22%,var(--bg))}
.blk .loc,.ag .loc{color:var(--mut);display:block;font-size:10px}.ag .loc{display:inline;margin-left:6px}
.agenda .agday{border-top:1px solid var(--line);padding:6px 0}.agd{font-weight:600;margin-bottom:3px}.agday.today .agd{color:#2563eb}
.ag{--c:#2563eb;border-left:3px solid var(--c);padding:2px 8px;margin:2px 0;background:color-mix(in srgb,var(--c) 10%,var(--bg));border-radius:3px}
.when{display:inline-block;min-width:92px;color:var(--mut);font-variant-numeric:tabular-nums}.tag{font-size:10px;border:1px dashed var(--mut);border-radius:8px;padding:0 5px;margin-left:6px;color:var(--mut)}
.empty{color:var(--mut);padding:20px;text-align:center}.warn{margin-top:8px;color:var(--clash);font-size:12px}
"""


def _page(title, sub, body, cal_colors, has_proposed, n_clash) -> str:
    legend = "".join(f'<span><i style="--c:{c}"></i>{_esc(n if n != "default" else "Events")}</span>' for n, c in cal_colors.items())
    if has_proposed:
        legend += '<span class="pp"><i></i>proposed (not created yet)</span>'
    if n_clash:
        legend += '<span class="cl"><i></i>double-booked</span>'
    warn = f'<div class="warn">{n_clash} overlapping pair(s) of events.</div>' if n_clash else ""
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<style>{CSS}</style></head><body><div class=\"wrap\"><div class=\"top\"><h2>{_esc(title)}</h2><span class=\"sub\">{_esc(sub)}</span></div>"
        f'{body}<div class="legend">{legend}</div>{warn}</div></body></html>'
    )


# ---------------------------------------------------------------------------------------------- free slots
def compute_free_slots(events, day_from: date, day_to: date, minutes: int, start_h: time, end_h: time, skip_weekends: bool):
    slots = []
    d = day_from
    while d <= day_to:
        if not (skip_weekends and d.weekday() >= 5):
            win_s, win_e = datetime.combine(d, start_h), datetime.combine(d, end_h)
            busy = sorted(
                [(max(e["start"], win_s), min(e["end"], win_e)) for e in events
                 if not e["all_day"] and e["start"] < win_e and e["end"] > win_s],
            )
            cur = win_s
            for bs, be in busy:
                if bs - cur >= timedelta(minutes=minutes):
                    slots.append((cur, bs))
                cur = max(cur, be)
            if win_e - cur >= timedelta(minutes=minutes):
                slots.append((cur, win_e))
        d += timedelta(days=1)
    return slots


class Tools:
    class Valves(BaseModel):
        week_starts_monday: bool = Field(default=True, description="Month view starts weeks on Monday (otherwise Sunday).")
        work_day_start: str = Field(default="09:00", description="Default start of the working day for find_free_slots (HH:MM).")
        work_day_end: str = Field(default="18:00", description="Default end of the working day for find_free_slots (HH:MM).")
        skip_weekends: bool = Field(default=True, description="find_free_slots ignores Saturday and Sunday.")

    def __init__(self):
        self.valves = self.Valves()
        self.citation = False

    async def render_calendar(
        self,
        events: str,
        view: str = "week",
        focus_date: str = "",
        title: str = "Calendar",
        __event_emitter__=None,
    ):
        """
        Draws a calendar inline in the chat. Use it AFTER reading the user's events (e.g. with the Google get_events tool), or to
        preview a plan before creating it. Events that overlap are outlined in red; events with "proposed": true are drawn dashed.
        Do not retype the events in your reply; the picture shows them.

        :param events: JSON array of events, e.g. [{"title":"Standup","start":"2026-11-23T09:00:00","end":"2026-11-23T09:15:00","location":"Zoom","calendar":"Work"},{"title":"Deep work: report","start":"2026-11-23T10:00:00","end":"2026-11-23T12:00:00","proposed":true}]. start/end are ISO 8601 (a plain date such as "2026-11-24" means an all-day event). Optional keys: location, calendar, all_day, proposed.
        :param view: "month", "week" (7 days), "day" or "agenda" (list). Default week.
        :param focus_date: Any date (YYYY-MM-DD) inside the period to show. Default: the first event's date, or today.
        :param title: Heading above the calendar.
        :return: Confirmation that the calendar is displayed, plus any double-bookings found.
        """
        try:
            evs = _load_events(events)
        except Exception as e:  # malformed JSON from the model: say exactly what to fix
            return f"Could not read the events: {e}. Pass a JSON array of objects with title, start and end (ISO 8601)."
        view = (view or "week").strip().lower()
        if view not in ("month", "week", "day", "agenda"):
            view = "week"
        parsed = _parse_dt(focus_date) if focus_date else None
        focus = parsed[0].date() if parsed else (evs[0]["start"].date() if evs else date.today())

        pairs = _conflicts(evs)
        clash_ids = {e["id"] for p in pairs for e in p}
        cal_colors: dict[str, str] = {}
        for e in evs:
            _color(e, cal_colors)
        monday_first = self.valves.week_starts_monday

        if view == "month":
            body = _month_html(evs, focus, monday_first, cal_colors, clash_ids)
            sub = f"{focus:%B %Y}"
        elif view == "agenda":
            lo = focus
            body = _agenda_html([e for e in evs if e["end"].date() >= lo], cal_colors, clash_ids)
            sub = f"from {focus:%a %d %b %Y}"
        else:
            if view == "day":
                days = [focus]
                sub = f"{focus:%A %d %B %Y}"
            else:
                mon = focus - timedelta(days=focus.weekday() if monday_first else (focus.weekday() + 1) % 7)
                days = [mon + timedelta(days=i) for i in range(7)]
                sub = f"{days[0]:%d %b} - {days[-1]:%d %b %Y}"
            body = _grid_html(evs, days, cal_colors, clash_ids)

        page = _page(title, sub, body, cal_colors, any(e["proposed"] for e in evs), len(pairs))

        notes = []
        if pairs:
            notes.append("Double-bookings: " + "; ".join(f"'{a['title']}' overlaps '{b['title']}' on {a['start']:%a %d %b %H:%M}" for a, b in pairs[:6]))
        msg = (
            f"The {view} calendar ({len(evs)} events) is now displayed in the chat. Do not list the events again. "
            + (" ".join(notes) + " Mention these briefly." if notes else "No double-bookings.")
        )
        if __event_emitter__:
            await __event_emitter__({"type": "status", "data": {"description": "Calendar ready", "done": True}})
            await __event_emitter__({"type": "embeds", "data": {"embeds": [page]}})
            return msg
        return HTMLResponse(content=page, headers={"Content-Disposition": "inline"})

    async def find_free_slots(
        self,
        events: str,
        date_from: str,
        date_to: str = "",
        duration_minutes: int = 60,
        day_start: str = "",
        day_end: str = "",
    ) -> str:
        """
        Finds free time between existing events, inside working hours. Use it to plan: read the calendar first, call this, then
        propose events in the returned gaps (render them with render_calendar and "proposed": true) and only create them after the user agrees.

        :param events: JSON array of the user's existing events (same format as render_calendar: title, start, end ISO 8601).
        :param date_from: First day to search, YYYY-MM-DD.
        :param date_to: Last day to search, YYYY-MM-DD (default: same as date_from).
        :param duration_minutes: Minimum length of a free slot in minutes (default 60).
        :param day_start: Start of the working day HH:MM (default from settings, 09:00).
        :param day_end: End of the working day HH:MM (default from settings, 18:00).
        :return: A list of free slots per day.
        """
        try:
            evs = _load_events(events)
            a = _parse_dt(date_from)
            if not a:
                return "date_from must be YYYY-MM-DD."
            b = _parse_dt(date_to) if date_to else a
            if not b:
                return "date_to must be YYYY-MM-DD."
            d0, d1 = a[0].date(), b[0].date()
            if d1 < d0 or (d1 - d0).days > 62:
                return "Pick a range of 1 to 62 days (date_to on or after date_from)."
            hs = time.fromisoformat(day_start or self.valves.work_day_start)
            he = time.fromisoformat(day_end or self.valves.work_day_end)
        except Exception as e:
            return f"Bad input: {e}"
        minutes = max(5, min(int(duration_minutes), 12 * 60))
        slots = compute_free_slots(evs, d0, d1, minutes, hs, he, self.valves.skip_weekends)
        if not slots:
            return f"No free slot of {minutes} minutes between {hs:%H:%M} and {he:%H:%M} from {d0} to {d1}."
        lines, cur = [], None
        for s, e in slots:
            if s.date() != cur:
                cur = s.date()
                lines.append(f"{cur:%A %Y-%m-%d}:")
            lines.append(f"  {s:%H:%M}-{e:%H:%M} ({int((e - s).total_seconds() // 60)} min free)")
        return f"Free slots of at least {minutes} minutes ({hs:%H:%M}-{he:%H:%M}):\n" + "\n".join(lines)
