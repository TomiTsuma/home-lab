# Open WebUI stack (Ollama + LiteLLM + Open WebUI)

Deployed on `aeacus-dl` (Proxmox VM 101, `192.168.100.17` LAN / Tailscale
`100.101.189.31`), the GPU box with an RTX 3060 passed through. Three
containers in one compose stack:

| Service | Image | Port | Role |
|---|---|---|---|
| `ollama` | `ollama/ollama:0.30.10` | `11434` | Runs local models (currently `qwen3.5:researcher`, `qwen3.5:9b`, `gemma4`, `qwen2.5:14b`, `DeepSeek-R1`) on the GPU. |
| `litellm` | `ghcr.io/berriai/litellm:main-stable` | `4000` | OpenAI-wire-protocol proxy in front of Claude. |
| `open-webui` | `ghcr.io/open-webui/open-webui:main` | `3005` | The chat UI, talks to both of the above. |

## Why a LiteLLM proxy exists at all

Open WebUI's "OpenAI connection" type only speaks the **OpenAI wire protocol**
(`Authorization: Bearer ...`, `POST /v1/chat/completions`). Anthropic's API is
a **different protocol** (`x-api-key` header, `anthropic-version` header,
`POST /v1/messages`). Pointing an Open WebUI OpenAI-connection straight at
`https://api.anthropic.com/v1` looks plausible but never works — not because
of a bad API key, but because the two sides are speaking different languages.
LiteLLM translates between them, so Open WebUI's OpenAI connection just needs
to point at `http://litellm:4000/v1` with `LITELLM_MASTER_KEY` as the bearer
token.

## Deploying from scratch

```sh
cd homelab/ai/open-webui
cp .env.example .env            # fill in ANTHROPIC_API_KEY and LITELLM_MASTER_KEY
chmod 600 .env
./host-gpu-prep.sh              # one-time: persistence mode + cgroup driver fix (see script)
docker compose up -d
```

Wiring Open WebUI's Anthropic connection is a one-time step through the admin
UI (Settings → Connections → OpenAI API): base URL `http://litellm:4000/v1`
(from inside the container network, since Open WebUI calls it by Docker DNS
name), API key = `LITELLM_MASTER_KEY`.

To add/remove Claude models, edit `litellm-config.yaml`'s `model_list` and
restart the `litellm` service — no code changes needed.

## Access (LAN + Tailscale)

Open WebUI is published on `0.0.0.0:3005`:

- `http://192.168.100.17:3005` (LAN)
- `http://100.101.189.31:3005` (tailnet IP)
- `https://aeacus-dl.tailaee7b8.ts.net` (tailnet HTTPS with a valid cert, through
  `tailscale serve --https=443` → `127.0.0.1:3005`; the browser needs HTTPS for the mic and clipboard)

It is not exposed to the internet: there is no port-forward and no Cloudflare hostname, and
signup is disabled. Don't add a `tailscale serve --http=3005` rule: serve routes by hostname,
so the rule catches requests to the tailnet IP on port 3005 and returns 404 (this happened
on 2026-11-21). `mcp-tools` stays unpublished whatever the setting here. Check the serve
config with `tailscale serve status`; tailscaled keeps it across reboots.
`WEBUI_SECRET_KEY` is pinned in `.env` so recreating the container doesn't log you out.

## Web search (Exa)

Open WebUI's built-in web search uses **Exa** (`WEB_SEARCH_ENGINE=exa`, key `EXA_API_KEY`
in `.env`). These are PersistentConfig: once changed in Admin → Settings → Web Search,
the DB value wins over the env. (A self-hosted SearXNG was tried first on 2026-11-21 and
replaced by Exa at the owner's request; scrape.do is available as an alternative search
tool via the MCP server below.)

Using it: toggle **Web Search** under the message box, or — better — set the model's
*Function Calling* to **Native** (Admin → Settings → Models → Advanced Params) so the
model can search, fetch and call MCP tools itself, repeatedly.

## Memory and referencing other chats (v0.13.4)

- **Built-in tools** (used automatically when a model's Function Calling is *Native*, the
  default since 0.10): `add_memory` / `search_memories` / `update_memory`, `search_chats` /
  `view_chat` (lets the model look up and read your earlier conversations), notes, knowledge.
- **Background review** (`ENABLE_MEMORY_BACKGROUND_REVIEW=true`, every 6 turns): Open WebUI
  distils durable facts from the conversation into memory by itself.
- Review/edit memories: Settings → Personalization → Memory.
- Small local models use these tools less reliably than Claude; for "remember this" /
  "what did we decide in that other chat" tasks, Claude via LiteLLM is the dependable path.

## Context window / "responses randomly cut off"

The symptom is a reply that stops mid-sentence, or a tool loop that ends with no answer. It
means Ollama ran out of context: its log shows `stop processing: n_tokens = 16383, truncated = 1`.
Every step of a tool loop re-sends the system prompt, the tool schemas, the chat history and
all tool results so far. On 2026-11-21 one chat grew from 10.7k to 16.4k tokens over 6 steps
with a 16k window and was cut off.

What is in place:
- **qwen3.5:9b defaults to a 64k context inside Ollama.** It was re-tagged in place with
  `PARAMETER num_ctx 65536` (`ollama create qwen3.5:9b -f` a Modelfile whose `FROM` line is
  `qwen3.5:9b`). It needs 7.7 GB of VRAM and stays 100% on the GPU (its hybrid attention keeps
  the KV cache small). Set this in Ollama rather than per chat: if Open WebUI's background
  tasks asked for a different `num_ctx`, Ollama would reload the model (minutes on this disk).
  **`ollama pull qwen3.5:9b` resets it**, so re-apply after a pull:
  ```sh
  docker exec ollama sh -c 'printf "FROM qwen3.5:9b\nPARAMETER num_ctx 65536\n" > /tmp/M && ollama create qwen3.5:9b -f /tmp/M'
  ```
  Other models keep the global `OLLAMA_CONTEXT_LENGTH=16384`. qwen2.5:14b would not fit at 64k.
- **Open WebUI model override for qwen3.5:9b** (Workspace → Models): Function Calling =
  Native. Built-in tool groups are limited to chats, memory, web_search, files and time.
  knowledge, image/video generation and subagents are off: there are no knowledge bases or
  image backends, and every enabled group's schemas are sent with every request.
