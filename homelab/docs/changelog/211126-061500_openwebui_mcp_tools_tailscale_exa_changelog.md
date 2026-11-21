# Changelog — Open WebUI: MCP tool server, LlamaParse, S3 publishing, Exa, Tailscale-only, upgrade

**Date:** 2026-11-21 (same day as `211126-051000_ollama_gpu_guard_and_web_search_changelog.md`)
**Host:** `aeacus-dl` (VM 101), stack `homelab/ai/open-webui/`; S3 on `aeacus-data-handler` (VM 100)

## 1. MCP tool server `mcp-tools/` — NEW

Requested: give Open WebUI the command-execution capability of
`CoreOutlineV2/dss-agent/tools/mcp_server.py` (excluding its financial tools), LlamaParse
extraction for PDFs/images, and publishing generated documents to `s3.ereuna.org` bucket
`openwebui-files` with a download URL — executable **only** through Open WebUI.

### How dss-agent's server executes commands (reviewed, not modified)
- `run_command` / `exec` / `process`: `subprocess.run(["/bin/bash","-c",cmd])` (or PowerShell),
  `Popen(shell=True)` for background; `python_execute` / `code_execution` / `node_execution`
  write code to a temp file and run it.
- cwd pinned to a per-session workspace (`SESSION_CONTEXT`); writes confined
  (`_confine_write_path`), reads are not.
- No allowlist, no timeout, no output cap; commands inherit the server's full environment.
- No auth in the server: it is safe only because it runs over **stdio** (`mcp.run()`), so the
  spawning process (Nyx gateway) is the only possible caller.
- Documents: generated in the workspace, then `upload_file_to_minio` →
  `fput_object(bucket, "reports/{date}/{name}")` → 2 h presigned URL.

### What was built (adapted, hardened for an HTTP transport)
| dss-agent | mcp-tools |
|---|---|
| stdio = implicit single caller | Streamable HTTP, **no published port** + every request must carry a valid **Open WebUI session JWT** (`auth_type: session`), verified against `open-webui:8080/api/v1/auths/` |
| commands run as server user, full env | per-user UID (sha256(user id) → 20000–49999), `0700` workspace, scrubbed env (verified: `/proc/1/environ` → Permission denied; 0 secret vars visible) |
| no timeout | timeout (default 120 s, max 900 s) kills the whole process group; output capped at 30k chars |
| reads unconfined | reads + writes confined; `..` and symlink escapes rejected (verified) |
| MinIO root/tenant creds | dedicated MinIO user `openwebui-mcp`, policy `openwebui-files-rw` = bucket `openwebui-files` only |
| 2 h presigned | default 24 h, max 7 d; returns `markdown` (images `![]()`, files `[Download]()`); images ≤4 MB also returned as MCP image content → rendered inline by Open WebUI |
| — | `parse_document` (LlamaParse REST: upload → poll → markdown), source = Open WebUI file id / workspace path / URL |
| — | `list_uploaded_files` / `import_uploaded_file` using the caller's own token |
| — | `web_search_scrapedo`, `fetch_url` (scrape.do) |

Container hardening: `read_only`, `cap_drop: ALL` + `SETUID,SETGID,CHOWN,DAC_OVERRIDE,FOWNER,KILL`,
`no-new-privileges`, 4 GB / 4 CPU / 512 pids, exec tools limited to role `admin`.

### Verification
- 13 unit tests (`mcp-tools/test_server.py`): path confinement incl. symlinks, UID mapping, S3 key
  sanitising, scrape.do normalisation, auth middleware (401 / cache), role gate, timeout + clean env.
- End-to-end through **Open WebUI's own `MCPClient`** with an admin session token: no-token 401,
  forged-token 401, 11 tools listed, run_command (uid 41579), run_python (matplotlib/docx/reportlab),
  escape blocked, publish docx → presigned GET 200 (36,632 bytes), publish png → text+image content,
  LlamaParse extracted the test invoice PDF, scrape.do search + fetch OK, uploads listed.
- Fix found during e2e: slim image has no `/etc/mime.types` → Office files were `octet-stream`;
  registered Office/markdown/csv/webp types.

