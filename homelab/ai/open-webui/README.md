# Open WebUI stack (Ollama + LiteLLM + Open WebUI)

Deployed on `aeacus-dl` (Proxmox VM 101, `192.168.100.17` LAN / Tailscale
`100.101.189.31`), the GPU box with an RTX 3060 passed through. Three
containers in one compose stack:

| Service | Image | Port | Role |
|---|---|---|---|
| `ollama` | `ollama/ollama:0.30.10` | `11434` | Runs local models (currently `qwen3.5:researcher`, `qwen3.5:9b`, `gemma4`, `qwen2.5:14b`, `DeepSeek-R1`) on the GPU. |
| `litellm` | `ghcr.io/berriai/litellm:main-stable` | `4000` | OpenAI-wire-protocol proxy in front of Claude. |
| `open-webui` | `ghcr.io/open-webui/open-webui:main` | `3005` | The chat UI, talks to both of the above. |

## Why a LiteLLM proxy exists at all

Open WebUI's "OpenAI connection" type only speaks the **OpenAI wire protocol**
(`Authorization: Bearer ...`, `POST /v1/chat/completions`). Anthropic's API is
a **different protocol** (`x-api-key` header, `anthropic-version` header,
`POST /v1/messages`). Pointing an Open WebUI OpenAI-connection straight at
`https://api.anthropic.com/v1` looks plausible but never works — not because
of a bad API key, but because the two sides are speaking different languages.
LiteLLM translates between them, so Open WebUI's OpenAI connection just needs
to point at `http://litellm:4000/v1` with `LITELLM_MASTER_KEY` as the bearer
token.

## Deploying from scratch

```sh
cd homelab/ai/open-webui
cp .env.example .env            # fill in ANTHROPIC_API_KEY and LITELLM_MASTER_KEY
chmod 600 .env
./host-gpu-prep.sh              # one-time: persistence mode + cgroup driver fix (see script)
docker compose up -d
```

Wiring Open WebUI's Anthropic connection is a one-time step through the admin
UI (Settings → Connections → OpenAI API): base URL `http://litellm:4000/v1`
(from inside the container network, since Open WebUI calls it by Docker DNS
name), API key = `LITELLM_MASTER_KEY`.

To add/remove Claude models, edit `litellm-config.yaml`'s `model_list` and
restart the `litellm` service — no code changes needed.

## Access (LAN + Tailscale)

Open WebUI is published on `0.0.0.0:3005`:

- `http://192.168.100.17:3005` (LAN)
- `http://100.101.189.31:3005` (tailnet IP)
- `https://aeacus-dl.tailaee7b8.ts.net` (tailnet HTTPS with a valid cert, through
  `tailscale serve --https=443` → `127.0.0.1:3005`; the browser needs HTTPS for the mic and clipboard)

It is not exposed to the internet: there is no port-forward and no Cloudflare hostname, and
signup is disabled. Don't add a `tailscale serve --http=3005` rule: serve routes by hostname,
so the rule catches requests to the tailnet IP on port 3005 and returns 404 (this happened
on 2026-11-21). `mcp-tools` stays unpublished whatever the setting here. Check the serve
config with `tailscale serve status`; tailscaled keeps it across reboots.
`WEBUI_SECRET_KEY` is pinned in `.env` so recreating the container doesn't log you out.

## Web search (Exa)

Open WebUI's built-in web search uses **Exa** (`WEB_SEARCH_ENGINE=exa`, key `EXA_API_KEY`
in `.env`). These are PersistentConfig: once changed in Admin → Settings → Web Search,
the DB value wins over the env. (A self-hosted SearXNG was tried first on 2026-11-21 and
replaced by Exa at the owner's request; scrape.do is available as an alternative search
tool via the MCP server below.)

Using it: toggle **Web Search** under the message box, or — better — set the model's
*Function Calling* to **Native** (Admin → Settings → Models → Advanced Params) so the
model can search, fetch and call MCP tools itself, repeatedly.

## Memory and referencing other chats (v0.13.4)

- **Built-in tools** (used automatically when a model's Function Calling is *Native*, the
  default since 0.10): `add_memory` / `search_memories` / `update_memory`, `search_chats` /
  `view_chat` (lets the model look up and read your earlier conversations), notes, knowledge.
- **Background review** (`ENABLE_MEMORY_BACKGROUND_REVIEW=true`, every 6 turns): Open WebUI
  distils durable facts from the conversation into memory by itself.