- **mcp-tools output caps:** `fetch_url` 6k chars, `parse_document` 16k (the full text is
  saved to `parsed/`), `read_file` and command output 12k, scrape.do 5 results.
- Check: `docker exec ollama ollama ps` should show `CONTEXT 65536`; grep the ollama log for
  `truncated = 1`.

## Chlio MCP (`chlio-mcp/`)

Open WebUI has Chlio (the notes / mind-map / planner app at `https://chlio.ereuna.org`) registered as MCP server
`chlio` (`https://chlio.ereuna.org/mcp`, auth *Bearer*, token `open-webui`, 365 days). 26 tools (about 4.2k tokens of
schema): mind maps from an outline, tasks, events, notes, Jira, search. Enabled by default on **qwen3.5:9b and the
Claude models only**; the 16k-context local models are skipped because Chlio's schemas plus the other default tools
would leave them no room to answer. To change that: `CHLIO_MODELS='*'` (or other patterns), and to limit the tool
set add `?groups=mindmaps,planner,notes,jira,search` to `CHLIO_MCP_URL`.

```sh
# on minos: mint a token, show it only to the next command
{ printf '%s
' "$SUDO_PASSWORD"; ssh minos@100.79.34.33 'docker exec clio-app node server/cli.js token create open-webui --days 365 | tail -1'; } | ssh aeacus@100.101.189.31 '...'
```
`chlio-mcp/connect_chlio_mcp.py` is idempotent and reads the token from stdin only (see its docstring). To rotate: revoke
the old token in Chlio (Account -> Access tokens, or `cli.js token revoke <id>`) and re-run it with a new one.
Tested with Open WebUI's own MCP client from inside the container (connects in ~0.7 s, 26 tools, read calls work).

## Google Calendar + Drive / Docs / Sheets / Slides (`google-workspace-mcp/`)

