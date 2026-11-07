# GPU Monitoring

Notes and commands for checking whether a GPU-passthrough VM/container is
actually using the GPU, vs. silently running on CPU or sitting idle.

## Basic checks

```bash
# On the Proxmox host: is the device bound to vfio-pci (not in use by the host)?
lspci -k -s <addr>   # "Kernel driver in use: vfio-pci"

# Inside the guest: driver + utilization + power + VRAM
nvidia-smi
nvidia-smi --query-gpu=utilization.gpu,power.draw,memory.used,memory.total --format=csv

# Live sampling (nvidia-smi's -l flag doesn't accept custom fields well on
# older driver builds — a shell loop is more reliable):
for i in $(seq 1 20); do
  nvidia-smi --query-gpu=utilization.gpu,power.draw,clocks.sm --format=csv,noheader
  sleep 0.5
done
```

**A GPU sitting at 0% utilization with an elevated clock speed and power draw
above idle (e.g. ~37W instead of ~9W idle on an RTX 3060) means it IS
actively executing — just a workload with very small/sparse kernel launches
(e.g. still loading model weights from disk into RAM before anything moves
to VRAM). Don't conclude "hung" from utilization alone; check the
application's own logs for load-progress messages and check whether VRAM
usage is climbing.**

## Is a container actually seeing the GPU?

```bash
docker exec <container> nvidia-smi
```

If this returns `Failed to initialize NVML: Unknown Error` on a container
that worked a minute ago, the GPU access was almost certainly revoked by a
`systemctl daemon-reload` on the host (Docker's `systemd` cgroup driver
gotcha — see `nvidia-driver-install.md` and `gpu-passthrough.md`), not a
hardware fault. Recreate the container:

```bash
docker compose up -d --force-recreate <service>
```

## Ollama-specific: is the model on GPU or CPU?

```bash
docker exec ollama ollama ps
```

```
NAME                  ID              SIZE      PROCESSOR    CONTEXT    UNTIL
qwen3.5:researcher    688c33f1a293    5.8 GB    100% GPU     8192       Forever
```

`PROCESSOR` showing `100% CPU` (or a CPU/GPU split) despite GPU passthrough
being configured means the GPU wasn't visible to Ollama **at model-load
time** — check `docker logs ollama` for `llama-server GPU discovery watchdog
timed out` and see the cgroup-driver/persistence-mode gotchas linked above.
Fix the underlying cause and reload the model (`docker restart ollama`, then
re-send a request) — a model already loaded as CPU-only won't move to GPU on
its own.

## Process-level: is something stuck, not just slow?

```bash
# D = uninterruptible sleep (usually I/O/memory wait), R = actually running
ps -eo pid,stat,etimes,pcpu,cmd | grep llama-server

# What kernel call is it blocked in, if any:
sudo cat /proc/<pid>/stack
sudo cat /proc/<pid>/syscall
```

A process stuck in `D` state for a long time, especially alongside
`systemd-journald: Under memory pressure, flushing caches` in `dmesg`, points
at host/VM memory pressure, not a GPU fault — see the "GPU gotchas" section
in `../../ai/open-webui/README.md`.

## Isolating a real hardware/passthrough problem from everything else

If the above all look fine (driver loaded, container sees the GPU, model
shows `100% GPU`, process is in `R` state) but throughput still seems wrong,
measure actual CUDA bandwidth directly rather than trusting an app's
behavior — see `gpu-passthrough.md` → "Confirming the GPU is genuinely
healthy, independent of any app" for a minimal `ctypes`-based probe.