- Review/edit memories: Settings → Personalization → Memory.
- Small local models use these tools less reliably than Claude; for "remember this" /
  "what did we decide in that other chat" tasks, Claude via LiteLLM is the dependable path.

## Context window / "responses randomly cut off"

The symptom is a reply that stops mid-sentence, or a tool loop that ends with no answer. It
means Ollama ran out of context: its log shows `stop processing: n_tokens = 16383, truncated = 1`.
Every step of a tool loop re-sends the system prompt, the tool schemas, the chat history and
all tool results so far. On 2026-11-21 one chat grew from 10.7k to 16.4k tokens over 6 steps
with a 16k window and was cut off.

What is in place:
- **qwen3.5:9b defaults to a 64k context inside Ollama.** It was re-tagged in place with
  `PARAMETER num_ctx 65536` (`ollama create qwen3.5:9b -f` a Modelfile whose `FROM` line is
  `qwen3.5:9b`). It needs 7.7 GB of VRAM and stays 100% on the GPU (its hybrid attention keeps
  the KV cache small). Set this in Ollama rather than per chat: if Open WebUI's background
  tasks asked for a different `num_ctx`, Ollama would reload the model (minutes on this disk).
  **`ollama pull qwen3.5:9b` resets it**, so re-apply after a pull:
  ```sh
  docker exec ollama sh -c 'printf "FROM qwen3.5:9b\nPARAMETER num_ctx 65536\n" > /tmp/M && ollama create qwen3.5:9b -f /tmp/M'
  ```
  Other models keep the global `OLLAMA_CONTEXT_LENGTH=16384`. qwen2.5:14b would not fit at 64k.
- **Open WebUI model override for qwen3.5:9b** (Workspace → Models): Function Calling =
  Native. Built-in tool groups are limited to chats, memory, web_search, files and time.
  knowledge, image/video generation and subagents are off: there are no knowledge bases or
  image backends, and every enabled group's schemas are sent with every request.
- **mcp-tools output caps:** `fetch_url` 6k chars, `parse_document` 16k (the full text is
  saved to `parsed/`), `read_file` and command output 12k, scrape.do 5 results.
- Check: `docker exec ollama ollama ps` should show `CONTEXT 65536`; grep the ollama log for
  `truncated = 1`.

## Community plugins (`plugins/`)

Installed 2026-11-21. The plugins run arbitrary Python inside Open WebUI, so each file was read
before install (a security review of its network hosts, subprocess/eval, database writes and
environment reads) and **pinned by sha256** in `plugins/manifest.json`. `install_plugins.py`
refuses any file whose hash differs from the reviewed one. The source files are vendored in
`plugins/src/`, each with its upstream repo and commit (or the openwebui.com post id) recorded
in the manifest.

Re-install or update (idempotent):
```sh
docker cp plugins open-webui:/tmp/plugins
docker exec open-webui python3 /tmp/plugins/install_plugins.py /tmp/plugins/manifest.json
```

| Plugin | Type | Scope | Settings applied / notes |
|---|---|---|---|
| Async Context Compression | Filter | global | `summary_model=qwen3.5:9b` (it otherwise falls back to **gpt-4o-mini**!), 64k max context. Creates its own `chat_summary` table. |
| Markdown Normalizer | Filter | global | LaTeX fix and Mermaid fix **off** (the LaTeX fix can corrupt code; Mermaid Doctor owns diagrams). |
| Mermaid Doctor | Filter | global | Few-shot injection **off** (it otherwise adds about 50 lines to every prompt and breaks on image messages). |
| Token Usage Display | Filter | global | models.dev, OpenRouter and balance lookups off, so it makes no outbound calls. |
| Export to Word / Excel | Action | global | Title comes from the chat title (no LLM call). Word export loads mermaid and jszip from public CDNs into the browser; its `latex2mathml==3.81.1` and `mathml2omml==0.0.2` were **pinned locally** (upstream was unpinned). The packages are pip-installed at container start. |
| Download Code Blocks, Regenerate Follow-ups, Pin Message | Action | global | Defaults; follow-up event emitter off. |
| Add Web Sources to Knowledge | Action | global | At most 8 URLs, with confirmation. Server-side fetch goes through Open WebUI's SSRF-safe loader. Saved web content is untrusted. |
| Inline Visualizer (+ Skill `visualize`) | Tool | default-on, all models | `security_level=strict`. **Needs the user setting "iframe Sandbox Allow Same Origin"** (see the warning below). |
| Reveal.js Slide Deck Builder | Tool | default-on, all models | **Needs Allow Same Origin.** Model HTML is inserted unsanitised. |
| Email Composer | Tool | default-on, all models | Safe. No network; sends via mailto or a `.eml` download. |
| Sub Agent | Tool | default-on, all models | Up to 20 iterations, 2 parallel agents, model qwen3.5:9b. Overlaps the built-in `delegate_task`. |
| arXiv Search | Tool | default-on, all models | Queries go to the third-party searchthearxiv.com. |
| Ollama Model Card Generator | Tool | default-on, all models (admin) | Installed **defanged**: every `*_dry_run` valve is on and `hide_base_models=true`. Live, it rewrites model entries and can delete the override that holds qwen3.5:9b's 64k and tool settings. Back up `webui.db` before turning any dry-run off. |