**Self-hosted** [`workspace-mcp`](https://github.com/taylorwilsdon/google_workspace_mcp)
(MIT, pinned `2.6.0`). It calls the regular Google REST APIs.

**Why not the Google-hosted servers.** The first attempt (2026-11-21, `google-mcp/`, now
superseded) used Google's hosted `*mcp.googleapis.com` servers. Sign-in and tool listing worked,
but every tool call returned *"The caller does not have permission"*. Those servers are in the
Workspace Developer Preview and need the Cloud project enrolled, and the account is personal
Gmail.

**How it runs**
- **Mode and scope (2026-11-22):** single-user, `--tool-tier core`, `--permissions drive:readonly
  docs:readonly sheets:readonly slides:readonly calendar:full`. Calendar can read AND write; the other
  four stay read-only. (`--permissions` replaces `--read-only`/`--tools`, which are mutually exclusive
  with it.) 10 tools, about 7.5k tokens of schema:
  - Calendar: `list_calendars`, `get_events` (fetch, one event or a range), `manage_event`
    (create, update, delete, move, RSVP).
  - Read-only: `search_drive_files`, `get_drive_file_content`, `get_drive_file_download_url`,
    `get_drive_shareable_link`, `get_doc_content`, `read_sheet_values`, `get_presentation`.
  - The extended tier would add `query_freebusy`, `manage_calendar`, focus time and out-of-office,
    but it would also enlarge every other service's tool list, so it is not enabled.
- **Registration:** registered in Open WebUI as `gworkspace` (auth: none) and default-on for every
  model (`google-workspace-mcp/register_in_openwebui.py`).
- **Credentials:** the Google refresh token lives in volume `open-webui_gws-data` at
  `/data/credentials`.

**Isolation.** The `/mcp` endpoint has no auth of its own, so access is limited by network:
- The container is attached only to the `gws` Docker network, whose only other member is open-webui.
- Its only published port is `127.0.0.1:8001`.
- `tailscale serve` exposes exactly one path from it: `https://aeacus-dl.tailaee7b8.ts.net/oauth2callback`.
- Verified: the LAN can't reach `:8001`. The mcp-tools sandbox can't resolve the service name,
  and its requests to `172.17.0.1:8001` and `172.18.0.1:8001` are blocked.

**Google OAuth client**
- Required Authorized redirect URI: `https://aeacus-dl.tailaee7b8.ts.net/oauth2callback`.
- It reuses `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` from `.env`.
- `GOOGLE_WORKSPACE_USER_EMAIL` in `.env` sets the default account.

**Sign in or re-auth.** Any Google tool call made without valid credentials returns a Google
sign-in link. Open it in a browser on the tailnet. If the app is External and in Testing, Google
expires the refresh token after 7 days.

**Calendar re-consent.** Moving Calendar from "off" to read/write adds scopes, so the first Calendar
call returns a Google sign-in link; approve it once (consent screen lists Calendar, Drive/Docs/Sheets/
Slides read-only). The Google Calendar API must be enabled on the Cloud project.

**Making another service writable later:** change its level in `--permissions` (e.g. `drive:full`),
recreate the container (`docker compose up -d google-workspace-mcp`) and approve the new consent.

## Calendar planner (`plugins/src/calendar_planner.py`)

Open WebUI's built-in Calendar (`/calendar`, tools `search/create/update/delete_calendar_event`) does
**not** sync with Google Calendar, and the community catalogue has no calendar renderer, so Google
Calendar is the source of truth here and this is our own small tool (no external code, no JavaScript in
its output) that draws it:
- `render_calendar(events, view, focus_date, title)`: month / week / day / agenda, inline in the chat.
  Overlapping events get a red outline; events with `"proposed": true` are dashed, so a plan can be
  previewed before anything is created. Light/dark aware.
- `find_free_slots(events, date_from, date_to, duration_minutes, day_start, day_end)`: free gaps inside
  working hours (valves: work-day start/end, skip weekends).
- Typical planning chat: the model calls `get_events` -> `find_free_slots` -> `render_calendar` with the
  proposed blocks -> you agree -> it calls `manage_event` once per block.
- Tests: `python3.11 plugins/test_calendar_planner.py` (37 checks). Installed with `install_plugins.py`;
  the tool and `server:mcp:gworkspace` are default-on for every model (`default_tools_all_models.py`).

## ClickHouse MCP (`clickhouse-mcp/`)

Official [`mcp-clickhouse`](https://github.com/ClickHouse/mcp-clickhouse) (Apache-2.0, pinned `0.7.0`, PyPI latest on
2026-11-22) as container `clickhouse-mcp`, registered in Open WebUI as `clickhouse` and default-on for every model.
**Installed without a ClickHouse target**: until `.env` points it at an instance, tool calls fail with a connection error to
`localhost:8123` (nothing real is behind that).

- **Tools (about 1k tokens):** `list_databases`, `list_tables` (schema, comments, pagination), `run_query`.
  Read-only: `CLICKHOUSE_ALLOW_WRITE_ACCESS=false` and `CLICKHOUSE_ALLOW_DROP=false` are hard-coded in compose, so queries
  run with `readonly=1` on the server. Query timeout 30 s (`CLICKHOUSE_MCP_QUERY_TIMEOUT`), 4 workers.
- **Protocol:** ClickHouse's **HTTP interface** (8123 plain, 8443 TLS) via clickhouse-connect. The native port 9000 is NOT used,
  so whatever ClickHouse you run must expose 8123/8443 to aeacus-dl (published port or LAN/Tailscale address).
- **Isolation:** private Docker network `chmcp` shared only with open-webui; no published port; mandatory bearer token
  (`CLICKHOUSE_MCP_AUTH_TOKEN`, generated into the chmod-600 `.env`, stored in Open WebUI's config DB by
  `register_in_openwebui.py`); `Host` header allow-list (a wrong Host gets 421, no token gets 401); read-only rootfs,
  no capabilities, 512 MB, 128 pids.
- **Connect it to a ClickHouse (later):** set in `.env` on aeacus-dl, then `docker compose up -d clickhouse-mcp`:
  `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT` (8123 or 8443), `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, optionally
  `CLICKHOUSE_DATABASE`, `CLICKHOUSE_SECURE` (`true` for 8443/TLS), `CLICKHOUSE_VERIFY`. Use a **dedicated** ClickHouse user
  with only `GRANT SELECT ON <db>.* TO mcp_reader` (the MCP's guard is best-effort; the user's grants are the real boundary).
  The container reaches LAN/Tailscale addresses through normal Docker NAT (e.g. data-handler `192.168.100.9`).
- **Open WebUI restart note:** `open-webui` was attached to the `chmcp` network live (`docker network connect`); the compose file
  also lists it, so a normal `docker compose up -d` keeps it. Re-register after wiping Open WebUI's DB:
  `docker exec -e CLICKHOUSE_MCP_AUTH_TOKEN=... open-webui python3 /tmp/register_clickhouse.py`.
- **Current target (2026-11-22): the emergency instance on data-handler (VM 100)**: `100.127.65.29:18123` (Tailscale IP, HTTP,
  **no TLS**, hence `CLICKHOUSE_SECURE=false`; the hop is still encrypted by Tailscale), database `atlas_analytics`, user
  `toonaweza`. Credentials live only in aeacus-dl's `.env`. Verified read-only (`readonly=1` in the session). Server version
  24.10.2.80. It is a throwaway box: when `atlas-orchestrator.atlas.coreoutline.com` is back, repoint `CLICKHOUSE_HOST/PORT/USER/PASSWORD`
  (and use TLS 8443 + a dedicated `GRANT SELECT` user) and retire these credentials. `list_tables` returns each table's full
  `CREATE TABLE` text, so ask for small pages.
- **Gotcha:** `clickhouse/clickhouse-server:latest` crashed with "Illegal instruction" on VM 100 (Proxmox `cpu: x86-64-v2-AES` hides
  AVX/AVX2), but 24.10.2.80 runs there fine. Prefer a pinned older tag on that VM, or set `cpu: host` (VM restart) before using `latest`.

## Dagster MCP (`dagster-mcp/`)

Community [`dagster-mcp`](https://github.com/fabdendev/dagster-mcp) (MIT, single maintainer, `0.12.3` of 2026-09-28) as container
`dagster-mcp`, registered in Open WebUI as `dagster`. **Installed without a Dagster target**: calls return
"Cannot connect to Dagster at http://localhost:3000" until `.env` points it at a webserver.
- **Why this one:** Dagster's official MCP is the Dagster+ hosted endpoint (cloud only). This wraps the GraphQL API of any
  self-hosted webserver (`<DAGSTER_URL>/graphql`), which is what Maia provisions per warehouse.
- **Vetted, not trusted:** the source was read in full (3.3k lines): its only network call is the GraphQL POST to `DAGSTER_URL`;
  no telemetry. The image build downloads the exact wheel and **aborts unless its sha256 matches** the reviewed value
  (`dagster-mcp/Dockerfile`); bump version and hash together after re-reading the diff. Transitive deps (fastmcp, httpx) are
  not hash-pinned.
- **Read-only:** `DAGSTER_READ_ONLY=true` is hard-coded, so the write tools (`launch_job`, `materialize_assets`,
  `backfill_assets`, `terminate_run`, `reload_code_location`, start/stop schedule/sensor) are never registered.
  17 read tools, about 5.2k tokens: runs (`get_runs`, `get_run_status`, `get_run_logs`, `get_run_stats`,
  `get_run_failure_summary`), assets (`search_assets`, `get_asset_details`, `get_asset_health`,
  `get_recent_materializations`, `resolve_asset_selection`), `list_jobs`, `list_schedules`, `list_sensors`, `get_tick_history`,
  `list_code_locations`, `get_instance_status`, `list_backfills`.
- **Only stdio upstream**, so `dagster-mcp/serve_http.py` (our code) serves it over streamable HTTP with a mandatory bearer token
  (`DAGSTER_MCP_AUTH_TOKEN`, constant-time compare, 401) and a Host allow-list (421). Private Docker network `dgmcp` shared only
  with open-webui; no published port; read-only rootfs, no capabilities, 512 MB.
- **Default-on only for large-context models** (`qwen3.5:9b`, `qwen3.5:researcher`, the four Claude models): 5.2k tokens would
  overflow the 16k models (gemma4, qwen2.5:14b, DeepSeek-R1). Change with `DAGSTER_MCP_MODELS=... register_in_openwebui.py`.
- **Current target (2026-11-22): the emergency Dagster on data-handler**: `DAGSTER_URL=http://100.127.65.29:13000` (webserver 1.13.31,
  no auth, Tailnet only; 5 code locations, 5 schedules RUNNING). Verified: `get_instance_status` healthy, `list_code_locations`,
  `list_schedules`. Same throwaway caveat as ClickHouse above.
