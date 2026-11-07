# Changelog — Ollama GPU passthrough fix + Claude/LiteLLM integration (aeacus-dl)

**Date:** 2026-11-07
**Host:** `aeacus-dl` (Proxmox VM 101 on `aeacus-pve` / 100.124.198.112)
**Scope:** Open WebUI Anthropic connection, Ollama GPU inference, VM networking, VM resourcing, repo sync

---

## 1. Open WebUI → Claude (Anthropic) via LiteLLM — FIXED

### Root cause
Open WebUI's "OpenAI connection" was pointed directly at `https://api.anthropic.com/v1`.
Anthropic's endpoint speaks its **native Messages API** (`x-api-key` header, `/v1/messages`),
not the **OpenAI wire protocol** (`Authorization: Bearer`, `/v1/chat/completions`) that Open
WebUI's OpenAI client emits. This is a protocol mismatch, **not** an invalid API key — the key
was independently verified working via a direct `curl` against the real Anthropic API.

### Fix
Introduced a **LiteLLM proxy** container that accepts OpenAI-format requests and translates them
to native Anthropic calls. See `homelab/ai/open-webui/` for the resulting compose stack.

- `litellm-config.yaml` — maps `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`,
  `claude-fable-5-1` to `anthropic/*` upstreams.
- `.env` (chmod 600, not committed — see `.env.example`) — holds `ANTHROPIC_API_KEY` and
  `LITELLM_MASTER_KEY`.
- Open WebUI config patched in `webui.db` (`config` table, `openai` key) to point at
  `http://litellm:4000/v1` with the LiteLLM master key, replacing the direct Anthropic URL
  and the custom `x-api-key` / `anthropic-version` headers. (This lives in Open WebUI's own
  SQLite state, not in this repo as code.)

### Verification
- `GET http://localhost:4000/v1/models` → all 4 Claude models, HTTP 200.
- `POST /v1/chat/completions` (`claude-haiku-5-5`) → real response, HTTP 200.
- From inside the `open-webui` container: `GET http://litellm:4000/v1/models` → HTTP 200.

---

## 2. Ollama GPU inference — FIXED (44–45 tokens/sec)

### Symptom
Model weights loaded fully into VRAM (33/33 layers, 4812 MiB on CUDA0), then inference appeared
to hang indefinitely at 0% GPU utilisation. Intermittently, Ollama's GPU *discovery* probe also
timed out (`llama-server GPU discovery watchdog timed out`, `context deadline exceeded`) and it
silently fell back to CPU-only inference, with the model showing `100% CPU` in `ollama ps`.

### Root causes (three, stacked — see `homelab/ai/open-webui/README.md` "GPU gotchas" for the
### full write-up kept close to the code it applies to)

1. **VM memory starvation.** `aeacus-dl` had only 6 GB RAM while running a 6.6 GB model
   (`qwen3.5:researcher`, loaded with `--no-mmap`) plus three containers. The model alone
   exceeded total VM RAM, causing swap thrashing (`llama-server` stuck in D-state,
   `systemd-journald: Under memory pressure, flushing caches`), which also starved CUDA context
   creation during its ~30s discovery window.
   **Fix:** `memory: 6144` → `12288` (`homelab/infrastructure/proxmox/vms/create-vm-101-aeacus-dl.sh`).

2. **Docker's `systemd` cgroup driver silently revokes container GPU access.** With cgroups v2 +
   the systemd driver, any `systemctl daemon-reload` rewrites the running containers' device
   cgroup allowlist and strips `/dev/nvidia*` from them. The container keeps running, but
   `nvidia-smi` inside it starts failing with `Failed to initialize NVML: Unknown Error`. This
   was the most time-consuming false lead in this investigation, and was triggered
   self-inflicted mid-session (installing a systemd override for NVIDIA persistence mode).
   **Fix:** switched to the `cgroupfs` driver in `/etc/docker/daemon.json` — now scripted in
   `homelab/ai/open-webui/host-gpu-prep.sh`. Verified GPU access survives a deliberate
   `daemon-reload` afterward.

