import json, sys, time, uuid, urllib.request, urllib.error
sys.path.insert(0, "/tmp/bench")
from owui_bench_lib import api, token, BASE

model = sys.argv[1]
print("model entry:", json.dumps({k: v for k, v in (api("GET", f"/api/v1/models/model?id={model}") or {}).items() if k in ("id", "base_model_id", "params", "is_active")}))
ids = [m["id"] for m in api("GET", "/api/models")["data"]]
print("listed in /api/models:", model in ids, "| base listed:", (api("GET", f"/api/v1/models/model?id={model}") or {}).get("base_model_id") in ids)
now = int(time.time()); u, a = str(uuid.uuid4()), str(uuid.uuid4())
um = {"id": u, "parentId": None, "childrenIds": [a], "role": "user", "content": "Say hello in five words.", "timestamp": now, "models": [model]}
am = {"id": a, "parentId": u, "childrenIds": [], "role": "assistant", "content": "", "model": model, "modelName": model, "modelIdx": 0, "timestamp": now}
cid = api("POST", "/api/v1/chats/new", {"chat": {"title": "dbg", "models": [model], "params": {}, "history": {"messages": {u: um, a: am}, "currentId": a}, "messages": [um, am], "tags": [], "timestamp": now * 1000}})["id"]
payload = {"model": model, "messages": [{"role": "user", "content": "Say hello in five words."}], "stream": True, "tool_ids": [], "chat_id": cid, "id": a, "parent_id": u, "session_id": "dbg", "features": {}, "params": {}}
req = urllib.request.Request(BASE + "/api/chat/completions", method="POST", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        print("HTTP", r.status, r.headers.get("content-type"))
        body = b""
        for line in r:
            body += line
            if line.startswith(b"data: [DONE]") or time.time() - t0 > 90:
                break
        print("stream bytes:", len(body), "| head:", body[:300])
except urllib.error.HTTPError as e:
    print("HTTPError", e.code, e.read()[:400])
except Exception as e:
    print("EXC", repr(e)[:300])
for _ in range(10):
    m = api("GET", f"/api/v1/chats/{cid}")["chat"]["history"]["messages"].get(a) or {}
    print(f"t+{time.time()-t0:.0f}s done={m.get('done')} content={m.get('content')!r:.120} error={m.get('error')} keys={sorted(m.keys())}")
    if m.get("done"): break
    time.sleep(4)
api("DELETE", f"/api/v1/chats/{cid}")
