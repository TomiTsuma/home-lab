#!/bin/bash
# Recreates VM 101 "aeacus-dl" (GPU passthrough ML box) on Proxmox host `pve`.
# Captured from the live `qm config 101` output on 2026-11-05.
#
# REQUIRES PCI passthrough to already be set up on the host (IOMMU enabled,
# vfio modules loaded, GPU driver blacklisted on the host) — see
# ../pci-passthrough.md before running this.
#
# This VM hosts (install separately after the OS is up):
#   - NVIDIA GPU passthrough (RTX 3060 at this host's hostpci0=0000:01:00 —
#     confirm with `lspci -nn | grep -i nvidia` on YOUR host, the PCI address
#     will differ if the card is in a different slot)
#   - Native systemd services: MLflow (port 5000), Ollama, Open WebUI
#     (port 8080), code-server
#   - A Data-Handler docker-compose stack (MinIO + a Kaggle-ingestion FastAPI
#     service) — present but not always running
#
# Memory is deliberately small (6144 MB) relative to its 4 cores: this host's
# Proxmox box has ~39 GB RAM total shared across all 3 VMs, and this VM was
# previously OOM-killed at every boot when given more. `startup: order=3,up=60`
# makes it boot last, 60s after VMs 100/102, to avoid a boot-time OOM pile-up —
# see MEMORY.md homelab notes / docs/changelog for the incident history.
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
  --sockets 2 \
  --cores 4 \
  --cpu x86-64-v2-AES \
  --memory 6144 \
  --numa 0 \
  --net0 "virtio,bridge=${BRIDGE},firewall=1" \
  --scsihw virtio-scsi-single \
  --ide2 "${ISO_STORAGE}:iso/${ISO},media=cdrom" \
  --boot order=scsi0\;net0\;ide2 \
  --onboot 1 \
  --startup order=3,up=60

qm set "$VMID" --scsi0 "${STORAGE}:150,discard=on,iothread=1"
qm set "$VMID" --hostpci0 "${GPU_PCI_ADDR}"

echo "VM $VMID created with GPU passthrough at ${GPU_PCI_ADDR}."
echo "Boot it, install Ubuntu 24.04, then install the NVIDIA driver + CUDA"
echo "toolkit matching the one in use (driver 580.173.02 / CUDA 13.0 at audit"
echo "time — check for newer compatible releases before reinstalling)."
echo "Optional CPU pinning used on the live host: qm set $VMID --affinity 0-3"
