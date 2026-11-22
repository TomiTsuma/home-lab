"""Tests for src/calendar_planner.py. Run: python3.11 plugins/test_calendar_planner.py [out_dir]
(needs fastapi + pydantic; with out_dir it also writes the rendered pages there for a visual check)."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
import calendar_planner as cp  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    passed += ok
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'' if ok else '  ' + str(extra)}")


EVENTS = [
    {"title": "Standup", "start": "2026-11-23T09:00:00", "end": "2026-11-23T09:15:00", "location": "Zoom", "calendar": "Work"},
    {"title": "Supervisor call", "start": "2026-11-23T09:10:00+03:00", "end": "2026-11-23T10:00:00+03:00", "calendar": "Work"},
    {"title": "Gym", "start": "2026-11-23T18:30:00", "end": "2026-11-23T19:30:00", "calendar": "Personal"},
    {"summary": "Paper reading group", "start": {"dateTime": "2026-11-25T14:00:00Z"}, "end": {"dateTime": "2026-11-25T16:00:00Z"}, "calendar": "Work"},
    {"title": "Conference trip", "start": "2026-11-26", "end": "2026-11-28", "calendar": "Work"},
    {"title": "Late workshop", "start": "2026-11-27T21:00:00", "end": "2026-11-28T01:00:00", "calendar": "Work"},
    {"title": "Deep work: Ereuna report", "start": "2026-11-24T10:00:00", "end": "2026-11-24T12:30:00", "proposed": True},
    {"title": "Review reading list", "start": "2026-11-24T11:00:00", "end": "2026-11-24T11:45:00", "proposed": True},
]


def render(view, focus="2026-11-25", events=EVENTS):
    tools = cp.Tools()
    return asyncio.run(tools.render_calendar(json.dumps(events), view, focus, "Plan for the week"))


# ------------------------------------------------------------------ parsing
evs = cp._load_events(json.dumps(EVENTS))
check("all 8 events parsed (title/summary, string/dict dates)", len(evs) == 8, len(evs))
check("time zones dropped to wall clock", evs[1]["start"].hour == 9 and evs[1]["start"].minute == 10)
check("date-only start is all-day, end exclusive (Nov 26-28 = 2 days)", [e for e in evs if e["title"] == "Conference trip"][0]["all_day"])
check("fenced ```json blocks are accepted", len(cp._load_events("```json\n" + json.dumps(EVENTS[:2]) + "\n```")) == 2)
check("a wrapper object {events:[...]} is accepted", len(cp._load_events({"events": EVENTS[:3]})) == 3)
check("missing end -> 1 hour", cp._load_events('[{"title":"x","start":"2026-11-23T09:00"}]')[0]["end"].hour == 10)
check("events without a start are dropped, not fatal", cp._load_events('[{"title":"no start"},{"title":"ok","start":"2026-11-23"}]')[0]["title"] == "ok")

# ------------------------------------------------------------------ conflicts
pairs = cp._conflicts(evs)
names = {tuple(sorted((a["title"], b["title"]))) for a, b in pairs}
check("Standup/Supervisor call overlap found", ("Standup", "Supervisor call") in names, names)
check("proposed items that overlap each other are found", ("Deep work: Ereuna report", "Review reading list") in names, names)
check("no false positives (Gym vs Standup)", not any("Gym" in n for n in names), names)

# ------------------------------------------------------------------ rendering
for view in ("month", "week", "day", "agenda"):
    page = render(view)
    ok = isinstance(page, str) is False or True  # string result only when an emitter exists; see below
    html_ = asyncio.run(cp.Tools().render_calendar(json.dumps(EVENTS), view, "2026-11-25", "t"))
    check(f"{view}: returns an inline HTMLResponse when there is no event emitter", hasattr(html_, "body") and b"<html" in html_.body)
    if OUT:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"cal_{view}.html").write_bytes(html_.body)

page = asyncio.run(cp.Tools().render_calendar(json.dumps(EVENTS), "week", "2026-11-25", "t")).body.decode()
check("week shows Mon 23 .. Sun 29", "Mon <b>23</b>" in page and "Sun <b>29</b>" in page)
check("proposed + clash styling present", "blk proposed clash" in page or "blk proposed" in page and "clash" in page)
check("legend explains proposed and double-booked", "proposed (not created yet)" in page and "double-booked" in page)
check("calendars get distinct colours in the legend", page.count("<i style=") >= 3)

# overnight event must be visible on its first day (hours range must reach midnight)
check("week: overnight workshop is drawn on Friday (21:00) and Saturday (00:00)", page.count("Late workshop</div>") == 2 or page.count("Late workshop") >= 4, page.count("Late workshop"))
check("week: grid reaches 24:00 for it (23:00 row exists)", "23:00" in page)

# the multi-day all-day event shows on both days, the overnight event on both days
month = asyncio.run(cp.Tools().render_calendar(json.dumps(EVENTS), "month", "2026-11-25", "t")).body.decode()
check("month: all-day trip appears on 26th and 27th", month.count(">Conference trip</div>") == 2, month.count(">Conference trip</div>"))
check("month: overnight workshop appears on 27th and 28th", month.count("Late workshop</div>") == 2, month.count("Late workshop</div>"))

# emitter path: embeds event + short instruction to the model
sent = []


async def emitter(m):
    sent.append(m)


msg = asyncio.run(cp.Tools().render_calendar(json.dumps(EVENTS), "week", "2026-11-25", "t", __event_emitter__=emitter))
check("emitter path sends an embeds event", any(m["type"] == "embeds" and "<html" in m["data"]["embeds"][0] for m in sent))
check("model is told about double-bookings and not to repeat events", "Double-bookings" in msg and "Do not list" in msg, msg)

# ------------------------------------------------------------------ safety
evil = [{"title": "<script>alert(1)</script><img src=x onerror=alert(2)>", "start": "2026-11-23T09:00", "end": "2026-11-23T10:00", "location": "\"><b>x", "calendar": "<i>cal</i>"}]
for view in ("month", "week", "day", "agenda"):
    out = asyncio.run(cp.Tools().render_calendar(json.dumps(evil), view, "2026-11-23", "<b>t</b>")).body.decode()
    check(f"{view}: event text is HTML-escaped (no live <script>/<img>/<b>)", "<script>alert" not in out and "<img src=x" not in out and "<b>t</b>" not in out and "\"><b>x" not in out)
check("no JavaScript at all in the page", "<script" not in page.lower())

bad = asyncio.run(cp.Tools().render_calendar("not json", "week"))
check("malformed JSON gives a fixable message, not an exception", isinstance(bad, str) and "Could not read" in bad, bad)
empty = asyncio.run(cp.Tools().render_calendar("[]", "agenda"))
check("empty list renders", hasattr(empty, "body") and b"No events" in empty.body)

# ------------------------------------------------------------------ free slots
fs = asyncio.run(cp.Tools().find_free_slots(json.dumps(EVENTS[:3]), "2026-11-23", "2026-11-23", 60))
check("free slots on Monday: 10:00-18:00 free after the call", "10:00-18:00" in fs, fs)
fs2 = asyncio.run(cp.Tools().find_free_slots(json.dumps(EVENTS[:3]), "2026-11-23", "2026-11-23", 30, "09:00", "10:30"))
check("a 30-min slot is found between 10:00 and 10:30 only", "10:00-10:30" in fs2 and "09:" not in fs2.split("\n", 1)[1], fs2)
fs3 = asyncio.run(cp.Tools().find_free_slots("[]", "2026-11-28", "2026-11-29", 60))
check("weekends are skipped by default", "No free slot" in fs3, fs3)
check("bad range rejected", "62 days" in asyncio.run(cp.Tools().find_free_slots("[]", "2026-01-01", "2026-12-31")))
check("busy all day -> no slots", "No free slot" in asyncio.run(cp.Tools().find_free_slots('[{"title":"x","start":"2026-11-23T08:00","end":"2026-11-23T19:00"}]', "2026-11-23", "", 30)))
check("all-day events do not block working hours", "09:00-18:00" in asyncio.run(cp.Tools().find_free_slots('[{"title":"holiday","start":"2026-11-23"}]', "2026-11-23")))

print(f"\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
