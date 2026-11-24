import json, sys
sys.path.insert(0, "/tmp/bench")
from owui_bench_lib import run_turn

model = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:9b"
r = run_turn(model, "Use the get_me tool and tell me my login name. Answer in one short sentence.", tool_ids=["server:mcp:gitea"], timeout=600)
print(json.dumps({k: r[k] for k in ("finished", "elapsed", "calls", "errors", "err", "usage")}, default=str)[:500])
print("ANSWER:", r["content"][:300].replace("\n", " "))
print("output item types:", [o.get("type") for o in r["output"]])
