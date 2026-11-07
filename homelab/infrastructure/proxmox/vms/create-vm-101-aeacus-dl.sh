#!/bin/bash
# Recreates VM 101 "aeacus-dl" (GPU passthrough ML box) on Proxmox host `pve`.
# Captured from the live `qm config 101` output on 2026-11-07 (updated from the
# original 2026-11-05 capture — see docs/changelog/
# 071126-091000_ollama_gpu_and_claude_integration_changelog.md for what changed and why).
#
# REQUIRES PCI passthrough to already be set up on the host (IOMMU enabled,
# vfio modules loaded, GPU driver blacklisted on the host) — see
# ../pci-passthrough.md before running this.
#
# This VM hosts (install separately after the OS is up):
#   - NVIDIA GPU passthrough (RTX 3060 at this host's hostpci0=0000:01:00 —
#     confirm with `lspci -nn | grep -i nvidia` on YOUR host, the PCI address
#     will differ if the card is in a different slot)
#   - A Docker Compose stack: ollama + litellm + open-webui — see
#     ../../../ai/open-webui/ (run ../../ai/open-webui/host-gpu-prep.sh on
#     this VM once, before `docker compose up -d`, or GPU passthrough will
#     silently fall back to CPU — see that directory's README.md)
#   - Native systemd services: MLflow (port 5000), code-server
#   - A Data-Handler docker-compose stack (MinIO + a Kaggle-ingestion FastAPI
#     service) — present but not always running
#
# Machine type is q35 with pcie=1 on the passthrough device (real PCIe root
# port), not the default i440fx/legacy-PCI — required for this GPU, though on
# its own it was NOT sufficient to fix a real inference hang encountered here
# (see the changelog above: that also needed the Docker cgroup driver fix and
# the memory bump below). IMPORTANT: changing machine type changes the guest's
# PCI topology, which can rename its NIC via Linux's predictable-naming scheme
# (ens18 -> enp6s18 happened here) and silently break networking until
# /etc/netplan/*.yaml is updated to match and `netplan apply` is re-run.
#
# Memory is 12288 MB (raised from an original 6144 MB): that was *less than*
# the size of a single model file (qwen3.5:researcher, 6.6 GB), which caused
# severe swap thrashing whenever GPU passthrough wasn't active. This host's
# Proxmox box has ~39 GB RAM shared across all 3 VMs — confirm headroom with
# `free -h` on the host before raising this further. `startup: order=3,up=60`
# makes it boot last, 60s after VMs 100/102, to avoid a boot-time OOM pile-up —
# see MEMORY.md homelab notes / docs/changelog for the incident history.
#
# Disk is 250 GB (grown from an original 150 GB) — grown live with `qm resize`
# plus an online `growpart`/`resize2fs` of the root LV, not recreated from
# scratch; see docs/changelog for that procedure if doing it again.
#
# This VM has no QEMU guest agent and no serial console configured. If the
# network ever drops (e.g. after a machine-type change), the ONLY way in is
# the Proxmox noVNC console — there is no SSH/exec fallback. Consider adding
# `qemu-guest-agent` on a future rebuild to avoid this.
#
# Usage: run on the Proxmox host as root.
#   VMID=101 ./create-vm-101-aeacus-dl.sh
set -euo pipefail

VMID="${VMID:-101}"
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-local-lvm}"
ISO_STORAGE="${ISO_STORAGE:-local}"
ISO="${ISO:-ubuntu-24.04.3-live-server-amd64.iso}"
GPU_PCI_ADDR="${GPU_PCI_ADDR:-0000:01:00}"

if [ ! -f "/var/lib/vz/template/iso/${ISO}" ]; then
  echo "Download the Ubuntu Server 24.04.3 ISO into ${ISO_STORAGE}:iso first."
  exit 1
fi

qm create "$VMID" \
  --name aeacus-dl \
  --ostype l26 \
  --machine q35 \
  --sockets 2 \
  --cores 4 \
  --cpu x86-64-v2-AES \
  --memory 12288 \
  --numa 0 \
  --net0 "virtio,bridge=${BRIDGE},firewall=1" \
  --scsihw virtio-scsi-single \
  --ide2 "${ISO_STORAGE}:iso/${ISO},media=cdrom" \
  --boot order=scsi0\;net0\;ide2 \
  --onboot 1 \
  --startup order=3,up=60

qm set "$VMID" --scsi0 "${STORAGE}:250,discard=on,iothread=1"
qm set "$VMID" --hostpci0 "${GPU_PCI_ADDR},pcie=1"

echo "VM $VMID created with GPU passthrough at ${GPU_PCI_ADDR} (q35, pcie=1)."
echo "Boot it, install Ubuntu 24.04, then install the NVIDIA driver + CUDA"
echo "toolkit matching the one in use (driver 580.173.02 / CUDA 13.0 at audit"
echo "time — check for newer compatible releases before reinstalling)."
echo "Then run ../../ai/open-webui/host-gpu-prep.sh before bringing up the"
echo "docker compose stack in that directory — do not skip it."
echo "Optional CPU pinning used on the live host: qm set $VMID --affinity 0-3"
