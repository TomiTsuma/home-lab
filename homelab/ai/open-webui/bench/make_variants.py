"""Create the benchmark model variants in Open WebUI (run INSIDE the open-webui container). Idempotent.

All variants carry NO default tools (the benchmark passes the tool pack for each task, like a user switching tools on per chat) and share
the same trimmed built-in tools and no skills, so only the knob under test differs:
  bench-q35-agent        qwen3.5:9b, thinking OFF
  bench-q35-agent-think  qwen3.5:9b, thinking ON (the model's default behaviour)
  bench-gemma4-agent     gemma4:latest, 32k context
  bench-haiku-agent      claude-haiku-5-5 (reference)
"""
import sys
sys.path.insert(0, "/tmp/bench")
from owui_bench_lib import api

BUILTIN = {"chats": False, "memory": False, "web_search": True, "files": False, "time": True, "knowledge": False,
           "image_generation": False, "video_generation": False, "subagents": False,
           # without this the model is offered list_available_skills, calls it, and several models then end the turn with an empty answer
           "skills": False}
VARIANTS = [
    ("bench-q35-agent", "qwen3.5:9b", "qwen3.5:9b bench: thinking off", {"function_calling": "native", "think": False}),
    ("bench-q35-agent-think", "qwen3.5:9b", "qwen3.5:9b bench: thinking on", {"function_calling": "native", "think": True}),
    ("bench-gemma4-agent", "gemma4:latest", "gemma4 bench", {"function_calling": "native", "num_ctx": 32768}),
    ("bench-ministral-agent", "ministral-3:14b", "ministral-3 14b bench", {"function_calling": "native", "num_ctx": 24576}),
    ("bench-qwen3-14b-agent", "qwen3:14b", "qwen3 14b bench", {"function_calling": "native", "num_ctx": 24576, "think": False}),
    ("bench-haiku-agent", "claude-haiku-5-5", "claude haiku 5.5 bench (reference)", {"function_calling": "native"}),
]
for vid, base, name, params in VARIANTS:
    meta = {"toolIds": [], "skillIds": [], "builtinTools": BUILTIN, "description": "benchmark variant (safe to delete)",
            "capabilities": {"builtin_tools": True, "memory": False, "web_search": True, "file_upload": True}}
    form = {"id": vid, "base_model_id": base, "name": name, "meta": meta, "params": params, "is_active": True}
    try:
        ex = api("GET", f"/api/v1/models/model?id={vid}")
    except RuntimeError:
        ex = None
    try:
        api("POST", "/api/v1/models/model/update" if ex and ex.get("id") else "/api/v1/models/create", form)
        print("ok", vid, "<-", base, params)
    except RuntimeError as e:
        print("FAILED", vid, str(e)[:200])
