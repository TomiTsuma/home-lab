#!/bin/bash
# Recreates VM 100 "aeacus-data-handler" on Proxmox host `pve`.
# Captured from the live `qm config 100` output on 2026-11-05.
#
# This VM hosts (install separately after the OS is up, see
# infrastructure/truenas/README.md's sibling docs for each service):
#   - Harbor (self-hosted container registry, /opt/harbor)
#   - "Ereuna/Aether" microservice platform (Postgres, Kafka, Zookeeper,
#     ClickHouse, Redis, OpenSearch, Neo4j, MinIO) + "Lyre" app + GlitchTip
#   - Native HashiCorp Vault, native MinIO, NetBird, MicroK8s (k8s snap)
#   - The actual Nextcloud server (nextcloud snap) backing the user's
#     Nextcloud2 sync folder
#   - Gotify (homelab/messaging/gotify)
#   - A Cloudflare Tunnel client
#
# Usage: run on the Proxmox host as root.
#   VMID=100 ./create-vm-100-aeacus-data-handler.sh
set -euo pipefail

VMID="${VMID:-100}"
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-local-lvm}"
ISO_STORAGE="${ISO_STORAGE:-local}"
ISO="${ISO:-ubuntu-24.04.3-live-server-amd64.iso}"

if [ ! -f "/var/lib/vz/template/iso/${ISO}" ]; then
  echo "Download the Ubuntu Server 24.04.3 ISO into ${ISO_STORAGE}:iso first:"
  echo "  cd /var/lib/vz/template/iso && wget https://releases.ubuntu.com/24.04/${ISO}"
  exit 1
fi

qm create "$VMID" \
  --name aeacus-data-handler \
  --ostype l26 \
  --sockets 1 \
  --cores 2 \
  --cpu x86-64-v2-AES \
  --memory 16449 \
  --numa 0 \
  --net0 "virtio,bridge=${BRIDGE},firewall=1" \
  --scsihw virtio-scsi-single \
  --ide2 "${ISO_STORAGE}:iso/${ISO},media=cdrom" \
  --boot order=scsi0\;ide2\;net0 \
  --onboot 1 \
  --agent enabled=0

qm set "$VMID" --scsi0 "${STORAGE}:200,discard=on,iothread=1"

echo "VM $VMID created. Boot it, install Ubuntu 24.04 on scsi0, then set a"
echo "static IP matching the LAN (see infrastructure/proxmox/REBUILD.md)."
echo "Note: the live host pins this VM to CPU affinity 4-5 (qm set $VMID --affinity 4-5,"
echo "requires a Proxmox version with CPU affinity support) — optional, not required to boot."
