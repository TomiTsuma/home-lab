# Google Calendar read/write and the calendar planner tool (2026-11-22)

**Request:** the Google tools in Open WebUI should include Calendar editing (fetch, add, edit, delete events),
and a plugin that renders a calendar and does planning.

## Changes
- `ai/open-webui/docker-compose.yml`, service `google-workspace-mcp`: `--read-only --tools drive docs sheets slides`
  replaced by `--permissions drive:readonly docs:readonly sheets:readonly slides:readonly calendar:full` (the two
  forms are mutually exclusive in workspace-mcp 2.6.0). Calendar tools (core tier): `list_calendars`, `get_events`,
  `manage_event` (create/update/delete/move/RSVP). Everything else stays read-only. 10 tools, about 7.5k tokens.
- New own-code tool `plugins/src/calendar_planner.py` (`render_calendar`, `find_free_slots`), sha256-pinned in
  `plugins/manifest.json`, tests in `plugins/test_calendar_planner.py` (37 checks, incl. HTML escaping and overnight events).
- Installed in Open WebUI as tool `calendar_planner`; `server:mcp:gworkspace` and `calendar_planner` added to all 9 model overrides.

## Why own code
Built-in Open WebUI Calendar (v0.13) is local only (docs: no Google/CalDAV/iCal sync) and the community catalogue has
event-listing tools but no calendar renderer. Rendering takes the events as data, so the tool needs no Google credentials.

## To know
- **One manual step:** Google must be re-authorised for the Calendar scopes (first Calendar call returns a sign-in link). Until
  then Calendar calls return "ACTION REQUIRED: Google Authentication Needed".
- The three 16k-context models (gemma4, qwen2.5:14b, DeepSeek-R1) now carry about 7.5k tokens of Google schema plus the
  homelab tools on every request; long chats on them may overflow. The 65k qwen3.5 models are fine.
- Rollback: restore the previous `command:` line and `docker compose up -d google-workspace-mcp`.
