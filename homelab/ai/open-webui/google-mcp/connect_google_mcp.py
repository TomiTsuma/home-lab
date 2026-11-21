"""Register Google's hosted Workspace MCP servers (Drive/Docs/Sheets/Slides) in Open WebUI.

Run INSIDE the open-webui container, with the OAuth client passed as env vars (from .env):

    docker cp google-mcp/connect_google_mcp.py open-webui:/tmp/g.py
    docker exec -e GOOGLE_OAUTH_CLIENT_ID=... -e GOOGLE_OAUTH_CLIENT_SECRET=... \
        -e GOOGLE_MCP_SCOPE_MODE=readonly open-webui python3 /tmp/g.py

What it does (mirrors what the admin UI does for an "OAuth 2.1 (static)" MCP connection):
  1. POST /api/v1/configs/oauth/clients/register?type=mcp per server: discovers Google's auth
     endpoints from the server's protected-resource metadata and returns an encrypted client-info
     blob whose redirect URI is {WEBUI_URL}/oauth/clients/mcp:<id>/callback.
  2. Upserts the 4 connections into tool_server.connections (keeping every other connection,
     e.g. Homelab Tools) via POST /api/v1/configs/tool_servers, which also registers the OAuth
     clients with Open WebUI's OAuth client manager.
Google does not support dynamic client registration, hence the static (admin-provided) client.
Idempotent. The client secret ends up in Open WebUI's config DB (not in this repo).
"""
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

import jwt

CLIENT_ID = os.environ["GOOGLE_OAUTH_CLIENT_ID"]
CLIENT_SECRET = os.environ["GOOGLE_OAUTH_CLIENT_SECRET"]
MODE = os.environ.get("GOOGLE_MCP_SCOPE_MODE", "readonly")  # readonly | readwrite

G = "https://www.googleapis.com/auth/"
SERVERS = [
    # id,       name,            url,                                         readonly scopes,                          readwrite scopes
    ("gdrive", "Google Drive", "https://drivemcp.googleapis.com/mcp/v1", ["drive.readonly"], ["drive"]),
    ("gdocs", "Google Docs", "https://docsmcp.googleapis.com/mcp/v1", ["documents.readonly", "drive.readonly"], ["documents", "drive"]),
    ("gsheets", "Google Sheets", "https://sheetsmcp.googleapis.com/mcp/v1", ["spreadsheets.readonly", "drive.readonly"], ["spreadsheets", "drive"]),
    ("gslides", "Google Slides", "https://slidesmcp.googleapis.com/mcp/v1", ["presentations.readonly", "drive.readonly"], ["presentations", "drive"]),
]

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
        return {"HTTP": e.code, "detail": e.read()[:400].decode(errors="replace")}


if MODE not in ("readonly", "readwrite"):
    sys.exit("GOOGLE_MCP_SCOPE_MODE must be readonly or readwrite")

new_conns = {}
for sid, name, url, ro, rw in SERVERS:
    scope = " ".join(G + s for s in (ro if MODE == "readonly" else rw))
    reg = call("POST", "/api/v1/configs/oauth/clients/register?type=mcp",
               {"url": url, "client_id": sid, "client_name": f"Open WebUI {name}",
                "client_secret": CLIENT_SECRET, "oauth_scope": scope})
    if not reg or not reg.get("status"):
        sys.exit(f"register {sid} failed: {reg}")
    new_conns[sid] = {
        "url": url, "path": "", "type": "mcp", "auth_type": "oauth_2.1_static",
        "key": "", "headers": None, "forward_cookies": False,
        "config": {"enable": True, "oauth_scope": scope},
        "info": {
            "id": sid, "name": name,
            "description": f"{name} via Google's hosted MCP server ({MODE})",
            "oauth_client_info": reg["oauth_client_info"],
            "oauth_client_id": CLIENT_ID,
            "oauth_client_secret": CLIENT_SECRET,
            "oauth_scope": scope,
        },
    }
    print(f"registered {sid}: scope={scope.replace(G, '')}")

current = (call("GET", "/api/v1/configs/tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
kept = [c for c in current if (c.get("info") or {}).get("id") not in new_conns]
res = call("POST", "/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": kept + list(new_conns.values())})
if not res or "HTTP" in res:
    sys.exit(f"saving tool servers failed: {res}")
ids = [(c.get("info") or {}).get("id") for c in res["TOOL_SERVER_CONNECTIONS"]]
print("tool servers now:", ids)
