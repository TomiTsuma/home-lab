"""Register the github-mcp and gitea-mcp servers in Open WebUI (run INSIDE the open-webui container).

    docker cp git-mcp/register_in_openwebui.py open-webui:/tmp/register_git.py
    # The Gitea token and/or GitHub token are read from STDIN, one per line, in this order: gitea, github (either may be empty):
    printf '%s\n%s\n' "$GITEA_TOKEN" "$GITHUB_PAT" | docker exec -i open-webui python3 /tmp/register_git.py

  * gitea  -> http://gitea-mcp:8080/mcp, bearer key = the Gitea token (all 28 read-only tools),
  * github -> http://github-mcp:8082/mcp, bearer key = the GitHub token, exposed through a 13-tool allow-list (the full list is ~18k tokens).
    With no GitHub token the connection is created DISABLED: add the token in Admin > Settings > External Tools > GitHub (or re-run
    with it) and enable it.
  * both are default-on for the Claude models only (repository content, issues and PR text are untrusted input; the local models can
    switch them on per chat).
Tokens are stored only in the Open WebUI config database, never in the repository. Idempotent: a token that is not given again is kept.
"""
import json, os, sqlite3, sys, time, urllib.error, urllib.request
import jwt

lines = sys.stdin.read().split("\n")
GITEA = (lines[0] if len(lines) > 0 else "").strip()
GITHUB = (lines[1] if len(lines) > 1 else "").strip()
GITHUB_TOOLS = ["get_me", "search_repositories", "get_file_contents", "list_commits", "get_commit", "list_branches", "search_code",
                "issue_read", "list_issues", "search_issues", "pull_request_read", "list_pull_requests", "search_pull_requests"]
CLAUDE = [m for m in os.environ.get("DEFAULT_ON_MODELS", "claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5,claude-fable-5-1").split(",") if m]

uid = sqlite3.connect("/app/backend/data/webui.db").execute("select id from user where role='admin' order by created_at limit 1").fetchone()[0]
JWT = jwt.encode({"id": uid, "exp": int(time.time()) + 600}, os.environ["WEBUI_SECRET_KEY"], algorithm="HS256")


def call(method, path, body=None):
    req = urllib.request.Request("http://localhost:8080" + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {JWT}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": e.read()[:300].decode(errors="replace")}


def conn(cid, name, desc, url, key, enable, names=None):
    cfg = {"enable": enable}
    if names:
        cfg["function_name_filter_list"] = ",".join(names)
    return {"url": url, "path": "", "type": "mcp", "auth_type": "bearer", "key": key, "headers": None, "forward_cookies": False,
            "config": cfg, "info": {"id": cid, "name": name, "description": desc}}


conns = (call("GET", "/api/v1/configs/tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
old = {(c.get("info") or {}).get("id"): c for c in conns}
keep = [c for c in conns if (c.get("info") or {}).get("id") not in ("gitea", "github")]
gitea_key = GITEA or (old.get("gitea") or {}).get("key", "")
github_key = GITHUB or (old.get("github") or {}).get("key", "")
keep.append(conn("gitea", "Gitea (TrueNAS, read-only)", "Read-only access to the Gitea instance on TrueNAS: repos, files, issues, PRs, commits, releases, search",
                 "http://gitea-mcp:8080/mcp", gitea_key, bool(gitea_key)))
keep.append(conn("github", "GitHub (read-only)", "Read-only GitHub: repos, files, commits, issues, pull requests, code search (key = your GitHub token)",
                 "http://github-mcp:8082/mcp", github_key, bool(github_key), GITHUB_TOOLS))
res = call("POST", "/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": keep})
print("tool servers:", [((c.get("info") or {}).get("id"), (c.get("config") or {}).get("enable")) for c in (res or {}).get("TOOL_SERVER_CONNECTIONS", [])] or res)

models = call("GET", "/api/models")
for m in models.get("data", models):
    if m.get("pipe") or m.get("arena") or str(m["id"]).startswith("arena"):
        continue
    ov = call("GET", f"/api/v1/models/model?id={m['id']}")
    if not ov or not ov.get("id"):
        continue
    meta = ov.get("meta") or {}
    tools = set(meta.get("toolIds") or [])
    git = {"server:mcp:gitea", "server:mcp:github"}
    want = (tools | git) if m["id"] in CLAUDE else (tools - git)
    if want == tools:
        print(f"unchanged {m['id']}")
        continue
    meta["toolIds"] = sorted(want)
    r = call("POST", "/api/v1/models/model/update", {"id": ov["id"], "base_model_id": ov.get("base_model_id"), "name": ov["name"], "meta": meta,
                                                       "params": ov.get("params") or {}, "is_active": ov.get("is_active", True),
                                                       "commit_message": "GitHub/Gitea MCP default-on for Claude models only"})
    print(f"updated {m['id']}: {'ok' if r and 'HTTP' not in r else r}")
