"""Helpers to drive Open WebUI the way the web UI does (run INSIDE the open-webui container; stdlib + pyjwt only).

A "turn" = create a chat, send one user message with chat_id/message id so the server runs its normal tool loop and PERSISTS the transcript
(assistant `output` items: function calls, outputs, reasoning), stream until the answer is complete, then read the transcript back.
"""
import json, os, sqlite3, time, uuid, urllib.error, urllib.request
import jwt

BASE = "http://localhost:8080"
DB = "/app/backend/data/webui.db"
_UID = None


def token(ttl=6 * 3600):
    global _UID
    if _UID is None:
        _UID = sqlite3.connect(f"file:{DB}?mode=ro", uri=True).execute("select id from user where role='admin' order by created_at limit 1").fetchone()[0]
    return jwt.encode({"id": _UID, "exp": int(time.time()) + ttl}, os.environ["WEBUI_SECRET_KEY"], algorithm="HS256")


def api(method, path, body=None, timeout=120):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read()[:300].decode(errors='replace')}")


def model_tool_ids(model_id):
    m = api("GET", f"/api/v1/models/model?id={model_id}")
    return list(((m or {}).get("meta") or {}).get("toolIds") or [])


def run_turn(model, prompt, tool_ids=None, timeout=900, system=None, keep=False, stall=420):
    """-> dict(content, calls[list of names], errors, elapsed, usage, output, chat_id, finished)"""
    # refresh Open WebUI's cached model list; a model created a moment ago is otherwise "not found" and the request silently never starts.
    # The host swaps under memory pressure, so retry with a long timeout instead of crashing the whole task.
    for attempt in range(3):
        try:
            api("GET", "/api/models", timeout=300); break
        except Exception:
            time.sleep(20)
    now = int(time.time())
    uid_, aid = str(uuid.uuid4()), str(uuid.uuid4())
    user_msg = {"id": uid_, "parentId": None, "childrenIds": [aid], "role": "user", "content": prompt, "timestamp": now, "models": [model]}
    asst = {"id": aid, "parentId": uid_, "childrenIds": [], "role": "assistant", "content": "", "model": model, "modelName": model, "modelIdx": 0, "timestamp": now}
    chat = {"title": "bench " + prompt[:40], "models": [model], "params": {}, "history": {"messages": {uid_: user_msg, aid: asst}, "currentId": aid},
            "messages": [user_msg, asst], "tags": [], "timestamp": now * 1000}
    cid = api("POST", "/api/v1/chats/new", {"chat": chat})["id"]
    tool_ids = model_tool_ids(model) if tool_ids is None else tool_ids
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": msgs, "stream": True, "tool_ids": tool_ids, "chat_id": cid, "id": aid, "parent_id": uid_,
               "session_id": "bench-" + aid[:8], "features": {"memory": True}, "params": {}}
    t0 = time.time()
    err = None
    try:
        req = urllib.request.Request(BASE + "/api/chat/completions", method="POST", data=json.dumps(payload).encode(),
                                     headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            if "event-stream" in r.headers.get("content-type", ""):
                for line in r:  # drain a streamed answer; the saved message is read below either way
                    if time.time() - t0 > timeout or line.startswith(b"data: [DONE]"):
                        break
            else:
                r.read()
    except Exception as e:  # noqa: BLE001
        err = repr(e)[:200]
    # The server keeps running the tool loop in the background and saves the message; wait until it is marked done.
    m, finished = {}, False
    last_sig, last_change = None, time.time()
    while time.time() - t0 < timeout:
        try:
            m = api("GET", f"/api/v1/chats/{cid}")["chat"]["history"]["messages"].get(aid) or {}
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:200]
        if m.get("done"):
            finished = True
            break
        sig = (len(m.get("output") or []), len(m.get("content") or ""))
        if sig != last_sig:
            last_sig, last_change = sig, time.time()
        elif time.time() - last_change > stall:  # nothing new for `stall` seconds: the run is stuck, do not wait out the whole timeout
            err = (err or "") + f" stalled {stall}s with no progress"
            break
        time.sleep(3)
    elapsed = time.time() - t0
    out = m.get("output") or []
    calls = [o.get("name") for o in out if o.get("type") == "function_call"]
    errors = [str(o.get("output"))[:160] for o in out if o.get("type") == "function_call_output" and ("rror" in json.dumps(o.get("output"))[:300])]
    res = {"content": m.get("content") or "", "calls": calls, "errors": errors, "elapsed": round(elapsed, 1), "usage": m.get("usage"),
           "output": out, "chat_id": cid, "finished": finished, "err": err}
    if not keep:
        try:
            api("DELETE", f"/api/v1/chats/{cid}")
        except Exception:
            pass
    return res
