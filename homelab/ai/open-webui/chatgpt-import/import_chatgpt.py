"""Import a ChatGPT data export (conversations-NNN.json + *.dat attachments) into Open WebUI.

Run INSIDE the open-webui container (it mints a short-lived admin JWT and reads webui.db read-only to skip chats that were
already imported), after copying the export to /tmp/chatgpt:

    python3 import_chatgpt.py --src /tmp/chatgpt --dry-run                 # no network, prints what would happen
    python3 import_chatgpt.py --src /tmp/chatgpt --limit 3                 # trial: first 3 chats
    python3 import_chatgpt.py --src /tmp/chatgpt                           # everything not yet imported

What it does (see README "Importing ChatGPT conversations"):
  * rebuilds each conversation from its parent pointers: the main thread (current_node -> root) is the chat; messages the export
    left unlinked become native branches when they hang off the main thread, or separate "(recovered fragment N)" chats when
    they do not (nothing is dropped); only visible user/assistant text is kept;
    hidden "thoughts" / "reasoning_recap" entries are dropped; ChatGPT citation markers (U+E200..U+E206 blocks, old
    "【12†source】" cites) are removed, entity markers become their plain name;
  * keeps the original dates, pinned/archived flags and model name (gpt-4o, gpt-5, ...); tags every chat `chatgpt-import`;
  * attachments found next to the export (<asset id>.dat): images are embedded inline as data URIs (what Open WebUI itself
    stores), other files are uploaded as Open WebUI files WITHOUT document processing (?process=false: no embedding of PDFs etc.)
    and shown as file chips; attachments that are not in the export are replaced by an italic note so the gap is visible;
  * idempotent: a chat whose ChatGPT id is already in webui.db (meta.chatgpt_id) is skipped; uploaded assets are remembered in
    /app/backend/data/chatgpt_import/state.json so a re-run does not upload them again; `--rollback` deletes everything it created.
"""
import argparse
import glob
import json
import mimetypes
import os
import re
import sqlite3
import sys
import time
import uuid
import urllib.error
import urllib.request
from collections import Counter

TAG = "chatgpt-import"
STATE_PATH = "/app/backend/data/chatgpt_import/state.json"
DB_PATH = "/app/backend/data/webui.db"
IMAGE_MIMES = ("image/png", "image/jpeg", "image/gif", "image/webp")

# ----------------------------------------------------------------------------------------------- text cleaning
_ENTITY = re.compile(r"entity(\[.*?\])", re.S)
_BLOCK = re.compile(r"[^]*")          # cite, filecite, cite_v2, navlist, summary, video, image_group, ...
_OLD_CITE = re.compile(r"【[^】†]{0,300}†[^】]{0,300}】")
_PUA = re.compile(r"[-]")


def _entity_name(m):
    try:
        arr = json.loads(m.group(1))
        return str(arr[1]) if len(arr) > 1 else ""
    except Exception:
        return ""


