"""Register the dagster-mcp server in Open WebUI (run INSIDE the open-webui container).

    docker cp dagster-mcp/register_in_openwebui.py open-webui:/tmp/register_dagster.py
    docker exec -e DAGSTER_MCP_AUTH_TOKEN="$(grep ^DAGSTER_MCP_AUTH_TOKEN= .env | cut -d= -f2-)" \
        open-webui python3 /tmp/register_dagster.py

  * adds/updates the MCP connection `dagster` -> http://dagster-mcp:8000/mcp with bearer auth
    (the token is passed in the environment and is stored only in Open WebUI's config DB, never in the repo),
  * adds server:mcp:dagster to the default toolIds of the models named in DAGSTER_MCP_MODELS (comma list, default: every model).
Idempotent.
"""
import json, os, sqlite3, sys, time, urllib.error, urllib.request
import jwt

TOKEN_FOR_SERVER = os.environ.get("DAGSTER_MCP_AUTH_TOKEN", "")
if len(TOKEN_FOR_SERVER) < 32:
    sys.exit("DAGSTER_MCP_AUTH_TOKEN missing or too short (needs the same value the dagster-mcp container runs with)")

CONN = {
    "url": "http://dagster-mcp:8000/mcp", "path": "", "type": "mcp", "auth_type": "bearer",
    "key": TOKEN_FOR_SERVER, "headers": None, "forward_cookies": False, "config": {"enable": True},
    "info": {"id": "dagster", "name": "Dagster",
             "description": "Read-only Dagster: runs, assets, jobs, schedules, sensors, backfills, code locations (dagster-mcp)"},
}

uid = sqlite3.connect("/app/backend/data/webui.db").execute(
    "select id from user where role='admin' order by created_at limit 1").fetchone()[0]
JWT = jwt.encode({"id": uid, "exp": int(time.time()) + 600}, os.environ["WEBUI_SECRET_KEY"], algorithm="HS256")


def call(method, path, body=None):
    req = urllib.request.Request("http://localhost:8080" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {JWT}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": e.read()[:300].decode(errors="replace")}


conns = (call("GET", "/api/v1/configs/tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
conns = [c for c in conns if (c.get("info") or {}).get("id") != "dagster"] + [CONN]
res = call("POST", "/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": conns})
print("tool servers:", [(c.get("info") or {}).get("id") for c in (res or {}).get("TOOL_SERVER_CONNECTIONS", [])] or res)

ONLY = {x.strip() for x in os.environ.get("DAGSTER_MCP_MODELS", "").split(",") if x.strip()}
models = call("GET", "/api/models")
for m in models.get("data", models):
    if m.get("pipe") or m.get("arena") or str(m["id"]).startswith("arena"):
        continue
    if ONLY and m["id"] not in ONLY:
        continue
    ov = call("GET", f"/api/v1/models/model?id={m['id']}")
    if not ov or not ov.get("id"):
        continue
    meta = ov.get("meta") or {}
    tools = set(meta.get("toolIds") or []) | {"server:mcp:dagster"}
    if sorted(tools) == sorted(meta.get("toolIds") or []):
        print(f"unchanged {m['id']}"); continue
    meta["toolIds"] = sorted(tools)
    r = call("POST", "/api/v1/models/model/update", {
        "id": ov["id"], "base_model_id": ov.get("base_model_id"), "name": ov["name"], "meta": meta,
        "params": ov.get("params") or {}, "is_active": ov.get("is_active", True),
        "commit_message": "default tools += server:mcp:dagster"})
    print(f"updated {m['id']}: {'ok' if r and 'HTTP' not in r else r}")
