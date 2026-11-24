import json, sys, time, uuid, urllib.request
sys.path.insert(0, "/tmp/bench")
from owui_bench_lib import api, token, BASE

model, tools = sys.argv[1], sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else []
prompt = "Using the Gitea tools, list the repositories owned by the user aeacus and tell me which one was updated most recently. Answer with the repository name and one sentence."
now = int(time.time()); u, a = str(uuid.uuid4()), str(uuid.uuid4())
um = {"id": u, "parentId": None, "childrenIds": [a], "role": "user", "content": prompt, "timestamp": now, "models": [model]}
am = {"id": a, "parentId": u, "childrenIds": [], "role": "assistant", "content": "", "model": model, "modelName": model, "modelIdx": 0, "timestamp": now}
cid = api("POST", "/api/v1/chats/new", {"chat": {"title": "dbg", "models": [model], "params": {}, "history": {"messages": {u: um, a: am}, "currentId": a}, "messages": [um, am], "tags": [], "timestamp": now * 1000}})["id"]
payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True, "tool_ids": tools, "chat_id": cid, "id": a, "parent_id": u, "session_id": "dbg", "features": {"memory": True}, "params": {}}
req = urllib.request.Request(BASE + "/api/chat/completions", method="POST", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
t0 = time.time(); r = urllib.request.urlopen(req, timeout=120); print("POST ->", r.status, r.read()[:120])
last = None
while time.time() - t0 < 420:
    m = api("GET", f"/api/v1/chats/{cid}")["chat"]["history"]["messages"].get(a) or {}
    out = m.get("output") or []
    sig = (len(out), m.get("done"), len(m.get("content") or ""))
    if sig != last:
        last = sig
        print(f"t+{time.time()-t0:5.0f}s done={m.get('done')} content_len={sig[2]} output=", [f"{o.get('type')}{(':' + str(o.get('name'))) if o.get('name') else ''}{('(' + o['status'] + ')') if o.get('status') else ''}" for o in out][-6:])
    if m.get("done"):
        print("FINAL:", (m.get("content") or "")[:200].replace("\n", " ")); break
    time.sleep(5)
else:
    print("TIMED OUT; statusHistory:", json.dumps(m.get("statusHistory"))[:300])
api("DELETE", f"/api/v1/chats/{cid}")