### S3 setup (aeacus-data-handler)
Bucket `openwebui-files` + user `openwebui-mcp` + policy `openwebui-files-rw` created via the MinIO
admin API from a throwaway `python:3.11-slim` container (host has no `python3-venv`; `mc` is no
longer freely distributable). Credentials were piped straight into aeacus-dl's `.env` (never printed).
**No lifecycle/expiry rule** on the bucket yet — objects persist after links expire.

## 2. Web search: Exa (replaces SearXNG from earlier today)
`WEB_SEARCH_ENGINE=exa`, `EXA_API_KEY` in `.env`. `searxng` service + config removed (owner's
choice). scrape.do available as the alternative search via MCP. Note: scrape.do's Google results
often contain opaque `google.com/goto` links that do not redirect — returned as `link: null`.

## 3. Open WebUI reachable only on Tailscale
- Port bound to `127.0.0.1:3005` (was `0.0.0.0:3005`).
- `tailscale serve --bg --https=443 http://127.0.0.1:3005` and `--http=3005` (persisted by tailscaled).
- Verified: laptop → `https://aeacus-dl.tailaee7b8.ts.net` 200, `http://aeacus-dl.tailaee7b8.ts.net:3005`
  200; pve over LAN → `192.168.100.17:3005` refused, `:8000` (mcp-tools) refused.
- **Behaviour change:** the bare-IP URL `http://100.101.189.31:3005` now returns 404 (serve routes
  by hostname). Use the hostname. All traffic in the prior 72 h was already from tailnet IPs.
- `WEBUI_SECRET_KEY` pinned in `.env` (reused the existing key → no logout); previously it lived
  in the container filesystem and was regenerated on every recreate.
- Not changed: `ollama :11434` (Chlio on minos uses it over LAN) and `litellm :4000` are still
  LAN-published — recommend binding litellm to 127.0.0.1 if nothing else uses it.

### 3a. Follow-up: LAN and tailnet-IP access restored (owner's request)
- The owner asked for Open WebUI to be reachable at `http://100.101.189.31:3005` and on the
  LAN as well, so the port is back to `"3005:8080"` (all interfaces).
- Removed `tailscale serve --http=3005`. Serve routes by hostname, so that rule caught
  requests to the tailnet IP on port 3005 and returned 404.
- Kept `tailscale serve --https=443` for HTTPS on the tailnet.
- Verified:
  - `100.101.189.31:3005` → 200
  - `192.168.100.17:3005` from pve → 200
  - `https://aeacus-dl.tailaee7b8.ts.net` → 200
  - `mcp-tools :8000` from the LAN → still refused

## 4. Upgrade 0.6.41 → 0.13.4 (memory + referencing other chats) — DONE

Requested: Open WebUI should generate memory as it goes and reference other chats. 0.6.41 only
had manual memories; the built-in memory and chat tools (`add_memory`, `search_memories`,
`search_chats`, `view_chat`, …) arrived in 0.7.0+, and native function calling is the default
since 0.10. The owner chose to upgrade to the latest, 0.13.4.

- Backup: `~/backups/open-webui-data-0.6.41-20261121-030155.tgz` (981 MB); old image tagged
  `open-webui-rollback:0.6.41`.
- Image pinned to `ghcr.io/open-webui/open-webui:v0.13.4` (was the floating `:main`).
- Env: `ENABLE_MEMORIES=true`, `ENABLE_MEMORY_BACKGROUND_REVIEW=true`,
  `MEMORIES_REVIEW_INTERVAL_TURNS=6`.
- Migrations: alembic ran cleanly through `9f6c2a8d1b43`; container healthy.
- Re-verified on 0.13.4: `version 0.13.4`, `enable_memories: true`, web engine `exa` (Exa returned
  5 results in 3.6 s), the MCP server is registered with `auth_type: session`, and a session
  JWT validates at `/api/v1/auths/`. MCP e2e: 11 tools; run_python → xlsx → publish_file →
  presigned GET 200 with the correct xlsx MIME type.
- Known slowness: the legacy `/api/v1/retrieval/process/web/search` path embeds fetched pages
  with a CPU sentence-transformer, which took more than 180 s on this disk. Chats using native
  tool calling go through the built-in `search_web` tool instead. If it's still slow, try
  `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=true`.

