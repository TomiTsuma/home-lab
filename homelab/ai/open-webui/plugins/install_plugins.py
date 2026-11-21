"""Install reviewed Open WebUI community plugins from local files (run INSIDE the open-webui container).

Usage (from the host, see README "Community plugins"):
    docker cp plugins open-webui:/tmp/plugins
    docker exec open-webui python3 /tmp/plugins/install_plugins.py /tmp/plugins/manifest.json

Each manifest entry pins the sha256 of the exact file that was reviewed; a mismatch aborts that
plugin (so a file edited/replaced after review is never installed). Idempotent: existing ids are
updated in place. Auth: a short-lived admin JWT signed with WEBUI_SECRET_KEY (never stored).
"""
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt

BASE = "http://localhost:8080"


def admin_token() -> str:
    db = sqlite3.connect("/app/backend/data/webui.db")
    uid = db.execute("select id from user where role='admin' order by created_at limit 1").fetchone()[0]
    return jwt.encode({"id": uid, "exp": int(time.time()) + 900}, os.environ["WEBUI_SECRET_KEY"], algorithm="HS256")


TOKEN = admin_token()


def call(method: str, path: str, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"HTTP": e.code, "detail": e.read()[:500].decode(errors="replace")}


def install(entry: dict, root: Path) -> str:
    kind = entry["kind"]                      # "tool" | "function"
    pid = entry["id"]
    raw = (root / entry["file"]).read_bytes()          # hash raw bytes (no newline translation)
    digest = hashlib.sha256(raw).hexdigest()
    code = raw.decode("utf-8")
    if digest != entry["sha256"]:
        return f"SKIPPED {pid}: sha256 mismatch (file {digest[:12]} != reviewed {entry['sha256'][:12]})"

    if kind == "skill":
        form = {"id": pid, "name": entry["name"], "description": entry.get("description", ""),
                "content": code, "commit_message": f"install reviewed {entry.get('source', '')}"}
        existing = call("GET", f"/api/v1/skills/id/{pid}")
        if existing and existing.get("id"):
            res = call("POST", f"/api/v1/skills/id/{pid}/update", form)
        else:
            res = call("POST", "/api/v1/skills/create", form)
        return f"{'OK' if res and 'HTTP' not in res else 'FAILED'} {pid} (skill): {'' if res and 'HTTP' not in res else res}"

    api = "/api/v1/tools" if kind == "tool" else "/api/v1/functions"
    form = {"id": pid, "name": entry["name"], "content": code,
            "meta": {"description": entry.get("description", ""), "manifest": {}},
            "commit_message": f"install reviewed {entry.get('source', '')}"}
    if kind == "tool":
        form["access_grants"] = []            # private to the admin unless shared later

    existing = call("GET", f"{api}/id/{pid}")
    res = call("POST", f"{api}/id/{pid}/update" if existing and existing.get("id") else f"{api}/create", form)
    if not res or "HTTP" in res:
        return f"FAILED {pid}: {res}"
    notes = ["updated" if existing and existing.get("id") else "created"]

    if kind == "function":
        cur = call("GET", f"{api}/id/{pid}")
        if bool(cur.get("is_active")) != bool(entry.get("active", True)):
            call("POST", f"{api}/id/{pid}/toggle")
        cur = call("GET", f"{api}/id/{pid}")
        if bool(cur.get("is_global")) != bool(entry.get("global", False)):
            call("POST", f"{api}/id/{pid}/toggle/global")
        cur = call("GET", f"{api}/id/{pid}")
        notes.append(f"active={cur.get('is_active')} global={cur.get('is_global')} type={cur.get('type')}")

    if entry.get("valves") or entry.get("valves_suffix_true"):
        spec = call("GET", f"{api}/id/{pid}/valves/spec") or {}
        known = set((spec.get("properties") or {}).keys()) if isinstance(spec, dict) else set()
        wanted = dict(entry.get("valves") or {})
        # e.g. "dry_run": force every valve whose name ends with it to True (safety switches).
        suffix = entry.get("valves_suffix_true")
        if suffix:
            wanted.update({k: True for k in known if k.lower().endswith(suffix)})
        unknown = sorted(k for k in wanted if known and k not in known)
        wanted = {k: v for k, v in wanted.items() if not known or k in known}
        current = call("GET", f"{api}/id/{pid}/valves") or {}
        merged = {**(current if isinstance(current, dict) else {}), **wanted}
        v = call("POST", f"{api}/id/{pid}/valves/update", merged)
        notes.append(f"valves set ({len(wanted)})" if v is not None and "HTTP" not in (v or {}) else f"valves FAILED {v}")
        if unknown:
            notes.append(f"UNKNOWN valve names ignored: {unknown}")
    if entry.get("user_valves"):
        # UserValves are per user; this sets them for the (single) admin user the token belongs to.
        current = call("GET", f"{api}/id/{pid}/valves/user") or {}
        merged = {**(current if isinstance(current, dict) else {}), **entry["user_valves"]}
        v = call("POST", f"{api}/id/{pid}/valves/user/update", merged)
        notes.append("user valves set" if v and "HTTP" not in v else f"user valves FAILED {v}")
    return f"OK {pid}: " + ", ".join(notes)


def attach_to_models(entries: list[dict]) -> None:
    """Add per-model filters (filterIds) / tools (toolIds) to model overrides that already exist."""
    per_model: dict[str, dict[str, list[str]]] = {}
    for e in entries:
        for m in e.get("models", []):
            key = "toolIds" if e["kind"] == "tool" else "filterIds"
            per_model.setdefault(m, {}).setdefault(key, []).append(e["id"])
    for model_id, ids in per_model.items():
        m = call("GET", f"/api/v1/models/model?id={model_id}")
        if not m or not m.get("id"):
            print(f"  model {model_id}: no Open WebUI override exists, skipped attaching {ids}")
            continue
        meta = m.get("meta") or {}
        for key, new in ids.items():
            meta[key] = sorted(set(meta.get(key) or []) | set(new))
        form = {"id": m["id"], "base_model_id": m.get("base_model_id"), "name": m["name"], "meta": meta,
                "params": m.get("params") or {}, "is_active": m.get("is_active", True),
                "commit_message": "attach reviewed plugins"}
        r = call("POST", "/api/v1/models/model/update", form)
        print(f"  model {model_id}: {'ok' if r and 'HTTP' not in r else r} {ids}")


if __name__ == "__main__":
    manifest_path = Path(sys.argv[1])
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    for e in entries:
        print(install(e, manifest_path.parent))
    attach_to_models([e for e in entries if e.get("models")])
