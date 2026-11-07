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

## GPU gotchas (read before troubleshooting a "hang" or "CPU fallback")

These cost a full debugging session to find. In order of how confusing they
are to diagnose:

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