- **To point it elsewhere:** in `.env` on aeacus-dl set `DAGSTER_URL` (e.g. `http://192.168.100.9:3000`), `DAGSTER_API_TOKEN` only if the
  webserver needs one, then `docker compose up -d dagster-mcp`. For several Dagster instances (one per Maia warehouse) set
  `DAGSTER_ENVS` (JSON name -> url/token); tools then take `env=<name>`.

## Importing ChatGPT conversations (`chatgpt-import/`)

`chatgpt-import/import_chatgpt.py` imports a ChatGPT data export (`conversations-NNN.json` + the `*.dat` attachments) through
Open WebUI's `POST /api/v1/chats/import` (the Settings > Chats importer does not handle this export's shards, hidden reasoning or
attachments). Imported on **2026-11-22**: 993 chats / 17,494 messages (Jan 2023 to Nov 2026), verified against the source.
- **Run:** copy the export into the open-webui container (`/tmp/chatgpt`), copy the script, then
  `docker exec open-webui python3 /tmp/import_chatgpt.py --src /tmp/chatgpt` (`--dry-run` first, `--limit N` / `--only <id,..>` for
  trials). It mints a short-lived admin JWT inside the container, so nothing needs a password.
- **Conversion:** keeps original dates, archived flag and model name (gpt-4o, gpt-5, ...); tags every chat `chatgpt-import`
  (`meta.chatgpt_id` is the de-duplication key, so a re-run skips what is already there); drops hidden "thoughts" and
  reasoning-recap entries and ChatGPT's citation markers (U+E200..U+E206 blocks, entity markers become the plain name).
- **Nothing is dropped from the conversation itself:** 2.7% of the export's visible messages are not linked from `current_node`
  (its `children` lists are incomplete). They are rebuilt from `parent` pointers: native Open WebUI branches (arrow switcher) when
  they hang off the main thread, or a separate "<title> (recovered fragment N)" chat when they do not (4 such chats, 8 messages).
- **Attachments:** images (174) are embedded inline as data URIs, because Open WebUI's frontend treats a relative image `url` as a
  file *id* (a path like `/api/v1/files/<id>/content` renders as a broken image). Other files (24) are uploaded with
  `?process=false` (no embedding) and show as file chips. 60 attachments are not in the export; those messages carry an italic
  "[attachment not included in the ChatGPT export: name]" note.
- **Limits:** the old ChatGPT model ids do not exist in Open WebUI, so these chats are for reading/searching; continuing one means
  picking another model. Uploaded non-image files are not processed for RAG. The database grew from 14 MB to 228 MB (inline images
  are stored in both the `chat` and `chat_message` tables); full-text search over about 1,000 chats takes 2-5 s.
- **Undo:** `python3 import_chatgpt.py --src x --rollback` deletes every chat it created (`meta.source == chatgpt-export`) and every
  file it uploaded. The pre-import database backup is `/app/backend/data/webui.db.bak-pre-chatgpt-import-20261122-030613` (in the
  `open-webui` volume); delete it once you are happy.

## Importing Claude conversations (`chatgpt-import/import_claude.py`)

Imports a claude.ai data export (`conversations.json` + `design_chats/*.json`) the same way as the ChatGPT import (own code, same
`/api/v1/chats/import` route, reuses `import_chatgpt.py`'s API client, so copy both files into the container). Imported on
**2026-11-22**, two accounts: 224 chats / 3,601 messages (`ereuna.org`: 50 chats, Oct-Nov 2026; Gmail: 174 chats, May-Nov 2026),
including 5 Claude Design chats and 3 recovered fragments.
- **How to get the export:** claude.ai gives a manifest of per-category zip links (`manifest-*.json`). The links sit behind
  Cloudflare's bot check, so they must be opened in a signed-in browser (they are valid 24 h and may be single-use); a script cannot
  fetch them. Each account exports separately.
- **Run:** `python3 import_claude.py --src <dir with conversations.json> --account ereuna|gmail [--dry-run|--limit N|--only <uuid,..>]`.
  `--rollback` deletes everything it created (`meta.source == claude-export`) and the files it uploaded.
- **Tags:** `claude-import` + `claude-<account>` (+ `claude-design`); `meta.claude_uuid` is the de-duplication key.
- **Conversion:** the branch ending at the newest message is the chat; other branches are native Open WebUI branches (22 branch points);
  unconnected pieces become "(recovered fragment N)" chats. Text blocks are the message text. Each tool call (about 11,400, mostly
  agentic Bash/Read/Write/WebSearch) becomes one collapsed row (name + label, input and result truncated to 1.2k/1.5k chars). A message
  whose tool output would exceed 40k chars is shrunk, and if still too big its calls are folded into one list (the largest message went
  from 691,000 to 58,900 chars; 15 messages summarised, 76 shrunk). Hidden `thinking` (6,374), `injected_prompt_block` (440) and
  `token_budget` blocks are dropped.
- **Attachments:** the export has NO bytes for uploaded images/PDFs (only name and id), so 933 of them are listed in an italic note on
  their message. The 85 pasted-text attachments carry full text and were uploaded as `.txt`/`.md` files (`process=false`, chips).
- **Not imported (your call):** `projects` (19 projects, 39 documents plus instructions; the export does not link chats to projects),
  `memories` (Claude memory files; the Gmail account also has a conversations memory and 5 project memories), `frames` (about 600 MB of
  Claude artifact versions), `artifact_data`, `claude_code_cloud` (routines and runs), `feedback`. Projects could become Knowledge bases and
  memories Notes if wanted.
- **Verification:** counts equal the dry run exactly (chats, messages, `chat_message` rows, no duplicates, integrity ok); in the real UI the
  tool rows start collapsed and expand to Input/Result, file chips and notes show, the branch switcher appears. Database 228 -> 289 MB.
  Backup before this import: `/app/backend/data/webui.db.bak-pre-claude-import-20261122-044919` (delete when happy).

## Claude memories, projects and chats -> Open WebUI (2026-11-22)

Scripts in `chatgpt-import/` (own code, run inside the open-webui container next to `import_chatgpt.py`):
- **Memories** (`import_claude_memories.py`): Open WebUI 0.13 memory is path-based (`type` user|context + `path`; the model can list/read paths
  and search), which matches Claude's memory files. 77 imported as `claude/<account>/<original path>` (project ids replaced by project names;
  Gmail also has `conversations-memory.md` and one `memory-summary.md` per project). Only each account's `profile.md` / `preferences.md` are type
  `user` (recalled in every chat, capped by the admin setting `memories.user_char_limit`, default 2000 chars, so not all of it fits); everything else
  is `context` (found by semantic search or read by path, capped by `context_char_limit`). Verified: semantic search returns the right files.
  **Heads-up:** `claude/ereuna/preferences.md` contains Claude's standing "deeper, not bigger" prompt-rewrite routine, which is now injected into
  every chat; demote it to `context` (or delete it) if you do not want that in Open WebUI. `--rollback` deletes all `claude/*` memories.
