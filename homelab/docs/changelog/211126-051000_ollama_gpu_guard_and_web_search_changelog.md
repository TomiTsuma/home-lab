# Changelog — Ollama CPU-fallback fix (GPU guard) + Open WebUI web search (aeacus-dl)

**Date:** 2026-11-21
**Host:** `aeacus-dl` (Proxmox VM 101), stack in `homelab/ai/open-webui/`
**Trigger:** "Ollama models in Open WebUI are too slow — suspect they're not using the GPU" + "give Open WebUI full web access for research".

---

## 1. Ollama was running on CPU — FIXED

### Symptom
`ollama ps` → `qwen3.5:9b  100% CPU`, `/api/ps` → `size_vram: 0`, VM load average 8–9 on 8 vCPUs,
`nvidia-smi` showing 1 MiB used / no processes. No error visible in Open WebUI.

### Root cause
Ollama runs GPU discovery once at container start: it launches `llama-server` against each backend
(`vulkan`, `cuda_v12`, `cuda_v13`) under a **30 s watchdog**. Container log for the 2026-11-20 boot:

```
09:10:37 llama-server GPU discovery watchdog timed out  [.../vulkan]
09:11:08 llama-server GPU discovery watchdog timed out  [.../cuda_v12]
09:11:38 llama-server GPU discovery watchdog timed out  [.../cuda_v13]
09:11:38 inference compute id=cpu library=cpu
```

The probes time out because **disk reads are extremely slow**, not because of the GPU:
- Raw CUDA via ctypes on the VM: `cuInit` 0.49 s + `cuCtxCreate` 0.13 s → GPU/passthrough healthy.
- `libggml-cuda.so` (250 MB) read with `O_DIRECT`: **17.1 MB/s** (~15 s for one file; `cuda_v13/` is 807 MB).
- Guest `/proc/pressure/io`: `full avg60=75%`; `vmstat` showed 1–3 tasks blocked on I/O.
- On the Proxmox host, **all three VM system disks (100, 101, 102) are thin LVs on one ST3500414CS
  500 GB HDD** (`local-lvm`). SMART is healthy (0 reallocated/pending) — it is simply contended.
- A plain `docker restart ollama` on an idle VM reproduced the CPU fallback, so this is not only a
  boot-time race.
- Pre-reading the libs into page cache (86 s cold, 0 s warm) then restarting → `library=CUDA`. Confirmed.

The previously documented causes (systemd cgroup driver, persistence mode off) were re-checked and are
still fixed (`Cgroup Driver: cgroupfs`, `Persistence-M: On`).

### Fix
- `docker-compose.yml` (ollama): `OLLAMA_VULKAN=0` (skip one probe), json-file log rotation.
- New `ollama-gpu-guard.sh` + `systemd/ollama-gpu-guard.{service,timer}` + `install-gpu-guard.sh`:
  every 15 min (and 3 min after boot) check the latest `inference compute` line; if CPU → pre-warm CUDA
  libs into page cache → restart container → re-verify (max 3 attempts). Installed to
  `/usr/local/sbin/` + `/etc/systemd/system/`, timer enabled.
- Note: the guard's CPU→restart path was validated manually (same steps by hand); the script itself has
  so far only run its "OK: on CUDA" branch.

### Verification
```
inference compute id=0 library=CUDA compute=8.6 name=CUDA0 description="NVIDIA GeForce RTX 3060"
NAME        SIZE    PROCESSOR  CONTEXT  UNTIL
qwen3.5:9b  6.1 GB  100% GPU   16384    Forever
eval: 45.2 tokens/s (CPU fallback was the cause of the slowness)
[ollama-gpu-guard] OK: ollama is on CUDA
```

### Still slow: cold model load (NOT fixed — needs hardware)
Loading the 5.6 GB model takes **170–270 s** for the same disk reason. `OLLAMA_KEEP_ALIVE=-1` means this is
paid once per container restart, but switching models in Open WebUI pays it each time.
**Recommended:** an SSD for the Proxmox host (even a 256–500 GB SATA SSD) holding VM 101's disk or at
least the `ollama` volume. This removes both the load delay and the discovery-timeout class of bug.

## 2. Context window raised
`OLLAMA_CONTEXT_LENGTH=16384` (Ollama default on <24 GiB VRAM GPUs is 4096, which web-search results and
tool schemas overflow — Ollama truncates silently). qwen3.5:9b at 16k = 6.8 GB VRAM. 14B models at 16k
are tight on 12 GB; lower `num_ctx` per model in Open WebUI if they spill to CPU.

## 3. Open WebUI web search — ADDED
- New `searxng` service (self-hosted open-source metasearch; no host port; digest-pinned), config in
  `searxng/settings.yml`, secret `SEARXNG_SECRET` in `.env` (generated on the host, chmod 600).
- Open WebUI env: `ENABLE_WEB_SEARCH=true`, `WEB_SEARCH_ENGINE=searxng`,
  `SEARXNG_QUERY_URL=http://searxng:8080/search?q=<query>`, `WEB_SEARCH_RESULT_COUNT=5`,
  `WEB_SEARCH_CONCURRENT_REQUESTS=5`.
- Engine tuning (tested from this IP): upstream default mix → **0 results** (DuckDuckGo CAPTCHA, Google CSE
  timeout). Brave returned broken `http:///go/v1/...` URLs for 60/78 results. Qwant: access denied.
  Kept: google, bing, startpage, mojeek, wikipedia, github, arxiv, stackexchange; timeouts 6 s / 12 s max.
- Verified: 20 clean results / 0.8 s from SearXNG; Open WebUI `/api/v1/retrieval/process/web/search`
  → status True, top hits ollama.com / github.com/ollama/ollama/releases (~30 s incl. page fetch + embed).

### Alternatives considered
| Option | Why not chosen |
|---|---|
| Tavily / Brave Search API / Serper | Paid/keyed SaaS; user prefers open-source; SearXNG is free + private |
| Open WebUI `duckduckgo` engine (no key) | DDG serves CAPTCHA to this IP — same failure as in SearXNG |
| `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=true` | Faster, but dumps full pages into a 16k context; revisit after an SSD/upgrade |

## 4. Not done / recommended next
- [ ] SSD for VM 101 (root cause of both CPU fallback and slow loads).
- [ ] Set Function Calling = Native on tool-capable models (qwen3.5:9b, Claude via LiteLLM) for agentic search.
- [ ] Upgrade Open WebUI from 0.6.41 and pin a version (`:main` is unpinned; newer releases default to
      native tool calling and add Open Terminal). Back up the `open-webui` volume first.
- [ ] MCP tool servers (filesystem/fetch/git/playwright via `mcpo` or native Streamable HTTP) — see the
      OpenClaw/MCP assessment in the session notes; OpenClaw itself is NOT recommended for this.
- [ ] Remove obsolete `version:` key from the compose file (compose warns on every command).
- Changes are on the aeacus-dl checkout and in the local repo working tree — **not committed/pushed**.
