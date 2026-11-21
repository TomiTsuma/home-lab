"""Register Chlio's MCP server in Open WebUI (run INSIDE the open-webui container).

    <token on stdin> | docker exec -i open-webui python3 /tmp/c.py

The Chlio access token (chlio_pat_...) is read from the first line of stdin, so it never appears in a
command line or in output. It is stored in Open WebUI's config DB as the connection's bearer key.

  * adds/updates the connection `chlio` (type mcp, auth bearer) -> $CHLIO_MCP_URL
    (default https://chlio.ereuna.org/mcp; add ?groups=mindmaps,planner,... to limit the tool set),
  * enables it by default on the models matching $CHLIO_MODELS (comma-separated fnmatch patterns).
    Default: qwen3.5:9b (64k context) and the Claude models. The 16k-context local models are skipped on
    purpose: the tool schemas of Chlio (~4.2k tokens) on top of the other default tools would leave them no
    room to answer. Re-run with CHLIO_MODELS='*' to enable it everywhere.
Idempotent; keeps every other tool server and model setting.
"""
import fnmatch
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import jwt

URL = os.environ.get("CHLIO_MCP_URL", "https://chlio.ereuna.org/mcp")
PATTERNS = [p.strip() for p in os.environ.get("CHLIO_MODELS", "qwen3.5:9b,claude-*").split(",") if p.strip()]
TOKEN_IN = sys.stdin.readline().strip()
if not TOKEN_IN.startswith("chlio_pat_"):
    sys.exit("expected a chlio_pat_... token on the first line of stdin")

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


conn = {
    "url": URL, "path": "", "type": "mcp", "auth_type": "bearer", "key": TOKEN_IN,
    "headers": None, "forward_cookies": False, "config": {"enable": True},
    "info": {"id": "chlio", "name": "Chlio", "description": "Chlio: mind maps, tasks, calendar, notes, Jira (via MCP)"},
}
current = (call("GET", "/api/v1/configs/tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
res = call("POST", "/api/v1/configs/tool_servers",
           {"TOOL_SERVER_CONNECTIONS": [c for c in current if (c.get("info") or {}).get("id") != "chlio"] + [conn]})
if not res or "HTTP" in res:
    sys.exit(f"saving the tool server failed: {res}")
print("tool servers:", [(c.get("info") or {}).get("id") for c in res["TOOL_SERVER_CONNECTIONS"]])

models = call("GET", "/api/models")
for m in models.get("data", models):
    mid = m["id"]
    if m.get("pipe") or m.get("arena"):
        continue
    ov = call("GET", f"/api/v1/models/model?id={mid}")
    if not ov or not ov.get("id"):
        continue
    meta = ov.get("meta") or {}
    ids = set(meta.get("toolIds") or [])
    want = any(fnmatch.fnmatch(mid, p) for p in PATTERNS)
    new = (ids | {"server:mcp:chlio"}) if want else (ids - {"server:mcp:chlio"})
    if new == ids:
        print(f"unchanged {mid} ({'on' if want else 'off'})")
        continue
    meta["toolIds"] = sorted(new)
    r = call("POST", "/api/v1/models/model/update", {
        "id": ov["id"], "base_model_id": ov.get("base_model_id"), "name": ov["name"], "meta": meta,
        "params": ov.get("params") or {}, "is_active": ov.get("is_active", True),
        "commit_message": f"Chlio MCP {'on' if want else 'off'}"})
    print(f"{'enabled' if want else 'disabled'} on {mid}: {'ok' if r and 'HTTP' not in r else r}")
