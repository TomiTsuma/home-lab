"""Move imported Claude chats into the Open WebUI Folders that mirror their Claude projects.

Run INSIDE the open-webui container next to import_chatgpt.py:

    python3 assign_chats_to_folders.py --map /tmp/chat_folder_map.json --dry-run
    python3 assign_chats_to_folders.py --map /tmp/chat_folder_map.json
    python3 assign_chats_to_folders.py --rollback          # puts every chat it moved back outside any folder

The Claude export has no project field on conversations, so the map is derived from evidence inside each conversation
(see the README): project files in the memory snapshot, writes into a project's memory path, project documents named in the chat,
project-knowledge-search use. Each entry: {claude_uuid, account_title, project}. "Claude Design" chats go to a folder of that name
(created if missing) under the account's parent folder "Claude · <account title>". Fragment chats ("<uuid>#fragN") follow their conversation.
"""
import argparse, json, os, sqlite3, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_chatgpt import Api, mint_token, DB_PATH

STATE = "/app/backend/data/claude_import/folder_moves.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map")
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    token, _ = mint_token()
    api = Api(a.api, token)
    post = lambda path, body: api._req("POST", path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    if a.rollback:
        moved = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else []
        for cid in moved:
            post(f"/api/v1/chats/{cid}/folder", {"folder_id": None})
        if os.path.exists(STATE):
            os.remove(STATE)
        print(f"rolled back: {len(moved)} chats moved out of their folders")
        return
    entries = json.load(open(a.map, encoding="utf-8"))
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    chat_ids = {}
    for cid, meta in con.execute("select id, meta from chat"):
        try:
            u = (json.loads(meta) if meta else {}).get("claude_uuid")
        except Exception:
            u = None
        if u:
            chat_ids.setdefault(u.split("#")[0], []).append(cid)
    folders = api._req("GET", "/api/v1/folders/") or []
    by_name = {(f.get("parent_id"), f["name"]): f["id"] for f in folders}
    plan, missing = [], []
    for e in entries:
        parent = by_name.get((None, f"Claude · {e['account_title']}"))
        if not parent:
            missing.append(("no parent folder", e["account_title"])); continue
        fid = by_name.get((parent, e["project"]))
        if not fid and e["project"] == "Claude Design" and not a.dry_run:
            fid = api._req("POST", "/api/v1/folders/", json.dumps({"name": "Claude Design", "parent_id": parent}).encode(), {"Content-Type": "application/json"})["id"]
            by_name[(parent, "Claude Design")] = fid
        if not fid and e["project"] != "Claude Design":
            missing.append(("no folder", e["project"])); continue
        for cid in chat_ids.get(e["claude_uuid"], []):
            plan.append((cid, fid or "<new Claude Design folder>", e["project"], e["title"]))
        if e["claude_uuid"] not in chat_ids:
            missing.append(("chat not imported", e["title"]))
    print(f"{len(entries)} mapped conversations -> {len(plan)} chats to move (incl. fragments); problems: {missing[:8] or 'none'}")
    if a.dry_run:
        return
    moved = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else []
    for i, (cid, fid, proj, title) in enumerate(plan, 1):
        post(f"/api/v1/chats/{cid}/folder", {"folder_id": fid})
        moved.append(cid)
        if i % 10 == 0:
            print(f"  moved {i}", flush=True)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(moved, open(STATE, "w", encoding="utf-8"))
    print(f"DONE: moved {len(plan)} chats")


main()