## 4b. "Responses randomly cut off" with qwen3.5:9b — FIXED

**Root cause.** The Ollama log showed context exhaustion, not a crash or a GPU-guard restart
(the guard reported "OK" every 15 min; ollama restarts = 0). In one chat, each step of a native
tool loop (5 scrape.do searches plus a fetch, with outputs of 3–6k characters each) re-sent a
growing prompt: 10,680 → 12,118 → 13,246 → 14,711 → 15,809 → 16,364 → 16,379 tokens against
`n_ctx = 16384`. The log then shows `stop processing: n_tokens = 16383, truncated = 1`. The answer
had no room, and the assistant message was saved with empty content.

**Fixes**
1. **64k context.** qwen3.5:9b now defaults to a 64k context inside Ollama (re-tagged with
   `PARAMETER num_ctx 65536`). Measured: 7.7 GB, 100% GPU. Other models keep 16k.
2. **Model override.** Open WebUI now has an override for qwen3.5:9b: native function calling,
   and the built-in tool groups trimmed to chats, memory, web_search, files and time.
3. **Smaller MCP tool output defaults.**
   - fetch_url: 20k → 6k chars
   - parse_document: 40k → 16k (the full text is still saved)
   - read_file: 50k → 12k
   - command output: 30k → 12k
   - scrape.do results: 8 → 5
   - Tool descriptions now nudge the model to answer after 1–2 searches.
4. **Verified.**
   - A fresh qwen3.5:9b request with Homelab Tools and web search enabled now starts at
     1,475 prompt tokens in a 65,536 window, at 45 tok/s.
   - 13/13 unit tests pass.
   - The multi-step loop as the browser UI runs it was not exercised here; that is left for
     the owner to confirm.

## 5a. Community plugins — INSTALLED (17 items), Homelab Tools default on all models

**Plugins**
- **Review:**
  - Three review passes read the plugins (each pass also checked its main claims against the
    code), followed by an independent check: sha256 re-verified and a grep for dangerous patterns.
  - The grep found only two JavaScript `regex.exec` false positives.
  - Outbound hosts: cdnjs, jsDelivr, unpkg, arxiv.org and searchthearxiv.com.
    models.dev and openrouter.ai are off.
- **Install:**
  - Done with `plugins/install_plugins.py`, against a sha256-pinned `plugins/manifest.json`.
  - Sources are vendored in `plugins/src/`.
  - Every valve name matched the plugin's spec.
  - Database backup before installing: `webui.db.pre-plugins-20261121-040609`, in the
    open-webui volume.
- **Local patch:** Export to Word's pip requirements were pinned
  (`latex2mathml==3.81.1`, `mathml2omml==0.0.2`). The installs succeeded and the packages import.
- **Scope:**
  - Filters, global: Markdown Normalizer, Mermaid Doctor, Token Usage Display.
  - Filter, qwen3.5:9b only: Async Context Compression.
  - Actions, global: Word, Excel, Code Blocks, Follow-ups, Pin, Web Sources→Knowledge.
  - Tools, per chat: Inline Visualizer + Skill, Reveal.js, Email Composer, Sub Agent, arXiv,
    and Model Card Generator in dry-run.
- **Safety-relevant valves:**
  - The compression summary model is pinned to local qwen3.5:9b; it otherwise defaults to
    gpt-4o-mini.
  - Every Model Card Generator `*_dry_run` valve is on.
  - "iframe Sandbox Allow Same Origin" was deliberately **not** enabled; it is the owner's
    decision (see README).
- **Skipped (with reasons in README):** Ask User (built in), MCP App Bridge, Forward to
  Channel, Generate Image, Thinking Toggle, OpenRouter tracker, Forward to Webhook,
  Theme Designer, User Disclaimer.

**Homelab Tools default on**
- `plugins/default_tools_all_models.py` created overrides for 8 models and merged into
  qwen3.5:9b's override.
- All 9 models now have `toolIds=["server:mcp:homelab"]`.
- qwen3.5:9b kept `function_calling=native`, the built-in tool trim and the compression filter.