**Skipped:**
- Ask User: duplicates the built-in `ask_user`.
- MCP App Bridge: the MCP server is native and our server has no UI resources.
- Forward to Channel: no Channels.
- Generate Image: no image engine.
- Thinking Toggle: llama.cpp only, and unmaintained.
- OpenRouter tracker: no OpenRouter.
- Forward to Webhook: no webhook target.
- Theme Designer and User Disclaimer: single user.

**Allow Same Origin warning.** With that setting on, HTML the model generates runs in Open
WebUI's origin. It can read your admin token from localStorage and call the API as you. Content
the model saw from a web page can therefore steer it into stealing the session. It is OFF. If
you enable it for Inline Visualizer or Reveal.js, prefer a separate non-admin account.

**Everything is default-on for every model (owner's request, 2026-11-21).** Every filter and action is global. Every model's override has all 7 tools (Homelab MCP + 6 plugin tools) in `toolIds` and the `visualize` skill in `skillIds`. Untick a tool per chat with ➕ if you don't need it. Context cost: the plugin tool schemas add about 4k tokens to every request (Model Card Generator alone is about 2.1k). That is comfortable on qwen3.5:9b (64k) and tighter on the 16k models. Re-apply after adding models:
```sh
docker cp plugins/default_tools_all_models.py open-webui:/tmp/d.py
docker exec -e DEFAULT_SKILL_IDS=visualize open-webui python3 /tmp/d.py server:mcp:homelab inline_visualizer email_composer revealjs_slide_deck_builder ollama_model_card_generator sub_agent arxiv_search
```

**Default tools on every model (earlier, Homelab only).** Each model has an Open WebUI override with
`toolIds = ["server:mcp:homelab"]`, so Homelab Tools is pre-selected in every new chat (you can
still untick it per chat). Re-run `plugins/default_tools_all_models.py` after adding new
models. It only merges, and keeps existing params and filters.

## Upgrading / rollback

The image is pinned (`v0.13.4`). Upgrades run **one-way** DB migrations, so:

```sh
docker run --rm -v open-webui_open-webui:/data:ro -v ~/backups:/b alpine:3.20 \
  tar czf /b/open-webui-data-$(date -u +%Y%m%d-%H%M%S).tgz -C /data .
# edit the image tag in docker-compose.yml, then:
docker compose up -d open-webui
```

Rollback: stop the container, restore the tarball into the volume, and set the image back.
The 0.6.41 → 0.13.4 upgrade on 2026-11-21 left `~/backups/open-webui-data-0.6.41-20261121-030155.tgz`
and the old image tagged `open-webui-rollback:0.6.41`.

## MCP tool server (`mcp-tools/`)

Our own FastMCP server (Streamable HTTP), registered in Open WebUI as **Homelab Tools**
via `TOOL_SERVER_CONNECTIONS`. Enable it per chat (➕ → Tools) or per model (Workspace →
Models → Tools). Execution model adapted from `CoreOutlineV2/dss-agent/tools/mcp_server.py`
minus its financial tools.

| Tool | What it does |
|---|---|
| `run_command`, `run_python` | bash / Python 3.11 in a private per-user workspace (timeout, output cap) |
| `list_workspace`, `read_file`, `write_file` | workspace files (paths confined; symlink escapes rejected) |
| `list_uploaded_files`, `import_uploaded_file` | files attached in Open WebUI chats (fetched with the user's own token) |
| `parse_document` | **LlamaParse** extraction of PDFs/images/Office docs → markdown (saved to `parsed/`) |
| `publish_file` | upload to **s3.ereuna.org/openwebui-files**, return presigned URL + markdown (images also render inline) |
| `web_search_scrapedo`, `fetch_url` | alternative Google search + anti-bot page fetch via **scrape.do** |

### Security model ("only executable through Open WebUI")
dss-agent's server is safe only because it is **stdio** (the spawning process is the only
caller). Over HTTP that guarantee is rebuilt in layers:

1. **Network** — `mcp-tools` publishes no port; only containers on this compose network reach it.
2. **Identity** — Open WebUI uses `auth_type: session`, forwarding the logged-in user's own JWT;
   the server verifies it against Open WebUI's `/api/v1/auths/` on every request (401 otherwise).
3. **Authorisation** — exec tools require a role in `EXEC_ALLOWED_ROLES` (default `admin`).
4. **Containment** — commands run as a per-user UID (not the server's), in a `0700` workspace,
   with a scrubbed env (no API keys/S3 creds), timeout, and container limits (read-only rootfs,
   all caps dropped except SETUID/SETGID/CHOWN/DAC_OVERRIDE/FOWNER/KILL, no-new-privileges,
   4 GB RAM, 4 CPUs, 512 pids). Sandboxed code **does** have internet access by design.
5. **S3** — a dedicated MinIO user `openwebui-mcp` whose policy only allows bucket
   `openwebui-files` (not the MinIO root key). Objects: `<user-id>/YYYY/MM/DD/<rand>-<name>`;
   links expire (default 24 h, max 7 days). No lifecycle rule yet — objects persist.

Tests: `docker run --rm --entrypoint python homelab/mcp-tools:local -m pytest -q test_server.py`.
Rebuild after editing: `docker compose up -d --build mcp-tools`.

## GPU discovery guard (`ollama-gpu-guard.sh`)

Ollama decides GPU vs CPU **once**, at container start, and its probe has a
hard 30 s watchdog. On this VM's slow shared-HDD disk, reading the ~800 MB of
CUDA libraries often takes longer than that, so Ollama silently falls back to
CPU (`ollama ps` → `100% CPU`, ~10x slower) — this happened on the 2026-11-20
boot and also on a plain idle restart. Mitigations:

- `OLLAMA_VULKAN=0` in the compose file skips the Vulkan probe (one fewer 30 s
  window to fail in).
- `ollama-gpu-guard.sh` (installed by `sudo ./install-gpu-guard.sh` as a
  systemd timer: 3 min after boot, then every 15 min) checks the newest
  `inference compute` log line; if it says CPU, it pre-reads the CUDA libs into
  the page cache and restarts the container (up to 3 tries). Logs:
  `journalctl -u ollama-gpu-guard`.
- The real fix is faster storage for VM 101 (an SSD) — see the changelog.

## GPU gotchas (read before troubleshooting a "hang" or "CPU fallback")

These cost a full debugging session to find. In order of how confusing they
are to diagnose:

0. **(2026-11-21) Slow disk → GPU discovery timeout → CPU fallback.** See
   "GPU discovery guard" above. Check with
   `docker logs ollama 2>&1 | grep -a "inference compute" | tail -1` — `library=cpu`
   means this. `nvidia-smi` inside the container will still work fine, which
   is what makes it confusing.

1. **Docker's cgroup driver silently revokes GPU access.** If
   `docker info | grep Cgroup Driver` says `systemd`, then *any*
   `systemctl daemon-reload` on the host (which plenty of unrelated admin
   tasks trigger) rewrites the running containers' device cgroup allowlist
   and strips `/dev/nvidia*` access from them — the container keeps running,
   but `nvidia-smi` inside it starts failing with
   `Failed to initialize NVML: Unknown Error`, and Ollama silently falls back
   to CPU (`docker exec ollama ollama ps` will show `100% CPU` instead of
   `100% GPU`, with no error anywhere). `host-gpu-prep.sh` switches Docker to
   the `cgroupfs` driver, which doesn't have this problem. **If GPU access
   ever goes missing again, check the cgroup driver before anything else.**
2. **NVIDIA persistence mode is off by default.** Without it, CUDA context
   creation after a cold boot/driver reload can take long enough to blow past
   Ollama's internal ~30s GPU-discovery watchdog, which makes Ollama fall
   back to CPU for the exact same reason as above but from a different root
   cause. `host-gpu-prep.sh` fixes this too.
3. **A cold model load is slow and looks like a hang.** Models here load
   with `--no-mmap` (`ollama`'s default scheduling choice when a model should
   be fully GPU-resident), meaning a full read of the model file into RAM
   before it even starts moving layers to VRAM — for `qwen3.5:researcher`
   (6.6 GB) this is **~4 minutes**. During this window `nvidia-smi` shows
   0% utilization (expected — it hasn't started computing yet), which looks
   identical to a genuine hang. `OLLAMA_KEEP_ALIVE=-1` in the compose file
   means you only pay this cost once per container restart, not once per
   5-minute gap in traffic.
4. **The VM needs real RAM, not just VRAM.** `aeacus-dl` was originally sized
   at 6 GB RAM, which is *less than* the model file size (6.6 GB) — when GPU
   discovery failed and Ollama fell back to CPU, it couldn't even fit the
   model in system RAM, causing severe swap thrashing on top of the slowness.
   The VM is now sized at **12 GB** (see
   `../../infrastructure/proxmox/vms/create-vm-101-aeacus-dl.sh`). If you ever
   see `llama-server` stuck in `D` state (uninterruptible sleep,
   `ps -eo pid,stat,cmd`) together with `systemd-journald: Under memory
   pressure, flushing caches` in `dmesg`, this is memory pressure, not a GPU
   problem.
5. **A degraded PCIe link is not itself the problem.** `lspci -vvv` on the
   GPU may show `LnkSta: Speed 2.5GT/s (downgraded)` against a
   `LnkCap: 8GT/s` capability while the GPU is idle — this is normal ASPM
   power-saving and the link renegotiates to full speed under load (measured
   ~12 GB/s host↔device, ~110 GB/s on-GPU via a direct CUDA driver-API probe,
   bypassing Ollama entirely). Don't chase this unless you've also confirmed
   actual throughput is low under load. If the link *won't* come back up
   even under load after several guest power-cycles, a hardware-level reset
   can help (consumer GPUs often don't get a clean reset from a guest-only
   restart): with the VM stopped, `echo 1 > /sys/bus/pci/devices/<addr>/reset`
   on the Proxmox host.
6. **PCIe passthrough topology still matters, just isn't the whole story.**
   `machine: q35` + `hostpci0: ...,pcie=1` (real PCIe root port, not legacy
   i440fx PCI) is the correct, required config for this GPU — see
   `../../infrastructure/proxmox/pci-passthrough.md`. But on its own it did
   **not** fix the inference hang in this homelab's case; it took finding and
   fixing #1 and #4 above as well. Don't assume fixing the PCIe topology
   alone will resolve a passthrough compute hang.

### Diagnosing "is this actually a GPU problem?"

The fastest way to rule the GPU in or out, bypassing Ollama/llama.cpp
entirely, is to call the CUDA driver API directly via `ctypes` against
`/usr/lib/x86_64-linux-gnu/libcuda.so.1` (`cuInit` → `cuDeviceGet` →
`cuCtxCreate` → `cuMemAlloc`/`cuMemcpyHtoD`/`cuMemcpyDtoD` → timings). If that
completes quickly with real bandwidth numbers, the passthrough is fine and
the problem is in Ollama's GPU-discovery subprocess, Docker's GPU exposure to
the container, or VM memory — not VFIO/IOMMU.

## Deployment path

As of 2026-11-07, the live stack runs directly from this repo checkout on
`aeacus-dl` — `cd ~/home-lab/homelab/ai/open-webui && docker compose up -d`.
It previously ran from an ad-hoc `~/open-webui/` directory outside any git
repo (migrated here; the old directory is preserved as
`~/open-webui.superseded-by-home-lab-repo-20261107` in case anything was
missed, safe to delete once confirmed). `.env` lives alongside the compose
file in this checkout (gitignored via `**/.env` — never commit it).

**Editing config now takes effect directly**: `git pull` on `aeacus-dl`, then
`docker compose up -d` in this directory to apply. No more copying changes
between two locations.

## Known limits / not yet done
- No image digest pinning on `litellm`/`open-webui` (`:main-stable`/`:main`)
  — a `docker compose pull` could change behavior under you. Only `ollama` is
  pinned (`0.30.10`), because a newer Ollama version was one of several
  things tested (and ruled out) while debugging the GPU hang above.
- Open WebUI's Anthropic connection config lives in its own SQLite
  `webui.db` (`config` table), not in this compose file or `.env` — it's set
  once through the admin UI and isn't captured as code here.
