"""Register the self-hosted google-workspace-mcp in Open WebUI (run INSIDE the open-webui container).

  * removes the Google-HOSTED connections gdrive/gdocs/gsheets/gslides (their tool calls fail with
    "The caller does not have permission" without Workspace Developer Preview enrollment),
  * adds/updates connection `gworkspace` -> http://google-workspace-mcp:8000/mcp (auth none: the
    server is only reachable on the private `gws` Docker network shared with open-webui),
  * on every model: drops server:mcp:g{drive,docs,sheets,slides} and adds server:mcp:gworkspace to the
    default toolIds (other tools/skills/params untouched).
Idempotent.
"""
import json, os, sqlite3, time, urllib.error, urllib.request
import jwt

OLD = ["gdrive", "gdocs", "gsheets", "gslides"]
NEW = {
    "url": "http://google-workspace-mcp:8000/mcp", "path": "", "type": "mcp", "auth_type": "none",
    "key": "", "headers": None, "forward_cookies": False, "config": {"enable": True},
    "info": {"id": "gworkspace", "name": "Google Drive, Docs, Sheets & Slides",
             "description": "Google Drive, Docs, Sheets and Slides (read-only) via self-hosted workspace-mcp"},
}

uid = sqlite3.connect("/app/backend/data/webui.db").execute(
    "select id from user where role='admin' order by created_at limit 1").fetchone()[0]
TOKEN = jwt.encode({"id": uid, "exp": int(time.time()) + 600}, os.environ["WEBUI_SECRET_KEY"], algorithm="HS256")


def call(method, path, body=None):
    req = urllib.request.Request("http://localhost:8080" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": e.read()[:300].decode(errors="replace")}


conns = (call("GET", "/api/v1/configs/tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
conns = [c for c in conns if (c.get("info") or {}).get("id") not in OLD + ["gworkspace"]] + [NEW]
res = call("POST", "/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": conns})
print("tool servers:", [(c.get("info") or {}).get("id") for c in (res or {}).get("TOOL_SERVER_CONNECTIONS", [])] or res)

drop = {f"server:mcp:{i}" for i in OLD}
models = call("GET", "/api/models")
for m in models.get("data", models):
    ov = call("GET", f"/api/v1/models/model?id={m['id']}")
    if not ov or not ov.get("id"):
        continue
    meta = ov.get("meta") or {}
    tools = (set(meta.get("toolIds") or []) - drop) | {"server:mcp:gworkspace"}
    if sorted(tools) == sorted(meta.get("toolIds") or []):
        print(f"unchanged {m['id']}"); continue
    meta["toolIds"] = sorted(tools)
    r = call("POST", "/api/v1/models/model/update", {
        "id": ov["id"], "base_model_id": ov.get("base_model_id"), "name": ov["name"], "meta": meta,
        "params": ov.get("params") or {}, "is_active": ov.get("is_active", True),
        "commit_message": "Google: hosted gdrive/gdocs/gsheets/gslides -> self-hosted gworkspace"})
    print(f"updated {m['id']}: {'ok' if r and 'HTTP' not in r else r}")
