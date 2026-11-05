# Rebuilding the Proxmox host (`pve`) from nothing

This is the top-level recovery doc for the physical hypervisor. If the whole
homelab is gone, start here, then follow `../truenas/README.md` for VM 102's
internals once it's created.

Captured from the live host on 2026-11-05: `pve-manager/9.0.3`, kernel
`6.14.8-2-pve`, hardware = Dell Precision Tower 3620 (per memory), 1 onboard
Ethernet NIC, 4 SATA ports.

## 1. Install Proxmox VE

Install Proxmox VE 9.x from the official ISO (https://www.proxmox.com/en/downloads)
onto the primary disk (`sda` in the live host — a single disk holding the OS
and `local-lvm`, no RAID). During install:
- Hostname: `pve`
- Network: static, matches `/etc/network/interfaces` below
- Timezone: `Africa/Nairobi` (TZ=EAT)

## 2. Network config

The host has exactly one bridge, `vmbr0`, directly on the onboard NIC
(`enp0s31f6` — **confirm the interface name on your hardware with `ip link`,
it may differ**). This is the Proxmox installer's default pattern; verify
`/etc/network/interfaces` matches:

```
auto lo
iface lo inet loopback

iface enp0s31f6 inet manual

auto vmbr0
iface vmbr0 inet static
	address 192.168.100.145/24
	gateway 192.168.100.1
	bridge-ports enp0s31f6
	bridge-stp off
	bridge-fd 0

source /etc/network/interfaces.d/*
```

No VLANs, no second bridge, no bonding.

## 3. Storage config

Default Proxmox layout, no changes needed — `/etc/pve/storage.cfg` should be:

```
dir: local
	path /var/lib/vz
	content iso,vztmpl,backup

lvmthin: local-lvm
	thinpool data
	vgname pve
	content rootdir,images
```

`local-lvm` is **overcommitted** (small physical disk, ~350GB thin pool backing
multiple VMs with larger nominal disk sizes — see per-VM scripts). Watch free
space; this has caused issues before (see docs/changelog).

## 4. Identify physical disks

Before creating VM 102 (TrueNAS), find your passthrough disk IDs:

```sh
lsblk -d -o NAME,SIZE,SERIAL,MODEL
ls -la /dev/disk/by-id/
```

On the reference host these were:
- `storage-tank` members: Hitachi HDS721050CLA662 (`JP1572FN2ZXW0K`), WDC
  WD10EZEX-08WN4A0 (`WD-WCC6Y6ETCY2C`)
- `backup-tank` member: WDC WD5000AAKX-083CA1 (`WD-WMAYUR371592`)

These are raw physical disks passed through to the VM, not virtual disk
images — the serials will be different on new hardware. Update the
`DISK_*` variables in `vms/create-vm-102-aeacus-backup.sh` accordingly.

## 5. GPU passthrough prerequisites (for VM 101)

Follow `pci-passthrough.md` in full (IOMMU in GRUB, vfio modules, driver
blacklist, reboot) **before** running `vms/create-vm-101-aeacus-dl.sh`. The
reference host passes through an NVIDIA RTX 3060 at PCI address `0000:01:00` —
find yours with `lspci -nn | grep -i nvidia`.

## 6. Download ISOs

Into `local:iso` (`/var/lib/vz/template/iso/` on the host):
- `ubuntu-24.04.3-live-server-amd64.iso` (VMs 100, 101)
- `TrueNAS-SCALE-25.04.2.4.iso` (VM 102) — https://www.truenas.com/download-truenas-scale/

## 7. Create the VMs, in this order

```sh
cd vms/
VMID=100 ./create-vm-100-aeacus-data-handler.sh
VMID=102 ./create-vm-102-aeacus-backup.sh
VMID=101 ./create-vm-101-aeacus-dl.sh
```

Order matters for memory headroom: the host has ~39 GB total RAM shared
across VM 100 (16449 MB), VM 101 (6144 MB), and VM 102 (16384 MB) — that's
already ~39 GB fully committed with almost no slack. VM 101 is intentionally
small and boots last (`startup: order=3,up=60`, i.e. 60s after the others)
because it was previously OOM-killed at boot when given more memory or
started concurrently with the other two. If you add RAM to the host, VM 101
is the one to grow first (it's the GPU/ML box and is the most memory-starved
relative to its workload).

## 8. Per-VM follow-up

- **VM 100** (`aeacus-data-handler`): install Docker, then recreate Harbor,
  the Ereuna/Aether stack, Lyre, Vault, MinIO, NetBird, the Nextcloud snap,
  Gotify, and the Cloudflare tunnel. (Detailed per-service docs are a
  follow-up pass on this repo — not yet written as of 2026-11-05.)
- **VM 101** (`aeacus-dl`): install the NVIDIA driver + CUDA toolkit, then
  MLflow, Ollama, Open WebUI, code-server.
- **VM 102** (`aeacus-backup` / TrueNAS): follow `../truenas/README.md`.

## Known incident history

See `../../docs/changelog/` for dated incident write-ups, including:
- 2026-10-17: Jellyfin AVPlayer `-11850` playback fix + initial Proxmox/TrueNAS
  remediation (net0 model, VM 101 OOM, backup-tank creation).
- 2026-11-05: `backup-tank`'s `scsi3` passthrough mapping was found dropped
  from VM 102's live config (physical disk was fine, just unmapped) — fixed
  by re-running the `qm set 102 --scsi3 ...` line. If this happens again,
  check `qm config 102` for a missing `scsi3` line before assuming disk failure.