def clean_text(t: str) -> str:
    t = _ENTITY.sub(_entity_name, t)
    t = _BLOCK.sub("", t)
    t = _OLD_CITE.sub("", t)
    t = _PUA.sub("", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


# ----------------------------------------------------------------------------------------------- export reading
def load_conversations(src):
    convs = []
    for f in sorted(glob.glob(os.path.join(src, "conversations-*.json"))):
        with open(f, encoding="utf-8") as fh:
            convs += json.load(fh)
    return convs


def sniff_mime(path, name=None):
    with open(path, "rb") as fh:
        b = fh.read(16)
    if b[:4] == b"\x89PNG":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"%PDF":
        return "application/pdf"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    guess = mimetypes.guess_type(name or "")[0]
    return guess or "application/octet-stream"


def thread(conv):
    """Linear list of message dicts from the root to current_node (ChatGPT stores a tree; this export has no branches)."""
    mapping = conv.get("mapping") or {}
    node_id, path = conv.get("current_node"), []
    seen = set()
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        path.append(mapping[node_id])
        node_id = mapping[node_id].get("parent")
    return [n["message"] for n in reversed(path) if n.get("message")]


def visible(msg):
    if msg["author"]["role"] not in ("user", "assistant"):
        return False
    if msg["content"].get("content_type") not in ("text", "multimodal_text"):
        return False
    return not (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation")


def asset_refs(msg):
    """[(asset id, display name or None, mime or None)] referenced by a message, de-duplicated, in order."""
    refs, seen = [], set()
    for p in msg["content"].get("parts") or []:
        if isinstance(p, dict) and p.get("asset_pointer"):
            aid = p["asset_pointer"].split("://")[-1]
            if aid not in seen:
                seen.add(aid)
                refs.append((aid, None, "image/png" if p.get("content_type") == "image_asset_pointer" else None))
    for a in (msg.get("metadata") or {}).get("attachments") or []:
        aid = a.get("id")
        if aid and aid not in seen:
            seen.add(aid)
            refs.append((aid, a.get("name"), a.get("mime_type")))
        elif aid:  # same asset as an image part: take the real name and mime
            refs = [(i, a.get("name") or n, a.get("mime_type") or m) if i == aid else (i, n, m) for i, n, m in refs]
    return refs


# ----------------------------------------------------------------------------------------------- Open WebUI side
class Api:
    def __init__(self, base, token):
        self.base, self.token = base.rstrip("/"), token

    def _req(self, method, path, body=None, headers=None, timeout=600):
        h = {"Authorization": f"Bearer {self.token}"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, method=method, data=body, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")

    def delete(self, path):
        return self._req("DELETE", path)

    def import_chats(self, forms):
        return self._req("POST", "/api/v1/chats/import", json.dumps({"chats": forms}).encode(), {"Content-Type": "application/json"})

    def upload(self, path, name, mime):
        boundary = uuid.uuid4().hex
        with open(path, "rb") as fh:
            data = fh.read()
        safe = name.replace('"', "'").replace("\r", " ").replace("\n", " ")
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe}\"\r\nContent-Type: {mime}\r\n\r\n").encode() \
            + data + f"\r\n--{boundary}--\r\n".encode()
        return self._req("POST", "/api/v1/files/?process=false", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def mint_token():
    import jwt  # available inside the open-webui container
    uid = sqlite3.connect(DB_PATH).execute("select id from user where role='admin' order by created_at limit 1").fetchone()[0]
    return jwt.encode({"id": uid, "exp": int(time.time()) + 6 * 3600}, os.environ["WEBUI_SECRET_KEY"], algorithm="HS256"), uid


def already_imported():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    out = set()
    for (meta,) in con.execute("select meta from chat"):
        try:
            cid = (json.loads(meta) if meta else {}).get("chatgpt_id")
        except Exception:
            cid = None
        if cid:
            out.add(cid)
    return out


# ----------------------------------------------------------------------------------------------- conversion
class Converter:
    def __init__(self, src, api=None, user_id="", no_assets=False):
        self.src, self.api, self.user_id, self.no_assets = src, api, user_id, no_assets
        self.names = {}
        p = os.path.join(src, "conversation_asset_file_names.json")
        if os.path.exists(p):
            self.names = json.load(open(p, encoding="utf-8"))
        self.state = {"assets": {}}
        if os.path.exists(STATE_PATH):
            self.state = json.load(open(STATE_PATH, encoding="utf-8"))
        self.stats = Counter()

    def save_state(self):
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        json.dump(self.state, open(STATE_PATH, "w", encoding="utf-8"))

    def asset(self, aid, name, mime):
        """-> (file entry for message.files | None, note text | None)."""
        path = os.path.join(self.src, aid + ".dat")
        label = name or self.names.get(aid + ".dat") or "attachment"
        if not os.path.exists(path):
            self.stats["assets_missing"] += 1
            return None, f"_[attachment not included in the ChatGPT export: {label}]_"
        mime = mime if mime and mime != "application/octet-stream" else sniff_mime(path, label)
        if mime == "image/png" and sniff_mime(path) != "image/png":
            mime = sniff_mime(path, label)  # a part claimed png; trust the bytes
        self.stats["assets_found"] += 1
        if mime in IMAGE_MIMES:
            # Open WebUI's frontend treats a relative image url as a FILE ID (it builds /api/v1/files/<url>), so a path such as
            # /api/v1/files/<id>/content renders as a broken image. Its own images are stored inline as data URIs, which also
            # work when a chat is continued with a vision model, so embed them.
            self.stats["images_embedded"] += 1
            if self.api is None:  # dry run
                return {"type": "image", "name": label}, None
            import base64
            with open(path, "rb") as fh:
                return {"type": "image", "url": f"data:{mime};base64," + base64.b64encode(fh.read()).decode()}, None
        if self.api is None:  # dry run
            return {"type": "file", "name": label}, None
        info = self.state["assets"].get(aid)
        if not info:
            res = self.api.upload(path, label, mime)
            info = {"id": res["id"], "filename": label, "mime": mime, "size": os.path.getsize(path)}
            self.state["assets"][aid] = info
            self.stats["assets_uploaded"] += 1
        url = f"/api/v1/files/{info['id']}/content"
        return {
            "type": "file", "id": info["id"], "name": info["filename"], "url": url, "size": info["size"], "status": "uploaded",
            "file": {"id": info["id"], "user_id": self.user_id, "hash": None, "filename": info["filename"], "data": {"status": "completed"},
                     "meta": {"name": info["filename"], "content_type": info["mime"], "size": info["size"]}},
        }, None

    def _content(self, m, chat_model, t0):
        """Converted message fields, or None when the message has neither text nor files."""
        parts = m["content"].get("parts") or []
        text = clean_text("\n".join(p for p in parts if isinstance(p, str)))
        files, notes = [], []
        if not self.no_assets:
            for aid, name, mime in asset_refs(m):
                entry, note = self.asset(aid, name, mime)
                if entry:
                    files.append(entry)
                if note:
                    notes.append(note)
        if notes:
            text = (text + "\n\n" if text else "") + "\n".join(notes)
        if not text and not files:
            self.stats["empty_messages_dropped"] += 1
            return None
        role = m["author"]["role"]
        slug = (m.get("metadata") or {}).get("model_slug") or chat_model
        return {"role": role, "content": text, "files": files, "slug": slug, "ts": int(m.get("create_time") or t0)}

    def _form(self, conv, nodes, vpar, current, title, chatgpt_id, chat_model):
        """One import form from a group of converted nodes {nid: fields}; tree edges come from vpar (visible parent)."""
        ids = {nid: str(uuid.uuid4()) for nid in nodes}
        msgs = {}
        for nid in sorted(nodes, key=lambda n: (nodes[n]["ts"], n)):
            c = nodes[nid]
            par = vpar.get(nid)
            node = {"id": ids[nid], "parentId": ids.get(par) if par in nodes else None, "childrenIds": [],
                    "role": c["role"], "content": c["content"], "timestamp": c["ts"]}
            if c["role"] == "user":
                node["models"] = [chat_model]
            else:
                node.update({"model": c["slug"], "modelName": c["slug"], "modelIdx": 0, "done": True})
            if c["files"]:
                node["files"] = c["files"]
            msgs[ids[nid]] = node
        for node in msgs.values():  # children in time order
            if node["parentId"]:
                msgs[node["parentId"]]["childrenIds"].append(node["id"])
        chain, nid, hops = [], current, 0
        while nid in nodes and hops < 100000:
            chain.append(ids[nid])
            nid = vpar.get(nid) if vpar.get(nid) in nodes else None
            hops += 1
        chain.reverse()
        first_ts = msgs[chain[0]]["timestamp"]
        created = int(conv.get("create_time") or first_ts)
        chat = {"id": "", "title": title, "models": [chat_model], "params": {}, "tags": [TAG],
                "history": {"messages": msgs, "currentId": chain[-1]}, "messages": [msgs[i] for i in chain],
                "timestamp": created * 1000, "files": []}
        self.stats["messages"] += len(msgs)
        return {"chat": chat, "folder_id": None, "archived": False, "pinned": False,
                "created_at": created, "updated_at": int(conv.get("update_time") or created),
                "current_message_id": chain[-1],
                "meta": {"tags": [TAG], "chatgpt_id": chatgpt_id, "source": "chatgpt-export"}}

    def convert(self, conv):
        """-> list of import forms: the main thread (plus any branches that hang off it) and one chat per disconnected fragment.

        The export's `children` lists are incomplete (about 2.7% of visible messages are not reachable from current_node), so the
        tree is rebuilt from `parent` pointers instead and nothing is dropped.
        """
        mapping = conv.get("mapping") or {}
        t0 = int(conv.get("create_time") or time.time())
        path, nid, seen = [], conv.get("current_node"), set()
        while nid and nid in mapping and nid not in seen:
            seen.add(nid)
            path.append(nid)
            nid = mapping[nid].get("parent")
        slugs = Counter(((mapping[n].get("message") or {}).get("metadata") or {}).get("model_slug") for n in path
                        if ((mapping[n].get("message") or {}).get("author") or {}).get("role") == "assistant")
        slugs.pop(None, None)
        chat_model = (slugs.most_common(1)[0][0] if slugs else conv.get("default_model_slug")) or "chatgpt"

        nodes = {}
        for nid, node in mapping.items():
            msg = node.get("message")
            if msg and visible(msg):
                c = self._content(msg, chat_model, t0)
                if c:
                    nodes[nid] = c

        def visible_parent(n):
            p, hops = mapping[n].get("parent"), 0
            while p and p in mapping and hops < 100000:
                if p in nodes:
                    return p
                p, hops = mapping[p].get("parent"), hops + 1
            return None

        vpar = {n: visible_parent(n) for n in nodes}
        main = {n for n in path if n in nodes}
        main_current = next((n for n in path if n in nodes), None)  # nearest visible node to current_node

        def top(n):  # climb through off-thread nodes to the first one attached to the main thread, or to nothing
            hops = 0
            while vpar[n] is not None and vpar[n] not in main and hops < 100000:
                n, hops = vpar[n], hops + 1
            return n

        groups, branch_nodes = {}, set()
        for n in nodes:
            if n in main:
                continue
            t = top(n)
            if main and vpar[t] in main:
                branch_nodes.add(n)
            else:
                groups.setdefault(t, set()).add(n)

        title = (conv.get("title") or "").strip() or "ChatGPT chat"
        cid = conv.get("conversation_id") or conv.get("id")
        forms = []
        if main:
            sub = {n: nodes[n] for n in main | branch_nodes}
            f = self._form(conv, sub, vpar, main_current, title, cid, chat_model)
            f["pinned"], f["archived"] = bool(conv.get("pinned_time")), bool(conv.get("is_archived"))
            forms.append(f)
            self.stats["chats"] += 1
            self.stats["branch_messages"] += len(branch_nodes)
        else:
            self.stats["conversations_without_main_thread"] += 1
        for k, (t, members) in enumerate(sorted(groups.items(), key=lambda kv: (nodes[kv[0]]["ts"], kv[0])), 1):
            sub = {n: nodes[n] for n in members}
            cur = max(sub, key=lambda n: (sub[n]["ts"], n))
            f = self._form(conv, sub, vpar, cur, f"{title} (recovered fragment {k})", f"{cid}#frag{k}", chat_model)
            first = min(c["ts"] for c in sub.values())
            f["created_at"], f["updated_at"] = first, max(c["ts"] for c in sub.values())
            f["chat"]["timestamp"] = first * 1000
            f["archived"] = bool(conv.get("is_archived"))
            forms.append(f)
            self.stats["fragment_chats"] += 1
            self.stats["fragment_messages"] += len(sub)
        return forms


def rollback(api):
    """Undo an import: delete every chat this script created (meta.source == chatgpt-export) and every file it uploaded."""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    chat_ids = []
    for cid, meta in con.execute("select id, meta from chat"):
        try:
            if (json.loads(meta) if meta else {}).get("source") == "chatgpt-export":
                chat_ids.append(cid)
        except Exception:
            pass
    for cid in chat_ids:
        api.delete(f"/api/v1/chats/{cid}")
    state = json.load(open(STATE_PATH, encoding="utf-8")) if os.path.exists(STATE_PATH) else {"assets": {}}
    n_files = 0
    for info in state["assets"].values():
        try:
            api.delete(f"/api/v1/files/{info['id']}")
            n_files += 1
        except RuntimeError as e:
            print("  file delete failed:", str(e)[:120])
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    print(f"rolled back: {len(chat_ids)} chats, {n_files} uploaded files")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="only the first N chats that are not yet imported")
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--no-assets", action="store_true")
    ap.add_argument("--only", help="comma-separated ChatGPT conversation ids (trial runs)")
    ap.add_argument("--out", help="dry run: write the converted forms to this file")
    ap.add_argument("--rollback", action="store_true", help="delete every chat/file this importer created, then exit")
    a = ap.parse_args()

    if a.rollback:
        token, _uid = mint_token()
        return rollback(Api(a.api, token))

    convs = load_conversations(a.src)
    convs.sort(key=lambda c: c.get("create_time") or 0)
    print(f"export: {len(convs)} conversations")
    api = user_id = None
    done = set()
    if not a.dry_run:
        token, user_id = mint_token()
        api = Api(a.api, token)
        done = already_imported()
        print(f"already in Open WebUI (by chatgpt_id): {len(done)}")
    conv = Converter(a.src, api, user_id or "", a.no_assets)

    todo = [c for c in convs if (c.get("conversation_id") or c.get("id")) not in done]
    if a.only:
        wanted = set(a.only.split(","))
        todo = [c for c in todo if (c.get("conversation_id") or c.get("id")) in wanted]
    if a.limit:
        todo = todo[: a.limit]
    print(f"to process: {len(todo)}")
    forms, imported, t0 = [], 0, time.time()

    def flush():
        nonlocal forms, imported
        if forms and api:
            res = api.import_chats(forms)
            imported += len(res or [])
            conv.save_state()
            print(f"  imported {imported}  ({time.time() - t0:.0f}s)  files uploaded {conv.stats['assets_uploaded']}", flush=True)
        forms = []

    kept = []
    for c in todo:
        for f in conv.convert(c):
            if a.dry_run:
                kept.append(f)
            else:
                forms.append(f)
                if len(forms) >= a.batch:
                    flush()
    flush()
    conv.save_state() if api else None
    print("stats:", dict(conv.stats))
    if a.dry_run and a.out:
        json.dump(kept, open(a.out, "w", encoding="utf-8"))
        print(f"wrote {len(kept)} converted chats to {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")
    if not a.dry_run:
        print(f"DONE: imported {imported} chats")


if __name__ == "__main__":
    sys.exit(main())