3. **NVIDIA persistence mode was off** (Ubuntu ships `nvidia-persistenced --no-persistence-mode`
   by default), making cold CUDA init slow enough to intermittently exceed Ollama's discovery
   watchdog. **Fix:** boot-persistent override, also in `host-gpu-prep.sh`.

A secondary, non-root-cause finding: the GPU had accumulated a degraded PCIe link state across
repeated VM power-cycles without a host reboot (`LnkSta: 2.5GT/s (downgraded)` vs
`LnkCap: 8GT/s`). A hardware-level reset (`echo 1 > .../reset` with the VM stopped) restored full
link speed; documented in `homelab/infrastructure/proxmox/pci-passthrough.md`.

### Also applied
- `OLLAMA_KEEP_ALIVE=-1` (was default 5m) — a cold model load takes ~4 minutes (`--no-mmap`
  forces a full 6.6 GB read before anything reaches VRAM), so keeping it resident avoids paying
  that cost on every gap in traffic.

### Verification
```
inference compute library=CUDA compute=8.6 name=CUDA0 description="NVIDIA GeForce RTX 3060"
libdirs=ollama,cuda_v13 driver=13.0 pci_id=0000:01:00.0 total="11.6 GiB" available="11.5 GiB"

NAME                 ID            SIZE     PROCESSOR   CONTEXT   UNTIL
qwen3.5:researcher   688c33f1a293  5.8 GB   100% GPU    8192      Forever

eval (generation)      : 44–45 tokens/second
prompt eval (cached)   : 1550 tokens/second (0.65 ms/token)
```
End-to-end from `minos` (the Chlio host), through the exact path Chlio uses:
```
POST http://192.168.100.17:11434/api/generate  -> HTTP 200
prompt "Reply with exactly: CHLIO_PATH_OK"     -> "CHLIO_PATH_OK"   @ 44.78 tok/s
```

### Hypotheses tested and REJECTED (recorded to avoid re-litigating)
| Hypothesis | Action taken | Result |
|---|---|---|
| i440fx/legacy-PCI passthrough | `qm set 101 --machine q35 --hostpci0 0000:01:00,pcie=1` | Correct config, kept — but did **not** fix the hang on its own |
| GeForce anti-virtualisation compute block | `qm set 101 --cpu x86-64-v2-AES,hidden=1` | **Made it worse** (broke CUDA discovery entirely) — reverted |
| Unhandled MSR accesses stalling the vCPU | `kvm.ignore_msrs=1` on host | No effect — reverted |
| Broken MSI/interrupt delivery | Verified IRQ remapping enabled on host; checked `/proc/interrupts` | Not the cause |
| GGML failing to auto-load the CUDA backend | Set `GGML_BACKEND_PATH` | Not the cause — Ollama selected `cuda_v13` regardless; removed |
| Degraded PCIe link throttling DMA | Measured actual throughput via direct CUDA probe | **Not a bottleneck** — ~12 GB/s H2D/D2H, ~111 GB/s D2D |

### Direct CUDA benchmark (via `libcuda.so` ctypes probe, bypassing Ollama entirely)
Proved the passthrough itself was always healthy — see `homelab/compute/nvidia/gpu-passthrough.md`
for the reusable probe snippet:
```
H2D pageable : 7.271 GB/s        D2H pinned : 12.236 GB/s
H2D pinned   : 11.968 GB/s       D2D on-GPU : 110.970 GB/s
small-op latency: 99.1 us/op
```

---

## 3. VM networking — FIXED (regression from the q35 migration)

