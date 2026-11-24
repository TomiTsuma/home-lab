"""Import Claude projects (claude.ai export: projects/*.json) into Open WebUI Folders (Open WebUI calls them "Projects" in code).

Run INSIDE the open-webui container next to import_chatgpt.py (reuses its API client / token minting):

    python3 import_claude_projects.py --src /tmp/cm/A --account-title ereuna.org --dry-run
    python3 import_claude_projects.py --src /tmp/cm/A --account-title ereuna.org --only "Ereuna Legal"
    python3 import_claude_projects.py --src /tmp/cm/A --account-title ereuna.org
    python3 import_claude_projects.py --rollback

Mapping:
  * one top-level folder per account ("Claude · <account title>"), one sub-folder per project (names stay unique per account);
  * folder.data.system_prompt = the project's instructions (prompt_template) + its description;
  * folder.data.files = the project's documents, uploaded WITH processing so they are embedded and searchable. The export only
    has the documents' extracted text, so every document is uploaded as text (".md/.txt/..." names kept, any other extension gets
    ".txt" appended, e.g. report.pdf -> report.pdf.txt);
  * Claude's starter project ("How to use Claude", is_starter_project) is skipped unless --include-starters;
  * chats are NOT linked to projects in the export, so the folders start without chats;
  * idempotent: a folder with the same name under the same parent is skipped. --rollback deletes the folders and files this
    script created (recorded in /app/backend/data/claude_import/projects_state.json).
"""
import argparse
import glob
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_chatgpt import Api, mint_token  # noqa: E402

STATE_PATH = "/app/backend/data/claude_import/projects_state.json"
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml", ".py", ".js", ".ts", ".tex", ".bib", ".rst", ".log", ".sql"}
MIME = {".md": "text/markdown", ".csv": "text/csv", ".json": "application/json", ".html": "text/html", ".htm": "text/html"}


def load_state():
    return json.load(open(STATE_PATH, encoding="utf-8")) if os.path.exists(STATE_PATH) else {"folders": [], "files": []}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    json.dump(st, open(STATE_PATH, "w", encoding="utf-8"))


def doc_name(fn):
    fn = (fn or "document").strip().replace("/", "-").replace("\\", "-") or "document"
    ext = os.path.splitext(fn)[1].lower()
    return fn if ext in TEXT_EXT else fn + ".txt"


def system_prompt(p):
    parts = []
    if (p.get("prompt_template") or "").strip():
        parts.append(p["prompt_template"].strip())
    if (p.get("description") or "").strip():
        parts.append("Project description:\n" + p["description"].strip())
    return "\n\n".join(parts)


def upload_text(api, name, text):
    boundary = uuid.uuid4().hex
    ext = os.path.splitext(name)[1].lower()
    mime = MIME.get(ext, "text/plain")
    safe = name.replace('"', "'")
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe}\"\r\nContent-Type: {mime}\r\n\r\n").encode() \
        + text.encode("utf-8") + f"\r\n--{boundary}--\r\n".encode()
    return api._req("POST", "/api/v1/files/?process=true&process_in_background=true", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}), mime


def wait_processed(api, fid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            st = api._req("GET", f"/api/v1/files/{fid}/process/status", timeout=30) or {}
        except RuntimeError as e:
            return f"status-error {str(e)[:80]}"
        if st.get("status") in ("completed", "failed"):
            return st.get("status") + (f" ({st.get('error')})" if st.get("error") else "")
        time.sleep(2)
    return "timeout"


def ensure_parent(api, title, st):
    name = f"Claude · {title}"
    for f in api._req("GET", "/api/v1/folders/") or []:
        if f.get("name") == name and not f.get("parent_id"):
            return f["id"], name
    res = api._req("POST", "/api/v1/folders/", json.dumps({"name": name}).encode(), {"Content-Type": "application/json"})
    st["folders"].append(res["id"])
    save_state(st)
    return res["id"], name


def rollback(api):
    st = load_state()
    nf = 0
    for fid in reversed(st["folders"]):
        try:
            api.delete(f"/api/v1/folders/{fid}")
            nf += 1
        except RuntimeError as e:
            print("  folder delete failed:", str(e)[:120])
    nfl = 0
    for fid in st["files"]:
        try:
            api.delete(f"/api/v1/files/{fid}")
            nfl += 1
        except RuntimeError as e:
            print("  file delete failed:", str(e)[:120])
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    print(f"rolled back: {nf} folders, {nfl} files")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--account-title", help='shown in the parent folder name, e.g. "ereuna.org" or "Gmail"')
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", help="substring of a project name (trial)")
    ap.add_argument("--include-starters", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    if a.rollback:
        token, _ = mint_token()
        return rollback(Api(a.api, token))
    if not a.src or not a.account_title:
        ap.error("--src and --account-title are required")
    projects = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(a.src, "projects", "*.json")))]
    projects.sort(key=lambda p: p.get("created_at") or "")
    skipped = [p["name"] for p in projects if p.get("is_starter_project") and not a.include_starters]
    projects = [p for p in projects if a.include_starters or not p.get("is_starter_project")]
    if a.only:
        projects = [p for p in projects if a.only.lower() in (p.get("name") or "").lower()]
    print(f"{len(projects)} projects (skipped starter projects: {skipped or 'none'})")
    if a.dry_run:
        for p in projects:
            docs = p.get("docs") or []
            print(f"  {p['name'][:60]!r}: instructions {len(system_prompt(p))} chars, {len(docs)} docs "
                  f"{[(doc_name(d.get('filename')), len(d.get('content') or '')) for d in docs][:4]}{' ...' if len(docs) > 4 else ''}")
        return
    token, user_id = mint_token()
    api = Api(a.api, token)
    st = load_state()
    parent_id, parent_name = ensure_parent(api, a.account_title, st)
    existing = {f["name"] for f in (api._req("GET", "/api/v1/folders/") or []) if f.get("parent_id") == parent_id}
    made = 0
    for p in projects:
        name = (p.get("name") or "Untitled project").strip()[:120]
        if name in existing:
            print(f"  skip (exists): {name}")
            continue
        files = []
        for d in p.get("docs") or []:
            text = (d.get("content") or "").strip()
            if not text:
                continue
            dn = doc_name(d.get("filename"))
            res, mime = upload_text(api, dn, text)
            st["files"].append(res["id"])
            status = wait_processed(api, res["id"])
            files.append({"type": "file", "id": res["id"], "name": dn, "url": f"/api/v1/files/{res['id']}/content", "size": len(text.encode()),
                          "status": "uploaded",
                          "file": {"id": res["id"], "user_id": user_id, "hash": None, "filename": dn, "data": {"status": "completed"},
                                   "meta": {"name": dn, "content_type": mime, "size": len(text.encode())}}})
            if not status.startswith("completed"):
                print(f"    ! {dn}: processing {status}")
        data = {}
        sp = system_prompt(p)
        if sp:
            data["system_prompt"] = sp
        if files:
            data["files"] = files
        body = {"name": name, "parent_id": parent_id, "data": data or None}
        res = api._req("POST", "/api/v1/folders/", json.dumps(body).encode(), {"Content-Type": "application/json"})
        st["folders"].append(res["id"])
        save_state(st)
        made += 1
        print(f"  created folder {name!r}: system prompt {len(sp)} chars, {len(files)} files")
    print(f"DONE: created {made} folders under {parent_name!r}")


if __name__ == "__main__":
    sys.exit(main())
