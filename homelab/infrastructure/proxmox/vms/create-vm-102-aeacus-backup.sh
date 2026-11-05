#!/bin/bash
# Recreates VM 102 "aeacus-backup" (TrueNAS SCALE) on Proxmox host `pve`.
# Captured from the live `qm config 102` output on 2026-11-05.
#
# This VM IS the TrueNAS box — see ../../truenas/README.md for everything
# that runs inside it (pools, 25 Apps, cron scripts, shares).
#
# Disk layout (all three disks must be real, separate physical devices —
# this is NOT a 3-way stripe on one disk):
#   scsi0 - 470G on local-lvm (thin), ssd=1          -> TrueNAS boot pool
#   scsi1 - raw passthrough: Hitachi HDS721050CLA662 -> storage-tank member
#   scsi2 - raw passthrough: WDC WD10EZEX-08WN4A0    -> storage-tank member
#   scsi3 - raw passthrough: WDC WD5000AAKX-083CA1   -> backup-tank (single disk)
# storage-tank is a 2-disk STRIPE with NO redundancy (by design, accepted
# tradeoff — see docs/changelog for the Oct 2026 remediation history).
# Identify each disk by serial on the real host with:
#   lsblk -d -o NAME,SIZE,SERIAL,MODEL
# and adjust the /dev/disk/by-id/... paths below to match — serials will
# differ on different physical hardware.
#
# Usage: run on the Proxmox host as root.
#   VMID=102 ./create-vm-102-aeacus-backup.sh
set -euo pipefail

VMID="${VMID:-102}"
BRIDGE="${BRIDGE:-vmbr0}"
STORAGE="${STORAGE:-local-lvm}"
ISO_STORAGE="${ISO_STORAGE:-local}"
ISO="${ISO:-TrueNAS-SCALE-25.04.2.4.iso}"

DISK_STORAGE_TANK_1="${DISK_STORAGE_TANK_1:-/dev/disk/by-id/ata-Hitachi_HDS721050CLA662_JP1572FN2ZXW0K}"
DISK_STORAGE_TANK_2="${DISK_STORAGE_TANK_2:-/dev/disk/by-id/ata-WDC_WD10EZEX-08WN4A0_WD-WCC6Y6ETCY2C}"
DISK_BACKUP_TANK="${DISK_BACKUP_TANK:-/dev/disk/by-id/ata-WDC_WD5000AAKX-083CA1_WD-WMAYUR371592}"

if [ ! -f "/var/lib/vz/template/iso/${ISO}" ]; then
  echo "Download the TrueNAS SCALE 25.04.2.4 ISO into ${ISO_STORAGE}:iso first:"
  echo "  https://www.truenas.com/download-truenas-scale/"
  exit 1
fi

for d in "$DISK_STORAGE_TANK_1" "$DISK_STORAGE_TANK_2" "$DISK_BACKUP_TANK"; do
  if [ ! -e "$d" ]; then
    echo "Expected passthrough disk not found: $d"
    echo "Run 'lsblk -d -o NAME,SIZE,SERIAL,MODEL' and set the matching"
    echo "DISK_* env var to the correct /dev/disk/by-id/... path."
    exit 1
  fi
done

qm create "$VMID" \
  --name aeacus-backup \
  --ostype l26 \
  --sockets 1 \
  --cores 6 \
  --cpu host \
  --memory 16384 \
  --numa 0 \
  --net0 "virtio,bridge=${BRIDGE},firewall=1" \
  --scsihw virtio-scsi-single \
  --ide2 "${ISO_STORAGE}:iso/${ISO},media=cdrom" \
  --boot order=scsi0\;ide2\;net0 \
  --onboot 1 \
  --agent enabled=1

qm set "$VMID" --scsi0 "${STORAGE}:470,discard=on,iothread=1,ssd=1"
qm set "$VMID" --scsi1 "${DISK_STORAGE_TANK_1}"
qm set "$VMID" --scsi2 "${DISK_STORAGE_TANK_2},iothread=1"
qm set "$VMID" --scsi3 "${DISK_BACKUP_TANK},iothread=1"

echo "VM $VMID created with 3 passthrough disks."
echo "Boot it, install TrueNAS SCALE on scsi0 (the 470G disk), then:"
echo "  1. Create pool 'storage-tank' as a stripe of scsi1+scsi2 (sdb+sdc inside the guest)"
echo "  2. Create pool 'backup-tank' as a single-disk pool on scsi3 (sdd inside the guest)"
echo "  3. Follow infrastructure/truenas/README.md for datasets, shares, Apps, and cron jobs."
echo "Optional CPU pinning used on the live host: qm set $VMID --affinity 4-7"
echo ""
echo "KNOWN INCIDENT (resolved 2026-11-05): the scsi3 mapping for backup-tank"
echo "was previously dropped from this VM's config (disk stayed on the host,"
echo "just unmapped), silently breaking the backup-tank pool and the daily"
echo "backup cron. If backup-tank ever shows OFFLINE again with 'qm config"
echo "$VMID' missing a scsi3 line, re-run the scsi3 line above (it's a safe"
echo "hotplug on a running VM) and re-import the pool from the TrueNAS UI."