- **Projects -> Folders** (`import_claude_projects.py`): Open WebUI calls them "Projects" in code and "Folders" in the UI. Parent folder per account
  ("Claude · ereuna.org", "Claude · Gmail", which the owner later renamed "Claude · Personal"), one sub-folder per project: instructions
  (`prompt_template` + description) -> folder system prompt (the ereuna.org Geothermal project's is 40,732 chars), documents -> files uploaded with
  processing (embedded, searchable). 17 folders (the starter "How to use Claude" projects were skipped). Export has only extracted text for documents,
  so non-text names get `.txt`. With native tool-calling a folder's files are read through the built-in *knowledge* tool, which `qwen3.5:9b` has
  switched off (`builtinTools.knowledge=false`); enable it for that model if you want it to use folder documents.
- **Chats -> their folders** (`assign_chats_to_folders.py`, map built from evidence in the export): conversations carry no project field, so membership
  is inferred in priority order: (1) memory write into `/projects/<id>/`; (2) the injected memory snapshot lists that project's files (in a project
  chat the current project's files are listed as full paths, in other chats other projects appear only as collapsed `projects/<id>/` entries);
  (3) a project document is named in the chat (agrees with (2) 3/3 times they overlap); (4) project-knowledge-search used (4 Drug Discovery chats:
  1 by text overlap with the project documents, 3 inferred by topic). Contradictory evidence leaves a chat unfiled. Result: 49 project
  conversations filed (55 chats counting 1 recovered fragment and 5 design chats): Gmail account 39 (Drug Discovery 14, Geothermal ML 11, Cropnuts 6,
  Personal Portfolio 6, Core&Outline 2) and ereuna.org 10 (Geothermal 4, Design and Brand 4, Briefings in Bioinformatics 1, Partnerships 1), plus the
  5 Claude Design chats in a "Claude Design" folder per account. The other 170 of the 219 conversations show no evidence of being in a project (many
  have no memory snapshot at all, so absence of evidence is not proof) and stay unfiled. Chats now inherit their folder's instructions when continued.
  `--rollback` moves them back out of folders.

## Gmail: read and draft, never send (`google-workspace-mcp/split_connections.py`)

Added 2026-11-22 to the existing Google server (same container, same sign-in):
- Permission `gmail:readonly+drafts` = scopes `gmail.readonly` + `gmail.compose` only (no labels/modify/settings). Google has no drafts-only
  scope and `gmail.compose` can technically send, so `send_gmail_message` is also disabled in the server (`--disabled-tools`), together with label,
  filter and modify tools. `draft_gmail_message` is in the "extended" tier, hence `--tool-tier extended`: the server now registers 30 tools.
- Open WebUI exposes it through two connections on the same URL with allow-lists (`function_name_filter_list`): `gworkspace` = the original 10
  (Calendar r/w, Drive/Docs/Sheets/Slides read-only), `gmail` = 7 tools (`search_gmail_messages`, `get_gmail_message_content`,
  `get_gmail_messages_content_batch`, `get_gmail_thread_content`, `get_gmail_attachment_content`, `list_gmail_labels`, `draft_gmail_message`).
- `gmail` is default-on only for the four Claude models; local models can enable it per chat from the "+" menu. Reason: email text is untrusted input
  (prompt injection), and the risky combination is a model that reads a hostile email while holding write or fetch tools. Draft-only plus no
  sending limits the damage, but `fetch_url`/`publish_file` (homelab tools) remain possible exfiltration paths, so use trusted models for mail.
- One-time Google consent is required for the new scopes (the first Gmail call returns the sign-in link); the Gmail API must be enabled in the
  Cloud project. Single-user server: it serves ONE Google account (tommytsuma7@gmail.com), not the ereuna.org mailbox.

## Why qwen3.5:9b sometimes "thinks and stops" (2026-11-22 findings)

- Real chat records show: Claude Haiku built a Reveal.js deck from calendar + Chlio data end to end; `qwen3.5:9b` summarised the calendar in text
  but, on "generate slides using the reveal.js tools" (no subject), ended with an EMPTY message after `recall_memories` and a short reasoning block.
- Causes seen: about 30k tokens of tool definitions on that model; the always-attached `visualize` skill makes it call `list_available_skills` /
  `view_skill` on any "render/draw" request; with thinking on, the turn can end inside the reasoning block (empty answer, no tool call).
- Reveal.js decks do render inline on 0.13.4 (native mode, `(HTMLResponse, text)` return). `calendar_planner` was switched to the same return shape
  (it used an `embeds` event that was never verified in a real chat).
- Variant model `qwen3.5:9b (agent)` created (thinking off via `think: false`, no skill, only calendar/Gmail/slides/Chlio tools). **Not yet validated:**
  the end-to-end test was interrupted (network change, then memory pressure on the laptop). Your default `qwen3.5:9b` is unchanged.

## GitHub and Gitea MCP (`git-mcp/`, compose services `github-mcp` and `gitea-mcp`)

Installed 2026-11-22. Both are the official servers, run in HTTP mode, **read-only**, image pinned by digest, on private Docker networks
(`ghmcp`, `gtmcp`) shared only with open-webui, no published ports, read-only rootfs, no capabilities. Neither container holds a credential:
they are stateless and Open WebUI sends the user's token on every request (connection key), so the tokens live only in Open WebUI's config DB.
- **Gitea** (`gitea.com/gitea/gitea-mcp` 1.13.0): connected to the Gitea app on TrueNAS, `http://100.100.133.10:30008` (Tailscale IP; Gitea 1.25.5).
  28 read tools (~5.4k tokens): repos, files, branches, commits, issues, PRs, releases, wiki, packages, actions runs, search. Token: a dedicated
  access token `open-webui-mcp` on user `aeacus` with read-only scopes (read:repository, issue, organization, user, misc, notification, package;
  no write, no admin). Verified: real repos returned (`aeacus/lyre`, `deploy`, `ml-containers`, ...), and a write with the token is refused (HTTP 403).
  Revoke: Gitea > Settings > Applications. The MCP also runs with `-r`/`GITEA_READONLY`, so a wider token would still not write until that is removed.
  Note: Gitea's `ROOT_URL` lacks the port, so links in results read `http://100.100.133.10/...` instead of `...:30008/...`.
- **GitHub** (`github/github-mcp-server` v2.2.0, `http` mode, `--read-only`, toolsets context,repos,issues,pull_requests): the full set is ~18k tokens,
  so Open WebUI exposes a 13-tool allow-list (`get_me`, `search_repositories`, `get_file_contents`, `list_commits`, `get_commit`, `list_branches`,
  `search_code`, `issue_read`, `list_issues`, `search_issues`, `pull_request_read`, `list_pull_requests`, `search_pull_requests`). **Connected 2026-11-22**
  with the owner's fine-grained token (login TomiTsuma, expires 2027-02-20): verified `get_me` and a repository search (83 public repos) through Open WebUI's
  network. That token is **public-repositories read-only**: it sees 0 private repos and no organization membership (its "all repositories" means public ones;
  public org repos such as coreoutline and DataExpert-io-Community are visible). For private repos create a token with a resource owner and repository
  selection (one token per owner: your account, each org) and add a connection per owner. Before it was connected the connection was disabled. Create a fine-grained PAT (GitHub > Settings > Developer settings > Fine-grained
  tokens; the repos you want; permissions Contents, Issues, Pull requests, Metadata = Read only; expiry 90 days), then paste it in Open WebUI
  Admin > Settings > External Tools > GitHub > Key and switch the connection on (or run `register_in_openwebui.py` with it on stdin).
  Verified without a real token: the server reaches api.github.com and returns GitHub's "401 Bad credentials" for a dummy token.
- **Exposure:** both are default-on for the four Claude models only. Repository files, issues and PR text are untrusted input (prompt injection), so the
  local models can switch them on per chat from the "+" menu instead of having them by default.
- **Write access later:** drop `-r` and `GITEA_READONLY` (Gitea) / `--read-only` (GitHub) in compose and use a token with write scopes.

## Community plugins (`plugins/`)

Installed 2026-11-21. The plugins run arbitrary Python inside Open WebUI, so each file was read
before install (a security review of its network hosts, subprocess/eval, database writes and
environment reads) and **pinned by sha256** in `plugins/manifest.json`. `install_plugins.py`
refuses any file whose hash differs from the reviewed one. The source files are vendored in
`plugins/src/`, each with its upstream repo and commit (or the openwebui.com post id) recorded
in the manifest.

Re-install or update (idempotent):
```sh
docker cp plugins open-webui:/tmp/plugins
docker exec open-webui python3 /tmp/plugins/install_plugins.py /tmp/plugins/manifest.json
```

| Plugin | Type | Scope | Settings applied / notes |
|---|---|---|---|
| Async Context Compression | Filter | global | `summary_model=qwen3.5:9b` (it otherwise falls back to **gpt-4o-mini**!), 64k max context. Creates its own `chat_summary` table. |
| Markdown Normalizer | Filter | global | LaTeX fix and Mermaid fix **off** (the LaTeX fix can corrupt code; Mermaid Doctor owns diagrams). |
| Mermaid Doctor | Filter | global | Few-shot injection **off** (it otherwise adds about 50 lines to every prompt and breaks on image messages). |
| Token Usage Display | Filter | global | models.dev, OpenRouter and balance lookups off, so it makes no outbound calls. |
| Export to Word / Excel | Action | global | Title comes from the chat title (no LLM call). Word export loads mermaid and jszip from public CDNs into the browser; its `latex2mathml==3.81.1` and `mathml2omml==0.0.2` were **pinned locally** (upstream was unpinned). The packages are pip-installed at container start. |
| Download Code Blocks, Regenerate Follow-ups, Pin Message | Action | global | Defaults; follow-up event emitter off. |
| Add Web Sources to Knowledge | Action | global | At most 8 URLs, with confirmation. Server-side fetch goes through Open WebUI's SSRF-safe loader. Saved web content is untrusted. |
| Inline Visualizer (+ Skill `visualize`) | Tool | default-on, all models | `security_level=strict`. **Needs the user setting "iframe Sandbox Allow Same Origin"** (see the warning below). |
| Reveal.js Slide Deck Builder | Tool | default-on, all models | **Needs Allow Same Origin.** Model HTML is inserted unsanitised. |
| Email Composer | Tool | default-on, all models | Safe. No network; sends via mailto or a `.eml` download. |
| Sub Agent | Tool | default-on, all models | Up to 20 iterations, 2 parallel agents, model qwen3.5:9b. Overlaps the built-in `delegate_task`. |
| arXiv Search | Tool | default-on, all models | Queries go to the third-party searchthearxiv.com. |
| Ollama Model Card Generator | Tool | default-on, all models (admin) | Installed **defanged**: every `*_dry_run` valve is on and `hide_base_models=true`. Live, it rewrites model entries and can delete the override that holds qwen3.5:9b's 64k and tool settings. Back up `webui.db` before turning any dry-run off. |

**Skipped:**
- Ask User: duplicates the built-in `ask_user`.
- MCP App Bridge: the MCP server is native and our server has no UI resources.
- Forward to Channel: no Channels.
- Generate Image: no image engine.
- Thinking Toggle: llama.cpp only, and unmaintained.
- OpenRouter tracker: no OpenRouter.
- Forward to Webhook: no webhook target.
- Theme Designer and User Disclaimer: single user.

**Allow Same Origin warning.** With that setting on, HTML the model generates runs in Open
WebUI's origin. It can read your admin token from localStorage and call the API as you. Content
the model saw from a web page can therefore steer it into stealing the session. It is OFF. If
you enable it for Inline Visualizer or Reveal.js, prefer a separate non-admin account.

**Everything is default-on for every model (owner's request, 2026-11-21).** Every filter and action is global. Every model's override has all 7 tools (Homelab MCP + 6 plugin tools) in `toolIds` and the `visualize` skill in `skillIds`. Untick a tool per chat with ➕ if you don't need it. Context cost: the plugin tool schemas add about 4k tokens to every request (Model Card Generator alone is about 2.1k). That is comfortable on qwen3.5:9b (64k) and tighter on the 16k models. Re-apply after adding models:
```sh
docker cp plugins/default_tools_all_models.py open-webui:/tmp/d.py
docker exec -e DEFAULT_SKILL_IDS=visualize open-webui python3 /tmp/d.py server:mcp:homelab inline_visualizer email_composer revealjs_slide_deck_builder ollama_model_card_generator sub_agent arxiv_search
```

**Default tools on every model (earlier, Homelab only).** Each model has an Open WebUI override with
`toolIds = ["server:mcp:homelab"]`, so Homelab Tools is pre-selected in every new chat (you can
still untick it per chat). Re-run `plugins/default_tools_all_models.py` after adding new
models. It only merges, and keeps existing params and filters.

## Upgrading / rollback

The image is pinned (`v0.13.4`). Upgrades run **one-way** DB migrations, so:

```sh
docker run --rm -v open-webui_open-webui:/data:ro -v ~/backups:/b alpine:3.20 \
  tar czf /b/open-webui-data-$(date -u +%Y%m%d-%H%M%S).tgz -C /data .
# edit the image tag in docker-compose.yml, then:
docker compose up -d open-webui
```

Rollback: stop the container, restore the tarball into the volume, and set the image back.
The 0.6.41 → 0.13.4 upgrade on 2026-11-21 left `~/backups/open-webui-data-0.6.41-20261121-030155.tgz`
and the old image tagged `open-webui-rollback:0.6.41`.

## MCP tool server (`mcp-tools/`)

Our own FastMCP server (Streamable HTTP), registered in Open WebUI as **Homelab Tools**
via `TOOL_SERVER_CONNECTIONS`. Enable it per chat (➕ → Tools) or per model (Workspace →
Models → Tools). Execution model adapted from `CoreOutlineV2/dss-agent/tools/mcp_server.py`
minus its financial tools.

| Tool | What it does |
|---|---|
| `run_command`, `run_python` | bash / Python 3.11 in a private per-user workspace (timeout, output cap) |
| `list_workspace`, `read_file`, `write_file` | workspace files (paths confined; symlink escapes rejected) |
| `list_uploaded_files`, `import_uploaded_file` | files attached in Open WebUI chats (fetched with the user's own token) |
| `parse_document` | **LlamaParse** extraction of PDFs/images/Office docs → markdown (saved to `parsed/`) |
| `publish_file` | upload to **s3.ereuna.org/openwebui-files**, return presigned URL + markdown (images also render inline) |
| `web_search_scrapedo`, `fetch_url` | alternative Google search + anti-bot page fetch via **scrape.do** |

### Security model ("only executable through Open WebUI")
dss-agent's server is safe only because it is **stdio** (the spawning process is the only
caller). Over HTTP that guarantee is rebuilt in layers:

1. **Network** — `mcp-tools` publishes no port; only containers on this compose network reach it.
2. **Identity** — Open WebUI uses `auth_type: session`, forwarding the logged-in user's own JWT;
   the server verifies it against Open WebUI's `/api/v1/auths/` on every request (401 otherwise).
3. **Authorisation** — exec tools require a role in `EXEC_ALLOWED_ROLES` (default `admin`).
4. **Containment** — commands run as a per-user UID (not the server's), in a `0700` workspace,
   with a scrubbed env (no API keys/S3 creds), timeout, and container limits (read-only rootfs,
   all caps dropped except SETUID/SETGID/CHOWN/DAC_OVERRIDE/FOWNER/KILL, no-new-privileges,
   4 GB RAM, 4 CPUs, 512 pids). Sandboxed code **does** have internet access by design.
5. **S3** — a dedicated MinIO user `openwebui-mcp` whose policy only allows bucket
   `openwebui-files` (not the MinIO root key). Objects: `<user-id>/YYYY/MM/DD/<rand>-<name>`;
   links expire (default 24 h, max 7 days). No lifecycle rule yet — objects persist.

Tests: `docker run --rm --entrypoint python homelab/mcp-tools:local -m pytest -q test_server.py`.
Rebuild after editing: `docker compose up -d --build mcp-tools`.

## GPU discovery guard (`ollama-gpu-guard.sh`)

Ollama decides GPU vs CPU **once**, at container start, and its probe has a
hard 30 s watchdog. On this VM's slow shared-HDD disk, reading the ~800 MB of
CUDA libraries often takes longer than that, so Ollama silently falls back to
CPU (`ollama ps` → `100% CPU`, ~10x slower) — this happened on the 2026-11-20
boot and also on a plain idle restart. Mitigations:

- `OLLAMA_VULKAN=0` in the compose file skips the Vulkan probe (one fewer 30 s
  window to fail in).
- `ollama-gpu-guard.sh` (installed by `sudo ./install-gpu-guard.sh` as a
  systemd timer: 3 min after boot, then every 15 min) checks the newest
  `inference compute` log line; if it says CPU, it pre-reads the CUDA libs into
  the page cache and restarts the container (up to 3 tries). Logs:
  `journalctl -u ollama-gpu-guard`.
- The real fix is faster storage for VM 101 (an SSD) — see the changelog.

## GPU gotchas (read before troubleshooting a "hang" or "CPU fallback")

These cost a full debugging session to find. In order of how confusing they
are to diagnose:

0. **(2026-11-21) Slow disk → GPU discovery timeout → CPU fallback.** See
   "GPU discovery guard" above. Check with
   `docker logs ollama 2>&1 | grep -a "inference compute" | tail -1` — `library=cpu`
   means this. `nvidia-smi` inside the container will still work fine, which
   is what makes it confusing.

1. **Docker's cgroup driver silently revokes GPU access.** If
   `docker info | grep Cgroup Driver` says `systemd`, then *any*
   `systemctl daemon-reload` on the host (which plenty of unrelated admin
   tasks trigger) rewrites the running containers' device cgroup allowlist
   and strips `/dev/nvidia*` access from them — the container keeps running,
   but `nvidia-smi` inside it starts failing with
   `Failed to initialize NVML: Unknown Error`, and Ollama silently falls back
   to CPU (`docker exec ollama ollama ps` will show `100% CPU` instead of
   `100% GPU`, with no error anywhere). `host-gpu-prep.sh` switches Docker to
   the `cgroupfs` driver, which doesn't have this problem. **If GPU access
   ever goes missing again, check the cgroup driver before anything else.**
2. **NVIDIA persistence mode is off by default.** Without it, CUDA context
   creation after a cold boot/driver reload can take long enough to blow past
   Ollama's internal ~30s GPU-discovery watchdog, which makes Ollama fall
   back to CPU for the exact same reason as above but from a different root
   cause. `host-gpu-prep.sh` fixes this too.
3. **A cold model load is slow and looks like a hang.** Models here load
   with `--no-mmap` (`ollama`'s default scheduling choice when a model should
   be fully GPU-resident), meaning a full read of the model file into RAM
   before it even starts moving layers to VRAM — for `qwen3.5:researcher`
   (6.6 GB) this is **~4 minutes**. During this window `nvidia-smi` shows
   0% utilization (expected — it hasn't started computing yet), which looks
   identical to a genuine hang. `OLLAMA_KEEP_ALIVE=-1` in the compose file
   means you only pay this cost once per container restart, not once per
   5-minute gap in traffic.
4. **The VM needs real RAM, not just VRAM.** `aeacus-dl` was originally sized
   at 6 GB RAM, which is *less than* the model file size (6.6 GB) — when GPU
   discovery failed and Ollama fell back to CPU, it couldn't even fit the
   model in system RAM, causing severe swap thrashing on top of the slowness.
   The VM is now sized at **12 GB** (see
   `../../infrastructure/proxmox/vms/create-vm-101-aeacus-dl.sh`). If you ever
   see `llama-server` stuck in `D` state (uninterruptible sleep,
   `ps -eo pid,stat,cmd`) together with `systemd-journald: Under memory
   pressure, flushing caches` in `dmesg`, this is memory pressure, not a GPU
   problem.
5. **A degraded PCIe link is not itself the problem.** `lspci -vvv` on the
   GPU may show `LnkSta: Speed 2.5GT/s (downgraded)` against a
   `LnkCap: 8GT/s` capability while the GPU is idle — this is normal ASPM
   power-saving and the link renegotiates to full speed under load (measured
   ~12 GB/s host↔device, ~110 GB/s on-GPU via a direct CUDA driver-API probe,
   bypassing Ollama entirely). Don't chase this unless you've also confirmed
   actual throughput is low under load. If the link *won't* come back up
   even under load after several guest power-cycles, a hardware-level reset
   can help (consumer GPUs often don't get a clean reset from a guest-only
   restart): with the VM stopped, `echo 1 > /sys/bus/pci/devices/<addr>/reset`
   on the Proxmox host.
6. **PCIe passthrough topology still matters, just isn't the whole story.**
   `machine: q35` + `hostpci0: ...,pcie=1` (real PCIe root port, not legacy
   i440fx PCI) is the correct, required config for this GPU — see
   `../../infrastructure/proxmox/pci-passthrough.md`. But on its own it did
   **not** fix the inference hang in this homelab's case; it took finding and
   fixing #1 and #4 above as well. Don't assume fixing the PCIe topology
   alone will resolve a passthrough compute hang.

### Diagnosing "is this actually a GPU problem?"

The fastest way to rule the GPU in or out, bypassing Ollama/llama.cpp
entirely, is to call the CUDA driver API directly via `ctypes` against
`/usr/lib/x86_64-linux-gnu/libcuda.so.1` (`cuInit` → `cuDeviceGet` →
`cuCtxCreate` → `cuMemAlloc`/`cuMemcpyHtoD`/`cuMemcpyDtoD` → timings). If that
completes quickly with real bandwidth numbers, the passthrough is fine and
the problem is in Ollama's GPU-discovery subprocess, Docker's GPU exposure to
the container, or VM memory — not VFIO/IOMMU.

## Deployment path

As of 2026-11-07, the live stack runs directly from this repo checkout on
`aeacus-dl` — `cd ~/home-lab/homelab/ai/open-webui && docker compose up -d`.
It previously ran from an ad-hoc `~/open-webui/` directory outside any git
repo (migrated here; the old directory is preserved as
`~/open-webui.superseded-by-home-lab-repo-20261107` in case anything was
missed, safe to delete once confirmed). `.env` lives alongside the compose
file in this checkout (gitignored via `**/.env` — never commit it).

**Editing config now takes effect directly**: `git pull` on `aeacus-dl`, then
`docker compose up -d` in this directory to apply. No more copying changes
between two locations.

## Known limits / not yet done
- No image digest pinning on `litellm`/`open-webui` (`:main-stable`/`:main`)
  — a `docker compose pull` could change behavior under you. Only `ollama` is
  pinned (`0.30.10`), because a newer Ollama version was one of several
  things tested (and ruled out) while debugging the GPU hang above.
- Open WebUI's Anthropic connection config lives in its own SQLite
  `webui.db` (`config` table), not in this compose file or `.env` — it's set
  once through the admin UI and isn't captured as code here.

## Which model for long-range work (benchmark, 2026-11-23)

Short version: use **Claude Haiku 5.5 - Agent (long tasks)** (`claude-haiku-agent`) for research, coding with test loops and reporting; use `qwen3.5-9b-agent` for quick local lookups. Measured with `bench/` (8 graded tasks through the real API): the Haiku agent passed T1-T7; qwen3.5:9b passed only the single-tool lookups (3/8) and thrashes on code. Details, bug list and caveats: `docs/changelog/231126-004500_agent_model_benchmark_changelog.md`.

Operational rules learned: keep the **skills builtin off** on agent models; attach only the tool servers a chat needs (eight at once stalls the first turn); do not pin the 9B in Ollama on the 12 GB VM (swap thrash); after an Ollama hang, `docker restart ollama` and warm the model with a long-timeout request (cold load from the shared HDD is slow). To rerun: `bench/make_variants.py` then `bench/run_bench.py --model <id> --label <name> [--packs]` inside the open-webui container; `bench/summarize.py` prints the table.