**Verified after the change**
- claude-haiku-5-5 routes OK.
- gemma4 failed: `timed out waiting for llama-server to start`. That is the 5-minute default
  `OLLAMA_LOAD_TIMEOUT` against a cold load from the slow disk.
  - Fix: `OLLAMA_LOAD_TIMEOUT=15m`.
  - Ollama was recreated with the CUDA libraries pre-warmed and came up on CUDA.
  - qwen3.5:9b was reloaded.

## 5e. Google Workspace switched to SELF-HOSTED `workspace-mcp` (hosted servers denied tool calls)

**Diagnosis of the hosted servers**
- All 4 OAuth sign-ins succeeded, and the token is valid with `drive.readonly`.
- The Drive REST API `files.list` worked with the same token.
- `drivemcp` `tools/list` worked, but every `tools/call` returned
  `isError: "The caller does not have permission"`.
- Google's guide makes Developer Preview membership a prerequisite, along with the "Use this
  client for an AI-powered agent" setting on the OAuth client.
- The account is personal Gmail, so the owner asked for a local server instead.

**Built**
- Service `google-workspace-mcp`: `workspace-mcp==2.6.0`, non-root uid 10001, read-only rootfs,
  `cap_drop: ALL`, no-new-privileges, 1 GB RAM limit.
- Flags: `--single-user --read-only --tool-tier core --tools drive docs sheets slides`.
- Streamable HTTP on `0.0.0.0:8000`. The default bind was 127.0.0.1, so open-webui couldn't
  reach it; this is fixed with `WORKSPACE_MCP_HOST`.
- Outbound hosts in the source are Google only.

**Isolation**
- A dedicated `gws` network, shared only with open-webui.
- Host publish `127.0.0.1:8001`.
- `tailscale serve --https=443 --set-path /oauth2callback` exposes only the callback path.
- Verified: the LAN gets 000 on `:8001`. The sandbox fails DNS for the service and is blocked
  via both Docker gateway addresses. `/oauth2callback` returns 400 without OAuth parameters.
  `/mcp` and `/health` on the Tailscale HTTPS address stay with Open WebUI.

**Open WebUI**
- Removed the `gdrive`, `gdocs`, `gsheets` and `gslides` connections and added `gworkspace`
  (auth none).
- On all 9 models, `toolIds` now has `server:mcp:gworkspace` in place of the 4 hosted ids.
- Script: `google-workspace-mcp/register_in_openwebui.py`.

**Context budget**
- The complete read-only tier was 23 tools at about 9.2k tokens, too much for the 16k models
  with everything default-on.
- Switched to `--tool-tier core`: 7 tools at about 3.9k tokens.

**Open WebUI side effect.** Recreating open-webui to join the new network took about 6 minutes
before it was healthy: a fresh container re-downloads the embedding model and pip-reinstalls the
plugins' frontmatter requirements.

**Signed in and tested (DONE).** The owner completed the sign-in. Credentials are stored as
`/data/credentials/tommytsuma7@gmail.com.json` (mode 0600, uid gws). Live test through Open
WebUI's MCPClient:
- `search_drive_files` for Docs, Sheets and Slides: 3 results each.
- `get_doc_content`: OK.
- `read_sheet_values` A1:D5: 5 rows.
- `get_presentation`: OK.

**Known issue.** `get_doc_content` returned the whole document (181k chars, about 45k tokens)
for a long proposal. One such result overflows the 16k-context models and uses most of
qwen3.5:9b's 64k. The server has no output cap of its own; see the README for mitigations.

## 5d. Google Drive/Docs/Sheets/Slides via Google-hosted MCP servers

The owner chose option B: Workspace tools that read and act on files, not just the file picker.
Instead of self-hosting `taylorwilsdon/google_workspace_mcp`, these use **Google's own hosted
MCP servers**.
- The owner had enabled the "MCP API" for each product in Google Cloud.
- Probed live: `https://{drive,docs,sheets,slides}mcp.googleapis.com/mcp/v1`.
- Each publishes RFC 9728 protected-resource metadata with `authorization_servers=accounts.google.com`
  plus read-only and read-write scopes.
- Google has no `registration_endpoint` and supports PKCE S256.

Why hosted rather than self-hosted:
- There is no container to run or patch.
- mcp-tools' sandbox has internet access and shares the Docker network with Open WebUI. A
  self-hosted Workspace server on that network would have let sandboxed code reach the Google
  account. A Google-hosted server needs the user's OAuth token, which only Open WebUI holds.