### Symptom
After switching machine type i440fx → q35 (for #2 above's investigation), the guest booted
(balloon stats updating) but was completely unreachable: no SSH, no ping, and
`tcpdump -i tap101i0` on the host showed **zero** guest-originated packets.

### Root cause
The q35 change altered the PCI topology, so Linux's predictable-naming scheme renamed the NIC
from **`ens18`** to **`enp6s18`**. Netplan still referenced `ens18`, so systemd-networkd generated
`10-netplan-ens18.network` for a device that no longer existed; the real interface was left
unmanaged and DOWN.

### Fix
```sh
sudo sed -i 's/ens18/enp6s18/' /etc/netplan/50-cloud-init.yaml
sudo netplan apply
```

### Note
This VM has **no QEMU guest agent** and **no serial console** — when the network drops there is
no remote path in; recovery required the Proxmox noVNC console. Flagged in
`homelab/infrastructure/proxmox/vms/create-vm-101-aeacus-dl.sh` as a known gap for a future
rebuild (install `qemu-guest-agent`, consider netplan match-by-MAC).

---

## 4. Repo sync (this change)

- Synced the working `docker-compose.yml` / `litellm-config.yaml` from the ad-hoc
  `~/open-webui/` location on `aeacus-dl` into this repo's `homelab/ai/open-webui/`, cleaned up
  (removed stray inline edit-marker comments, added real documentation).
- Added `homelab/ai/open-webui/host-gpu-prep.sh` (persistence mode + cgroup driver fix) and
  `README.md` (full stack architecture + gotcha list).
- Removed the stale `homelab/ai/open-webui/docker-install.sh` (a plain `docker run` for
  Open WebUI alone, superseded by the compose stack and referencing a port mapping that no
  longer matches).
- Updated `homelab/infrastructure/proxmox/vms/create-vm-101-aeacus-dl.sh` and
  `homelab/infrastructure/proxmox/pci-passthrough.md` to capture the q35/pcie=1 requirement,
  the memory bump, the disk resize, and the GPU hardware-reset procedure.
- Fleshed out previously-empty stub docs: `homelab/compute/nvidia/gpu-passthrough.md`,
  `homelab/compute/nvidia/monitoring.md`, and the Docker/NVIDIA Container Toolkit section of
  `homelab/compute/nvidia/nvidia-driver-install.md`.
- Repurposed `homelab/compute/ollama/` (previously implied a native install that never existed
  in this form) to point at `homelab/ai/open-webui/` instead.
- Updated `homelab/apps/chlio/README.md` and `.env.example` with the confirmed live
  `OLLAMA_URL`/model config, resolving a previously-flagged "known gap".
- Updated `homelab/docs/network-topology.md` with `aeacus-dl`'s LAN IP and its actual
  Docker-based service stack (previously described as native services).

## 5. Current state

| Component | State |
|---|---|
| VM 101 memory | 12 GB (was 6 GB) |
| VM 101 machine type | `q35`, `hostpci0: 0000:01:00,pcie=1` |
| VM 101 CPU | `x86-64-v2-AES` (no `hidden` flag) |
| VM 101 disk | 250 GB (was 150 GB) |
| Host `kvm.ignore_msrs` | `N` (default, reverted) |
| NVIDIA persistence mode | Enabled, boot-persistent |
| Docker cgroup driver | `cgroupfs` (was `systemd`) |
| Ollama | `ollama/ollama:0.30.10`, **100% GPU**, `OLLAMA_KEEP_ALIVE=-1` |
| LiteLLM | `ghcr.io/berriai/litellm:main-stable`, 4 Claude models |
| Open WebUI | points at `http://litellm:4000/v1` |
| Chlio (minos) | `OLLAMA_URL=http://192.168.100.17:11434`, `OLLAMA_MODEL=qwen3.5:researcher` |

## 6. Outstanding / recommended

- [ ] Browser-confirm Claude models respond in Open WebUI (functional via API, UI click-through
      not performed in this session).
- [ ] Consider `qemu-guest-agent` on VM 101 for out-of-band recovery.
- [ ] Consider netplan match-by-MAC to survive future topology changes.
- [ ] Unresolved from earlier work: large registry downloads reset near ~99% (suspected
      router/NAT connection-tracking timeout). Not root-caused.
