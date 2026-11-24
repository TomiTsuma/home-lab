# Agent model benchmark: what to run for long-range work (2026-11-23)

## Question
`qwen3.5:9b` "trips over everything". Fix the current model, or pick a new one? Prove the result on long-range research, coding, tests and reporting.

## Method
`bench/` runs 8 tasks through the real Open WebUI API (native function calling, real MCP servers, real chat persistence), each with an objective grader:

| Task | What it checks | Grader |
|---|---|---|
| T1 | Gitea tool lookup | live Gitea API ground truth (`lyre`) |
| T2 | GitHub tool lookup | live GitHub API (latest release tag + date + URL) |
| T3 | Write + run + fix code in sandbox | 14 hidden unit tests on `parse_duration` |
| T4 | Debug a buggy function with tests | 8 hidden unit tests on `merge_intervals` |
| T5 | Research across all repos + write `report.md` | per-repo last commit vs Gitea; file written |
| T6 | Multi-step web research | every cited URL fetched live |
| T7 | Report: calendar to slide deck | deck built, 18 calendar titles present |
| T8 | Long-context recall (85k chars) | 3 hidden codes |

## Results (final, healthy host)
| Config | Passed | Notes |
|---|---|---|
| Claude Haiku 5.5, agent profile (`claude-haiku-agent`) | **T1-T7 all pass** (T8 not measurable, see below) | 10-85 s per task, 0 stalls once the host was not swapping |
| qwen3.5:9b, thinking off, per-task tool packs, skills off | 3/8 (T1, T2, T8) | thrashes on code: 43 calls on T3, never converges |
| qwen3.5:9b, thinking on | 3/6 before I stopped it | 3 stalls (empty answers / no progress) |

Earlier local numbers (0/N for qwen think-off, qwen think-on, gemma4) were void: the grader was broken and Ollama was wedged at the time. They are not evidence about the models.

## Conclusion
A 9B local model is not the fix. Fixes that help (skills builtin off, per-task tool packs) lift it to reliable single-tool lookups but not to multi-step coding/research loops. Use Claude Haiku 5.5 for long-range work, keep `qwen3.5-9b-agent` for quick local lookups.

## Changes applied
- New Open WebUI model `claude-haiku-agent` ("Claude Haiku 5.5 - Agent (long tasks)"), default tools: Homelab (sandbox/web), Gitea, GitHub. Builtins trimmed, **skills builtin off**, memory on. Google/Gmail/Chlio/calendar tools stay opt-in per chat: attaching all eight servers by default made the first turn stall.
- `qwen3.5-9b-agent`: skills builtin turned off (the model was offered `list_available_skills`, called it, and ended turns with no answer).
- Deleted the `bench-*` variants and the bench chats.

## Bugs found and fixed along the way
1. Grader: `python -I` removed the script dir from `sys.path`, so `from sol import *` always failed and T3/T4 always scored `0/-1`. Now `-E -s`.
2. T5 setup crashed on empty Gitea repos (HTTP 409). Now skipped.
3. Harness: `/api/models` refresh now retries with a 300 s timeout.
4. Two benchmark runners were alive at once (a stale one survived) and fought over one GPU: always list `run_bench` processes before launching.
5. Ollama wedged mid-run (loaded model, generation hangs). Fix: `docker restart ollama`, then warm the model (cold load from the shared HDD takes minutes and can exceed Ollama's 5 min load timeout; retry once the file is in page cache).
6. The 9B with 65k context holds ~8.8 GB RSS on a 12 GB VM; pinned (`keep_alive -1`) it pushes the host into swap and Open WebUI starts timing out. Don't pin it while running other work.

## Known limit
T8 fails for Claude through the current Claude bridge: an 85k-char prompt returns an empty answer (short prompts work). Not investigated further; use local or a direct API path for huge pasted documents.

## Not conclusive
No gemma4 or other model run on the fixed harness; the local numbers were also taken on a memory-starved host. A 12 GB RAM VM, not only the model, limits local agent work.

## Follow-up (2026-11-23): can a local model replace Haiku? Not on this host yet
Goal: run the Haiku-level workload locally so Claude outages don't matter.

Host: aeacus-dl VM = RTX 3060 **12 GB VRAM**, **12 GB RAM**, 8 GB swap, models on a shared HDD (~17 MB/s cold reads, Ollama's 5 min load timeout).

Tried (fixed grader, skills off, per-task tool packs):
- `ministral-3:14b` (24k ctx): T1 pass in 436 s, T2 timed out, ~9 min/task. Reported footprint 19 GB with only ~10 GB in VRAM, so the rest spilled to RAM, swap filled (8/8 GB, 48% iowait) and the whole host (Open WebUI included) crawled. Not a quality verdict, a fit failure.
- `gemma4:12b`, `qwen3:14b`: pulled, but cold load from the HDD exceeded Ollama's load timeout, so never measured.
- `devstral-small-2` (24B): not pulled; cannot fit 12 GB VRAM.
- Earlier: `qwen3.5:9b` 3/8 (thinking off), 3/6 (thinking on).

Side effects seen while the host was swapping: Open WebUI timeouts, Claude models temporarily missing from the model picker (they were intact server-side; fixed itself after memory recovered).

Blockers to a fair local test (host, not model): (1) RAM: raise the VM to >=24 GB or free ~4 GB; (2) put Ollama's model dir on SSD/NVMe; (3) set `OLLAMA_LOAD_TIMEOUT=30m`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` so a 12-14B model fits fully on the GPU at 16-24k ctx. Then rerun `bench/run_bench.py` for `qwen3:14b`, `gemma4:12b`, `ministral-3:14b` (and `devstral-small-2` / MoE Qwen3-Coder if RAM allows). Pass bar: T1-T7 like `claude-haiku-agent`.

## Follow-up 2 (2026-11-23): memory change, incident, first usable local result
- Raised VM101 12->18 GB (VM100 16->10.5 GB). **Rolled back to 12 GB** after it starved TrueNAS (VM102): passthrough VMs pin all RAM, so the host (40 GB, already ~45 GB allocated) swapped VM102 out and TrueNAS was unreachable ~45 min. Recovered without a reset; pools ONLINE. See memory note `homelab-proxmox-truenas`.
- Kept: `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` in docker-compose.yml. With them `ministral-3:14b` at 24k ctx is **fully on the 12 GB GPU (10.2 GB)** instead of the 19 GB footprint that spilled to RAM. Cold loads from the HDD still take 15-18 min (use `OLLAMA_LOAD_TIMEOUT=15m`, already set; warm models before benchmarking).
- Result on the fixed suite, `ministral-3:14b` (thinking n/a, tool packs, skills off): **4/7 graded** (T1 lookup, T4 debug+tests, T6 web research, T8 long context pass; T2 GitHub lookup, T3 code-with-tests (8/14, did not run its code), T7 calendar->deck fail; T5 setup timed out twice). ~3 min/task, no stalls. Better than qwen3.5:9b (3/8) but not Haiku parity (T1-T7 all pass).
- Not run: qwen3:14b / gemma4:12b suites (stopped to protect the host). Next: run them in a quiet window with VM101 at 12 GB, only one model resident.