Setup:
- `google-mcp/connect_google_mcp.py` mirrors the admin UI's two steps:
  1. `POST /api/v1/configs/oauth/clients/register?type=mcp` with static credentials.
  2. Upsert of `tool_server.connections` as type `mcp`, auth `oauth_2.1_static`.
- The OAuth client ID and secret are in `.env` and in Open WebUI's config DB, not in git.
- Connections `gdrive`, `gdocs`, `gsheets` and `gslides` are read-only for now.
- The `TOOL_SERVER_CONNECTIONS` env var in compose is now superseded by the DB value (comment added).

Verified:
- Each server's authorize redirect goes to `accounts.google.com/o/oauth2/v2/auth` with the right
  client ID, redirect URI `https://aeacus-dl.tailaee7b8.ts.net/oauth/clients/mcp:<id>/callback`,
  read-only scopes, PKCE S256, `access_type=offline` and `prompt=consent`.
- All 9 models have `server:mcp:g*` in their default `toolIds`.

Pending:
- The owner's first sign-in per server, and a live read test per service.
- Measuring the tool-schema size once the servers are connected.

## 5c. All tools/plugins default-on for all models (owner's request)

- **Tools:** all 9 models now have `toolIds` = Homelab MCP plus inline_visualizer, email_composer,
  revealjs_slide_deck_builder, ollama_model_card_generator, sub_agent and arxiv_search, and
  `skillIds = [visualize]`.
- **Filters and actions:** Async Context Compression switched from qwen3.5:9b-only to global, so
  all 10 functions are now active and global.
- **Pre-checks:**
  - All 5 local models report the Ollama `tools` capability, so no model rejects tool requests.
  - The plugin tool schemas total about 4k tokens per request.

## 5b. "Failed to connect to MCP server 'homelab'" — FIXED (timeout)

**Cause**
- Open WebUI 0.13.4 ships MCP SDK **2.3.0**. Its client first sends a `server/discover` probe
  at protocol `2026-07-28`.
- mcp-tools uses SDK 1.30 / FastMCP 2.14, which supports protocols up to 2025-11-25. It
  rejects the probe with a 400, and the client falls back to the legacy `initialize` handshake.
- Auth was fine: `/api/v1/auths/` returned 200.
- At 04:47 the probe plus the fallback ran past Open WebUI's 10 s `MCP_INITIALIZE_TIMEOUT`
  (`Request 'initialize' timed out`). The VM disk was saturated at the time by a qwen3.5:9b
  cold reload.
- On an idle VM, Open WebUI's own `MCPClient` connects in 0.1 s and lists 11 tools.

**Fix**
- `MCP_INITIALIZE_TIMEOUT=30` on open-webui.
- Re-verified with `MCPClient`: connected, 11 tools.

**Follow-up (recommended, not done)**
- Migrate mcp-tools to FastMCP 4.x / MCP SDK 2.x so it speaks the stateless protocol natively.
  That removes the 400 and the fallback round trip.
- It is a major-version migration (the `http_app` and `get_http_request` APIs may have
  changed), so it is to be done as its own tested change.

## 5. Community plugins (researched, none installed)
Most featured plugins need 0.9 or later, so that is now possible after the upgrade. Shortlist:
- Async Context Compression (Fu-Jie): keeps long chats inside the context window of small local models.
- Markdown Normalizer (Fu-Jie)
- Mermaid Doctor (h4nn1b4l)
- arXiv Search (Haervwe): queries go to the third-party searchthearxiv.com.
- Inline Visualizer (needs 0.10.2)

The third-party Memory filter is redundant now that memory is built in. Plugins run arbitrary
Python inside Open WebUI, so read each one's code before installing.

## Secrets
All keys live only in `aeacus-dl:~/home-lab/homelab/ai/open-webui/.env` (chmod 600, gitignored):
`WEBUI_SECRET_KEY, EXA_API_KEY, LLAMA_CLOUD_API_KEY, SCRAPEDO_TOKEN, S3_ACCESS_KEY, S3_SECRET_KEY`.
The Exa, LlamaParse and scrape.do keys and the sudo password were pasted in a chat session —
rotate them if that transcript is not considered private.
