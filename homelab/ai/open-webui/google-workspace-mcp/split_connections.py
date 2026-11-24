"""Expose the one google-workspace-mcp server through two Open WebUI connections with tool allow-lists (run INSIDE the open-webui container).

    docker cp google-workspace-mcp/split_connections.py open-webui:/tmp/split_connections.py
    docker exec open-webui python3 /tmp/split_connections.py

  * `gworkspace`: Calendar, Drive, Docs, Sheets, Slides: exactly the 10 tools it had before (the server now registers more).
  * `gmail`: the 7 Gmail read/draft tools. Same URL and same Google sign-in, but a separate connection, so it can be switched on per model:
    default-on only for the Claude models (email text is untrusted input; a small local model that reads a hostile email while also holding
    write tools is the risky combination). Local models can enable it per chat from the "+" menu.
Sending, labels, filters and settings are not exposed (and send/filters/labels are also disabled server-side).
Idempotent. Models: DEFAULT_ON_MODELS env (comma list) overrides the default set.
"""
import json, os, sqlite3, time, urllib.error, urllib.request
import jwt

WORKSPACE = ["list_calendars", "get_events", "manage_event", "get_doc_content", "search_drive_files", "get_drive_file_content",
             "get_drive_file_download_url", "get_drive_shareable_link", "read_sheet_values", "get_presentation"]
GMAIL = ["search_gmail_messages", "get_gmail_message_content", "get_gmail_messages_content_batch", "get_gmail_thread_content",
         "get_gmail_attachment_content", "list_gmail_labels", "draft_gmail_message"]
URL = "http://google-workspace-mcp:8000/mcp"
DEFAULT_ON = [m for m in os.environ.get("DEFAULT_ON_MODELS", "claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5,claude-fable-5-1").split(",") if m]

uid = sqlite3.connect("/app/backend/data/webui.db").execute("select id from user where role='admin' order by created_at limit 1").fetchone()[0]
TOKEN = jwt.encode({"id": uid, "exp": int(time.time()) + 600}, os.environ["WEBUI_SECRET_KEY"], algorithm="HS256")


def call(method, path, body=None):
    req = urllib.request.Request("http://localhost:8080" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": e.read()[:300].decode(errors="replace")}


def conn(cid, name, desc, names):
    return {"url": URL, "path": "", "type": "mcp", "auth_type": "none", "key": "", "headers": None, "forward_cookies": False,
            "config": {"enable": True, "function_name_filter_list": ",".join(names)}, "info": {"id": cid, "name": name, "description": desc}}


conns = (call("GET", "/api/v1/configs/tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
conns = [c for c in conns if (c.get("info") or {}).get("id") not in ("gworkspace", "gmail")]
conns += [conn("gworkspace", "Google Calendar, Drive, Docs, Sheets & Slides", "Calendar (read/write) and Drive/Docs/Sheets/Slides (read-only) via self-hosted workspace-mcp", WORKSPACE),
          conn("gmail", "Gmail (read + drafts)", "Read and search Gmail and create DRAFTS. Cannot send. Email text is untrusted input.", GMAIL)]
res = call("POST", "/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": conns})
print("tool servers:", [(c.get("info") or {}).get("id") for c in (res or {}).get("TOOL_SERVER_CONNECTIONS", [])] or res)

models = call("GET", "/api/models")
for m in models.get("data", models):
    if m.get("pipe") or m.get("arena") or str(m["id"]).startswith("arena"):
        continue
    ov = call("GET", f"/api/v1/models/model?id={m['id']}")
    if not ov or not ov.get("id"):
        continue
    meta = ov.get("meta") or {}
    tools = set(meta.get("toolIds") or [])
    want = tools | {"server:mcp:gmail"} if m["id"] in DEFAULT_ON else tools - {"server:mcp:gmail"}
    if want == tools:
        print(f"unchanged {m['id']}"); continue
    meta["toolIds"] = sorted(want)
    r = call("POST", "/api/v1/models/model/update", {"id": ov["id"], "base_model_id": ov.get("base_model_id"), "name": ov["name"], "meta": meta,
                                                       "params": ov.get("params") or {}, "is_active": ov.get("is_active", True),
                                                       "commit_message": "Gmail connection default-on for Claude models only"})
    print(f"updated {m['id']}: {'ok' if r and 'HTTP' not in r else r}")
