# Dagster MCP for Open WebUI (2026-11-22)

- New `ai/open-webui/dagster-mcp/` (Dockerfile, `serve_http.py`, `register_in_openwebui.py`) and compose service `dagster-mcp` on a new
  private network `dgmcp` shared only with open-webui. Installed **without a Dagster target** (owner to supply details later).
- Package: community `dagster-mcp` 0.12.3 (MIT, fabdendev, 2026-09-28). Source read in full before use: only outbound call is the GraphQL POST
  to `DAGSTER_URL`, no telemetry. Build aborts unless the wheel sha256 equals the reviewed `f9b9c6ee...5e96` (verified: OK at build).
- Read-only: `DAGSTER_READ_ONLY=true` means the 10 write tools are not registered at all. 17 read tools, about 5.2k tokens.
- `serve_http.py`: dagster-mcp only does stdio, so our launcher serves it over streamable HTTP with bearer-token (401) and Host allow-list (421).
- Token generated on aeacus-dl into the mode-600 `.env`; never in the repo. `.env.example` documents DAGSTER_* variables.
- Registered as `dagster`, default-on for qwen3.5:9b, qwen3.5:researcher and the 4 Claude models only (16k models would overflow).
- Tested via Open WebUI's network path: 401 / 421 / 200, 17 tools listed, `/health` ok, unconfigured call says "Cannot connect to Dagster at
  http://localhost:3000". **Not tested against a real Dagster.**
- Alternatives considered: Dagster+ hosted MCP (cloud only), TEAMSchools/dagster-plus-mcp (Dagster+ only, Python 3.13), mcp-server-dagster (SSE, older).
- Rollback: `docker compose rm -sf dagster-mcp`, delete the `dagster` connection in Open WebUI and `server:mcp:dagster` from model tool lists.

## Update: both MCPs connected to the emergency instance (2026-11-22)
- ClickHouse MCP -> `100.127.65.29:18123` (Tailscale, HTTP, no TLS, `CLICKHOUSE_SECURE=false`), db `atlas_analytics`, user `toonaweza`;
  Dagster MCP -> `http://100.127.65.29:13000` (no auth). Values were put in aeacus-dl `.env` (mode 600); containers recreated, both healthy.
- Verified through Open WebUI's network: ClickHouse `list_databases`, `list_tables`, `SELECT count()` (stg_facebook_post = 9 rows),
  session `readonly=1`, server 24.10.2.80; Dagster `get_instance_status` healthy, 5 code locations, 5 schedules RUNNING. No write was attempted on
  either instance.
- Correction of an earlier note: ClickHouse does run on VM 100 (24.10.2.80); only the `latest` image crashed ("Illegal instruction").
- Temporary instance: when atlas-orchestrator is back, repoint the `CLICKHOUSE_*` / `DAGSTER_URL` values and retire these credentials.
