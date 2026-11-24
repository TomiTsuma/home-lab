"""Import a Claude (claude.ai) data export into Open WebUI: conversations.json + design_chats/*.json.

Run INSIDE the open-webui container next to import_chatgpt.py (it reuses that file's API client and token minting):

    python3 import_claude.py --src /tmp/claude_A --account ereuna --dry-run
    python3 import_claude.py --src /tmp/claude_A --account ereuna --limit 3
    python3 import_claude.py --src /tmp/claude_A --account ereuna
    python3 import_claude.py --src x --rollback            # deletes everything this script created

Conversion rules (see README "Importing Claude conversations"):
  * a chat is rebuilt from parent_message_uuid: the branch that ends at the newest message is the chat, other branches become native
    Open WebUI branches (or "(recovered fragment N)" chats if they are not connected to the main thread); nothing is dropped;
  * assistant/user `text` blocks are the message text; `tool_use` + `tool_result` pairs become one collapsed <details> block each
    (name, human label, input and result truncated); `thinking`, `injected_prompt_block` and `token_budget` blocks are dropped;
  * uploaded images/PDFs are NOT in the export (only a name and id), so the message gets an italic note listing them; pasted-text
    attachments carry their full text and are uploaded as text files (process=false) shown as file chips;
  * every chat is tagged `claude-import` plus `claude-<account>`; `meta.claude_uuid` is the de-duplication key, `meta.source` is
    `claude-export` (what --rollback matches); design chats get the extra tag `claude-design`.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from import_chatgpt import Api, mint_token, DB_PATH  # noqa: E402

TAG = "claude-import"
SOURCE = "claude-export"
STATE_PATH = "/app/backend/data/claude_import/state.json"
MODEL = "claude"
TOOL_INPUT_MAX, TOOL_RESULT_MAX = 1200, 1500
COMPACT_INPUT_MAX, COMPACT_RESULT_MAX = 250, 350
MSG_TOOL_BUDGET = 40000      # chars of tool markup per message before it is shrunk / summarised (one agentic run can have 450 calls)
SUMMARY_LINES_MAX = 120
SKIP_BLOCKS = {"thinking", "injected_prompt_block", "token_budget"}


# ----------------------------------------------------------------------------------------------- helpers
def ts(s, default=None):
    """ISO timestamp (Z, up to nanosecond fractions) -> epoch seconds."""
    if not s:
        return default
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(s)).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).astimezone(timezone.utc).timestamp())
    except ValueError:
        return default


def trunc(s, n):
    return s if len(s) <= n else s[:n].rstrip() + f"\n… [truncated {len(s) - n} chars]"


def fence(text, lang=""):
    f = "```"
    while f in text:
        f += "`"
    return f"{f}{lang}\n{text}\n{f}"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def result_text(content):
    if isinstance(content, str):
        return content
    out = []
    for c in content or []:
        if isinstance(c, dict) and c.get("type") == "text":
            out.append(c.get("text") or "")
        elif isinstance(c, dict) and c.get("type") == "image":
            out.append("[image]")
    return "\n".join(out)


def one_liner(use, res):
    src = use or res or {}
    label = (use or {}).get("message") or ((use or {}).get("display_content") or {}).get("text") or ""
    if not label:
        inp = (use or {}).get("input")
        if isinstance(inp, dict):
            label = str(inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("query") or inp.get("url") or "")
    label = " ".join(str(label).split())[:110]
    err = " (error)" if (res or {}).get("is_error") else ""
    return f"- {esc(src.get('name') or 'tool')}{' — ' + esc(label) if label else ''}{err}"


def render_tool(use, res, imax=TOOL_INPUT_MAX, rmax=TOOL_RESULT_MAX):
    """One collapsed block for a tool call (input and result truncated)."""
    src = use or res or {}
    name = src.get("name") or "tool"
    label = (use or {}).get("message") or ((use or {}).get("display_content") or {}).get("text") or ""
    inp = (use or {}).get("input")
    body = []
    if inp:
        if isinstance(inp, dict) and isinstance(inp.get("command"), str):
            body.append("**Input**\n\n" + fence(trunc(inp["command"], imax), "bash"))
        else:
            body.append("**Input**\n\n" + fence(trunc(json.dumps(inp, ensure_ascii=False), imax), "json"))
    if res is not None:
        rt = result_text(res.get("content")).strip()
        head = "**Error**" if res.get("is_error") else "**Result**"
        body.append(head + "\n\n" + (fence(trunc(rt, rmax)) if rt else "_(empty)_"))
    else:
        body.append("_(no result recorded)_")
    summary = f"🔧 {esc(name)}" + (f" — {esc(trunc(label, 120))}" if label else "")
    return f"<details>\n<summary>{summary}</summary>\n\n" + "\n\n".join(body) + "\n\n</details>"


# ----------------------------------------------------------------------------------------------- converter
class Converter:
    def __init__(self, api, user_id, account, dry):
        self.api, self.user_id, self.account, self.dry = api, user_id, account, dry
        self.tags = [TAG, f"claude-{account}"]
        self.stats = Counter()
        self.state = {"uploads": {}}
        if not dry and os.path.exists(STATE_PATH):
            self.state = json.load(open(STATE_PATH, encoding="utf-8"))

    def save_state(self):
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        json.dump(self.state, open(STATE_PATH, "w", encoding="utf-8"))

    # -- attachments (pasted text) ---------------------------------------------------------------------
    def text_attachment(self, a, n):
        content = a.get("extracted_content") or ""
        if not content.strip():
            return None
        name = (a.get("file_name") or "").strip() or f"pasted-text-{n}.txt"
        if "." not in os.path.basename(name):
            name += ".txt"
        mime = {"txt": "text/plain", "md": "text/markdown", "json": "application/json", "html": "text/html"}.get(
            (a.get("file_type") or "").lower().split("/")[-1], a.get("file_type") if "/" in (a.get("file_type") or "") else "text/plain")
        data = content.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        self.stats["text_attachments"] += 1
        if self.dry:
            return {"type": "file", "name": name}
        info = self.state["uploads"].get(sha)
        if not info:
            tmp = f"/tmp/_claude_att_{sha[:16]}"
            with open(tmp, "wb") as fh:
                fh.write(data)
            try:
                res = self.api.upload(tmp, name, mime)
            finally:
                os.remove(tmp)
            info = {"id": res["id"], "filename": name, "mime": mime, "size": len(data)}
            self.state["uploads"][sha] = info
            self.stats["uploads"] += 1
        return {"type": "file", "id": info["id"], "name": info["filename"], "url": f"/api/v1/files/{info['id']}/content",
                "size": info["size"], "status": "uploaded",
                "file": {"id": info["id"], "user_id": self.user_id, "hash": None, "filename": info["filename"],
                         "data": {"status": "completed"}, "meta": {"name": info["filename"], "content_type": info["mime"], "size": info["size"]}}}

    def render_items(self, items):
        """Text blocks as they are; tool calls as collapsed blocks, shrunk (then summarised) when a message would get huge."""
        def tools(imax, rmax):
            return [render_tool(i[1], i[2], imax, rmax) if i[0] == "x" else i[1] for i in items]
        out = tools(TOOL_INPUT_MAX, TOOL_RESULT_MAX)
        size = lambda xs: sum(len(x) for x, i in zip(xs, items) if i[0] == "x")
        if size(out) <= MSG_TOOL_BUDGET:
            return out
        out = tools(COMPACT_INPUT_MAX, COMPACT_RESULT_MAX)
        self.stats["messages_tool_compacted"] += 1
        if size(out) <= MSG_TOOL_BUDGET:
            return out
        # still too big: keep the prose, replace all tool calls by one list of one-liners at the first call's position
        calls = [i for i in items if i[0] == "x"]
        lines = [one_liner(u, r) for _, u, r in calls]
        shown = lines[:SUMMARY_LINES_MAX] + ([f"- … and {len(lines) - SUMMARY_LINES_MAX} more"] if len(lines) > SUMMARY_LINES_MAX else [])
        block = f"<details>\n<summary>🔧 {len(calls)} tool calls (summarised)</summary>\n\n" + "\n".join(shown) + "\n\n</details>"
        self.stats["messages_tool_summarised"] += 1
        res, placed = [], False
        for i in items:
            if i[0] == "t":
                res.append(i[1])
            elif not placed:
                res.append(block)
                placed = True
        return res

    # -- one message ----------------------------------------------------------------------------------------
    def message(self, m):
        items, pending, missing = [], {}, {}      # items: ("t", text) | ("x", tool_use, tool_result)
        for b in m.get("content") or []:
            t = b.get("type")
            if t == "text":
                tx = (b.get("text") or "").strip()
                if tx:
                    items.append(("t", tx))
            elif t == "tool_use":
                pending[b.get("id")] = b
            elif t == "tool_result":
                items.append(("x", pending.pop(b.get("tool_use_id"), None), b))
                self.stats["tool_calls"] += 1
            elif t in ("image", "document"):
                missing[b.get("file_uuid") or uuid.uuid4().hex] = b.get("title")
            elif t in SKIP_BLOCKS:
                self.stats[f"skipped_{t}"] += 1
        for b in pending.values():  # a call without a recorded result
            items.append(("x", b, None))
            self.stats["tool_calls"] += 1
        parts = self.render_items(items)
        for f in m.get("files") or []:
            key = f.get("file_uuid") or uuid.uuid4().hex
            if not missing.get(key):
                missing[key] = f.get("file_name") or None
        files = []
        for i, a in enumerate(m.get("attachments") or [], 1):
            entry = self.text_attachment(a, i)
            if entry:
                files.append(entry)
        if not parts and (m.get("text") or "").strip():
            parts.append(m["text"].strip())
        if missing:
            names = [(n or "unnamed file") for n in missing.values()]
            shown = ", ".join(names[:8]) + (f" (+{len(names) - 8} more)" if len(names) > 8 else "")
            parts.append(f"_[{len(names)} attachment(s) not included in the Claude export: {shown}]_")
            self.stats["missing_attachments"] += len(names)
        text = "\n\n".join(parts).strip()
        if not text and not files:
            self.stats["empty_messages_dropped"] += 1
            return None
        return {"role": "user" if m.get("sender") == "human" else "assistant", "content": text, "files": files,
                "ts": ts(m.get("created_at"), 0)}

    # -- forms ----------------------------------------------------------------------------------------------
    def form(self, conv_id, title, created, updated, nodes, vpar, current, extra_tags=()):
        ids = {n: str(uuid.uuid4()) for n in nodes}
        msgs = {}
        for n in sorted(nodes, key=lambda x: (nodes[x]["ts"], x)):
            c = nodes[n]
            par = vpar.get(n)
            node = {"id": ids[n], "parentId": ids.get(par) if par in nodes else None, "childrenIds": [], "role": c["role"],
                    "content": c["content"], "timestamp": c["ts"]}
            if c["role"] == "user":
                node["models"] = [MODEL]
            else:
                node.update({"model": MODEL, "modelName": "Claude", "modelIdx": 0, "done": True})
            if c["files"]:
                node["files"] = c["files"]
            msgs[ids[n]] = node
        for node in msgs.values():
            if node["parentId"]:
                msgs[node["parentId"]]["childrenIds"].append(node["id"])
        chain, n, hops = [], current, 0
        while n in nodes and hops < 100000:
            chain.append(ids[n])
            n = vpar.get(n) if vpar.get(n) in nodes else None
            hops += 1
        chain.reverse()
        tags = self.tags + list(extra_tags)
        chat = {"id": "", "title": title, "models": [MODEL], "params": {}, "tags": tags,
                "history": {"messages": msgs, "currentId": chain[-1]}, "messages": [msgs[i] for i in chain],
                "timestamp": created * 1000, "files": []}
        self.stats["messages"] += len(msgs)
        return {"chat": chat, "folder_id": None, "pinned": False, "archived": False, "created_at": created, "updated_at": updated,
                "current_message_id": chain[-1],
                "meta": {"tags": tags, "claude_uuid": conv_id, "source": SOURCE, "account": self.account}}

    def convert(self, conv):
        ms = conv.get("chat_messages") or []
        title = (conv.get("name") or "").strip() or (conv.get("summary") or "").strip().split("\n")[0][:80] or "Claude chat"
        created = ts(conv.get("created_at"), int(time.time()))
        updated = ts(conv.get("updated_at"), created)
        nodes = {}
        for m in ms:
            c = self.message(m)
            if c:
                c["ts"] = c["ts"] or created
                nodes[m["uuid"]] = c
        if not nodes:
            self.stats["conversations_without_messages"] += 1
            return []
        by_id = {m["uuid"]: m for m in ms}

        def visible_parent(u):
            p, hops = by_id[u].get("parent_message_uuid"), 0
            while p and p in by_id and hops < 100000:
                if p in nodes:
                    return p
                p, hops = by_id[p].get("parent_message_uuid"), hops + 1
            return None

        vpar = {u: visible_parent(u) for u in nodes}
        leaf = max(nodes, key=lambda u: (nodes[u]["ts"], list(by_id).index(u)))  # newest visible message ends the chat
        main, u, hops = set(), leaf, 0
        while u is not None and hops < 100000:
            main.add(u)
            u, hops = vpar[u], hops + 1

        def top(u):
            hops = 0
            while vpar[u] is not None and vpar[u] not in main and hops < 100000:
                u, hops = vpar[u], hops + 1
            return u

        groups, branch = {}, set()
        for u in nodes:
            if u in main:
                continue
            t = top(u)
            if vpar[t] in main:
                branch.add(u)
            else:
                groups.setdefault(t, set()).add(u)
        forms = [self.form(conv["uuid"], title, created, updated, {u: nodes[u] for u in main | branch}, vpar, leaf)]
        self.stats["chats"] += 1
        self.stats["branch_messages"] += len(branch)
        for k, (t, members) in enumerate(sorted(groups.items(), key=lambda kv: (nodes[kv[0]]["ts"], kv[0])), 1):
            sub = {u: nodes[u] for u in members}
            cur = max(sub, key=lambda x: (sub[x]["ts"], x))
            first = min(c["ts"] for c in sub.values())
            forms.append(self.form(f"{conv['uuid']}#frag{k}", f"{title} (recovered fragment {k})", first, max(c["ts"] for c in sub.values()), sub, vpar, cur))
            self.stats["fragment_chats"] += 1
            self.stats["fragment_messages"] += len(sub)
        return forms

    def convert_design(self, d):
        msgs = {}
        order, prev = [], None
        for m in d.get("messages") or []:
            c = m.get("content") or {}
            text = (c.get("content") or "").strip()
            ch = c.get("turnChanges") or {}
            changes = "; ".join(f"{k}: {', '.join(v)}" for k, v in ch.items() if v)
            if changes:
                text = (text + "\n\n" if text else "") + f"_[design files {changes}]_"
            if not text:
                self.stats["empty_messages_dropped"] += 1
                continue
            role = "user" if m.get("role") == "user" else "assistant"
            uid = m.get("uuid") or c.get("id") or uuid.uuid4().hex
            msgs[uid] = {"role": role, "content": text, "files": [], "ts": ts(m.get("created_at") or c.get("timestamp"), 0)}
            order.append(uid)
        if not order:
            return []
        created = ts(d.get("created_at"), msgs[order[0]]["ts"])
        for uid in order:
            msgs[uid]["ts"] = msgs[uid]["ts"] or created
        vpar = {u: (order[i - 1] if i else None) for i, u in enumerate(order)}
        proj = (d.get("project") or {}).get("name")
        title = ((d.get("title") or "").strip() or "Design chat") + (f" · {proj}" if proj else "")
        self.stats["design_chats"] += 1
        return [self.form(d["uuid"], f"[Design] {title}", created, ts(d.get("updated_at"), created), msgs, vpar, order[-1], extra_tags=("claude-design",))]


# ----------------------------------------------------------------------------------------------- driver
def existing_claude_ids():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    out = set()
    for (meta,) in con.execute("select meta from chat"):
        try:
            cid = (json.loads(meta) if meta else {}).get("claude_uuid")
        except Exception:
            cid = None
        if cid:
            out.add(cid)
    return out


def rollback(api):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    ids = []
    for cid, meta in con.execute("select id, meta from chat"):
        try:
            if (json.loads(meta) if meta else {}).get("source") == SOURCE:
                ids.append(cid)
        except Exception:
            pass
    for cid in ids:
        api.delete(f"/api/v1/chats/{cid}")
    state = json.load(open(STATE_PATH, encoding="utf-8")) if os.path.exists(STATE_PATH) else {"uploads": {}}
    n = 0
    for info in state["uploads"].values():
        try:
            api.delete(f"/api/v1/files/{info['id']}")
            n += 1
        except RuntimeError as e:
            print("  file delete failed:", str(e)[:120])
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
    print(f"rolled back: {len(ids)} chats, {n} uploaded files")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--account", help="short label used in the tag claude-<account>, e.g. ereuna or gmail")
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", help="comma-separated Claude conversation uuids")
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--no-design", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    if a.rollback:
        token, _ = mint_token()
        return rollback(Api(a.api, token))
    if not a.src or not a.account:
        ap.error("--src and --account are required")

    convs = json.load(open(os.path.join(a.src, "conversations.json"), encoding="utf-8"))
    convs.sort(key=lambda c: c.get("created_at") or "")
    designs = []
    if not a.no_design:
        for f in sorted(glob.glob(os.path.join(a.src, "design_chats", "*.json"))):
            designs.append(json.load(open(f, encoding="utf-8")))
    print(f"export: {len(convs)} conversations, {len(designs)} design chats (account {a.account})")
    api = user_id = None
    done = set()
    if not a.dry_run:
        token, user_id = mint_token()
        api = Api(a.api, token)
        done = existing_claude_ids()
        print(f"already in Open WebUI (by claude_uuid): {len(done)}")
    cv = Converter(api, user_id or "", a.account, a.dry_run)
    todo = [("c", c) for c in convs if c["uuid"] not in done] + [("d", d) for d in designs if d["uuid"] not in done]
    if a.only:
        wanted = set(a.only.split(","))
        todo = [t for t in todo if t[1]["uuid"] in wanted]
    if a.limit:
        todo = todo[: a.limit]
    print(f"to process: {len(todo)}")
    forms, kept, imported, t0 = [], [], 0, time.time()

    def flush():
        nonlocal forms, imported
        if forms and api:
            res = api.import_chats(forms)
            imported += len(res or [])
            cv.save_state()
            print(f"  imported {imported}  ({time.time() - t0:.0f}s)  uploads {cv.stats['uploads']}", flush=True)
        forms = []

    for kind, obj in todo:
        for f in (cv.convert(obj) if kind == "c" else cv.convert_design(obj)):
            if a.dry_run:
                kept.append(f)
            else:
                forms.append(f)
                if len(forms) >= a.batch:
                    flush()
    flush()
    if api:
        cv.save_state()
    print("stats:", dict(cv.stats))
    if a.dry_run and a.out:
        json.dump(kept, open(a.out, "w", encoding="utf-8"))
        print(f"wrote {len(kept)} converted chats to {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")
    if not a.dry_run:
        print(f"DONE: imported {imported} chats")


if __name__ == "__main__":
    sys.exit(main())
