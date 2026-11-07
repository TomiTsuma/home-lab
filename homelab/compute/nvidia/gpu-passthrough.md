# GPU Passthrough — guest/software side

This file covers the **guest-OS and container** side of GPU passthrough: what
has to be true inside the VM for a containerized workload to actually use the
GPU. For the **hypervisor** side (IOMMU, VFIO, `qm` config, machine type),
see `../../infrastructure/proxmox/pci-passthrough.md` — read that first if
the GPU doesn't show up in the guest at all.

This homelab's live example: `aeacus-dl` (Proxmox VM 101), RTX 3060, driver
580.173.02 / CUDA 13.0, running the Docker stack in
`../../ai/open-webui/` (ollama + litellm + open-webui).

## Checklist, in the order that actually matters

1. **Driver installed in the guest** — see `nvidia-driver-install.md`.
   `nvidia-smi` must work on the bare host (outside any container) first.
2. **NVIDIA Container Toolkit installed**, so `docker run --gpus all` (or the
   compose `deploy.resources.reservations.devices` block) can actually expose
   the device into a container. See the "Docker + NVIDIA Container Toolkit"
   section in `nvidia-driver-install.md`.
3. **Docker's cgroup driver is `cgroupfs`, not `systemd`.** Check with
   `docker info | grep Cgroup`. This is the single most confusing failure
   mode encountered in this homelab: with the `systemd` driver (the Ubuntu
   default under cgroups v2), any `systemctl daemon-reload` on the host
   silently revokes already-running containers' access to `/dev/nvidia*` —
   the container keeps running, `nvidia-smi` inside it starts returning
   `Failed to initialize NVML: Unknown Error`, and nothing in `docker logs`
   points at the real cause. `../../ai/open-webui/host-gpu-prep.sh` sets this
   correctly.
4. **NVIDIA persistence mode is on** (`nvidia-smi -pm 1`, and made
   boot-persistent — see `host-gpu-prep.sh`, same script as above). Without
   it, cold CUDA context creation can be slow enough to blow past an
   application's own GPU-discovery timeout (Ollama's is ~30s), making the
   app silently fall back to CPU with no error. This looks identical to a
   real passthrough failure but isn't one.
5. **The VM has enough system RAM**, not just VRAM. A model/workload that
   needs to stage data through host RAM before it reaches the GPU (or falls
   back to CPU because of #3/#4 above) needs real headroom — `aeacus-dl` was
   originally undersized (6 GB RAM for a 6.6 GB model file) and this caused
   swap-thrashing that looked like a GPU hang.

## Confirming the GPU is genuinely healthy, independent of any app

If something built on top (Ollama, llama.cpp, a training script) "hangs" with
the GPU at 0% utilization, don't assume VFIO/IOMMU before checking whether
the GPU itself is fine. The fastest way to isolate this: call the CUDA
driver API directly, bypassing the app entirely.

```python
import ctypes, time
lib = ctypes.CDLL("/usr/lib/x86_64-linux-gnu/libcuda.so.1")
lib.cuInit(0)
dev = ctypes.c_int(0); lib.cuDeviceGet(ctypes.byref(dev), 0)
ctx = ctypes.c_void_p(); lib.cuCtxCreate_v2(ctypes.byref(ctx), 0, dev)
# then cuMemAlloc_v2 / cuMemcpyHtoD_v2 / cuMemcpyDtoD_v2 and time them
```

If `cuInit`/`cuCtxCreate`/memcpy all complete quickly with real bandwidth
(expect several GB/s host↔device, tens-to-hundreds of GB/s device↔device),
the passthrough, driver, and CUDA stack are all fine — the problem is
upstream of CUDA: the application's own GPU-discovery logic, Docker's device
exposure to the container (point 3 above), or host memory pressure (point
5). This exact probe found all three of this homelab's real root causes in
under an hour, after far longer spent on VFIO/IOMMU config that turned out to
be correct already.

## See also

- `../../infrastructure/proxmox/pci-passthrough.md` — hypervisor-side config,
  machine type, IOMMU, GPU reset without a host reboot.
- `../../ai/open-webui/README.md` — the concrete deployment this all exists
  to support, with the full gotcha list and a worked example.
- `../../docs/changelog/071126-091000_ollama_gpu_and_claude_integration_changelog.md`
  — the full incident writeup.
