"""Make tools (and skills) default-on for EVERY model.

    python3 default_tools_all_models.py [tool_id ...]      # default: server:mcp:homelab
    DEFAULT_SKILL_IDS=visualize python3 default_tools_all_models.py ...

Run inside the open-webui container. Creates an Open WebUI model override for base models that
don't have one yet (name/params untouched), and merges into existing overrides (keeps their
params, builtinTools, filterIds, etc.). Idempotent.
"""
import json, os, sqlite3, sys, time, urllib.error, urllib.request
import jwt

TOOL_IDS = sys.argv[1:] or ["server:mcp:homelab"]
SKILL_IDS = [x for x in os.environ.get("DEFAULT_SKILL_IDS", "").split(",") if x]
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


models = call("GET", "/api/models")
models = models.get("data", models) if isinstance(models, dict) else models
for m in models:
    mid = m["id"]
    if m.get("pipe") or m.get("arena") or mid.startswith("arena"):
        print(f"skip {mid} (pipe/arena)"); continue
    ov = call("GET", f"/api/v1/models/model?id={mid}")
    if ov and ov.get("id"):
        meta = ov.get("meta") or {}
        before = (sorted(meta.get("toolIds") or []), sorted(meta.get("skillIds") or []))
        meta["toolIds"] = sorted(set(before[0]) | set(TOOL_IDS))
        meta["skillIds"] = sorted(set(before[1]) | set(SKILL_IDS))
        if (meta["toolIds"], meta["skillIds"]) == before:
            print(f"unchanged {mid}"); continue
        form = {"id": mid, "base_model_id": ov.get("base_model_id"), "name": ov["name"], "meta": meta,
                "params": ov.get("params") or {}, "is_active": ov.get("is_active", True),
                "commit_message": f"default tools += {TOOL_IDS}, skills += {SKILL_IDS}"}
        r = call("POST", "/api/v1/models/model/update", form)
        print(f"updated {mid}: {'ok' if r and 'HTTP' not in r else r}")
    else:
        form = {"id": mid, "base_model_id": None, "name": m.get("name") or mid,
                "meta": {"toolIds": TOOL_IDS, "skillIds": SKILL_IDS}, "params": {}, "is_active": True,
                "commit_message": f"default tools = {TOOL_IDS}"}
        r = call("POST", "/api/v1/models/create", form)
        print(f"created override {mid}: {'ok' if r and 'HTTP' not in r else r}")
