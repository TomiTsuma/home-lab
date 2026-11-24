"""Import Claude's memories (claude.ai export: memories/*.json) into Open WebUI's path-based Memory.

Run INSIDE the open-webui container next to import_chatgpt.py (reuses its API client / token minting):

    python3 import_claude_memories.py --src /tmp/cm/A --account ereuna --dry-run
    python3 import_claude_memories.py --src /tmp/cm/A --account ereuna
    python3 import_claude_memories.py --rollback              # deletes every memory whose path starts with "claude/"

Open WebUI 0.13 memories have a `type` (user | context) and an optional `path`, and the model can list and read paths, which is the
same shape as Claude's memory files. Mapping:
  * every Claude memory file -> one memory, path "claude/<account>/<original path>" (the two accounts both have /preferences.md,
    /areas/ereuna.md ... so they are kept apart by the account prefix); "projects/<uuid>/" becomes "projects/<project name>/";
  * type "user" (recalled in EVERY chat, capped by the admin setting memories.user_char_limit) only for the account-level
    profile.md and preferences.md; everything else is type "context" (found by semantic search / path, capped by context_char_limit);
  * the export's `conversations_memory` (a rolling summary) -> claude/<account>/conversations-memory.md, `project_memories`
    (one summary per project) -> claude/<account>/projects/<name>/memory-summary.md;
  * idempotent: a path that already exists is skipped. The memories API cannot set timestamps, so created_at is the import time;
    the content itself is left exactly as exported.
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_chatgpt import Api, mint_token  # noqa: E402

ROOT = "claude"


def seg(s: str) -> str:
    s = re.sub(r"[^\w &().,+\-]+", "-", str(s)).strip(" -.")
    return s[:60] or "x"


def project_names(src):
    names = {}
    for f in glob.glob(os.path.join(src, "projects", "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        names[d["uuid"]] = d.get("name") or d["uuid"][:8]
    return names


def build(src, account):
    names = project_names(src)
    entries = []
    for f in sorted(glob.glob(os.path.join(src, "memories", "*.json"))):
        mem = json.load(open(f, encoding="utf-8"))
        for mf in mem.get("memory_files") or []:
            parts = [p for p in mf["path"].strip("/").split("/") if p]
            orig = list(parts)
            if len(parts) >= 3 and parts[0] == "projects":
                parts[1] = seg(names.get(parts[1], parts[1][:8]))
            kind = "user" if len(orig) == 1 and orig[0] in ("profile.md", "preferences.md") else "context"
            entries.append({"path": "/".join([ROOT, account] + parts), "type": kind, "content": (mf.get("content") or "").strip()})
        if (mem.get("conversations_memory") or "").strip():
            entries.append({"path": f"{ROOT}/{account}/conversations-memory.md", "type": "context", "content": mem["conversations_memory"].strip()})
        for pu, text in (mem.get("project_memories") or {}).items():
            if (text or "").strip():
                entries.append({"path": f"{ROOT}/{account}/projects/{seg(names.get(pu, pu[:8]))}/memory-summary.md", "type": "context", "content": text.strip()})
    return [e for e in entries if e["content"]]


def rollback(api):
    mems = api._req("GET", "/api/v1/memories/") or []
    n = 0
    for m in mems:
        if (m.get("path") or "").startswith(ROOT + "/"):
            api.delete(f"/api/v1/memories/{m['id']}")
            n += 1
    print(f"rolled back: {n} memories")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--account")
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    if a.rollback:
        token, _ = mint_token()
        return rollback(Api(a.api, token))
    if not a.src or not a.account:
        ap.error("--src and --account are required")
    entries = build(a.src, a.account)
    paths = Counter(e["path"] for e in entries)
    dup = [p for p, n in paths.items() if n > 1]
    print(f"{len(entries)} memories to import (account {a.account}); types: {dict(Counter(e['type'] for e in entries))}; "
          f"total {sum(len(e['content']) for e in entries)} chars; duplicate paths: {dup or 'none'}")
    if a.dry_run:
        for e in entries:
            print(f"  [{e['type']:<7}] {e['path']}  ({len(e['content'])})")
        return
    token, _ = mint_token()
    api = Api(a.api, token)
    existing = {m.get("path") for m in (api._req("GET", "/api/v1/memories/") or []) if m.get("path")}
    added = skipped = 0
    for e in entries:
        if e["path"] in existing:
            skipped += 1
            continue
        api._req("POST", "/api/v1/memories/add", json.dumps({"content": e["content"], "type": e["type"], "path": e["path"]}).encode(),
                 {"Content-Type": "application/json"})
        added += 1
        if added % 10 == 0:
            print(f"  added {added}", flush=True)
    print(f"DONE: added {added}, skipped {skipped} (already present)")


if __name__ == "__main__":
    sys.exit(main())
