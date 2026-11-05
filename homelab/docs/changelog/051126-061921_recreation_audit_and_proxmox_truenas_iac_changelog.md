# 2026-11-05 — Full homelab audit + Proxmox/TrueNAS recreation IaC

Goal: make sure this repo can fully recreate the homelab from nothing. Ran a
two-part audit (repo contents vs. live infra across pve, TrueNAS, minos,
aeacus-data-handler, aeacus-dl, Azure wg-hub) and closed the highest-priority
"recreation blocker" gaps. Full service-level sweep (stub compose files,
minos's stack, the Ereuna/Lyre platform) is a follow-up pass — see "Not done
yet" below.

## Live incident found and fixed

- **`backup-tank` pool was fully offline.** VM 102's `scsi3` passthrough
  mapping for the backup-tank physical disk (WD5000AAKX, serial
  `WD-WMAYUR371592`) had been dropped from the live Proxmox VM config —
  the disk itself was fine, still present on the `pve` host as `/dev/sdc`,
  just unmapped from the VM. This meant `zpool`-level the pool had **zero**
  visible member disks (not merely degraded), and the daily
  `backup_to_backup_tank.py` cron had presumably been failing/no-op'ing via
  its own pool-health pre-flight check.
  - Fix: re-ran `qm set 102 --scsi3 /dev/disk/by-id/ata-WDC_WD5000AAKX-083CA1_WD-WMAYUR371592,serial=WD-WMAYUR371592,iothread=1`
    on `pve` (safe hotplug, no VM restart). Confirmed TrueNAS now sees the
    disk again (`midclt call disk.query` shows `sdd`).
  - **Still needed (left for the owner)**: re-import the `backup-tank` pool
    from the TrueNAS UI (Storage > Import Pool), since TrueNAS still shows
    it OFFLINE at the pool level even with the disk visible again.

## Reverted

- An uncommitted working-tree change to `homelab/media/stream/proxy/hls_proxy.py`
  that flipped `PROXY_ALLOWED_HOSTS`'s default from fail-closed (deny by
  default) to fail-open (`"*"`), plus a trivial `.env.example` edit —
  discarded as an accidental local edit (`git checkout --`).

## Added — Proxmox provisioning (`homelab/infrastructure/proxmox/`)

- `vms/create-vm-100-aeacus-data-handler.sh`, `create-vm-101-aeacus-dl.sh`,
  `create-vm-102-aeacus-backup.sh` — `qm create`/`qm set` scripts matching
  the exact live VM configs (cores, memory, disk sizes, GPU/disk passthrough,
  boot order, startup delay) captured via `qm config <id>` on `pve`.
- `REBUILD.md` — host-level rebuild procedure: network bridge, storage.cfg,
  disk identification, GPU passthrough prerequisites, ISO downloads, VM
  creation order (memory headroom matters — the 3 VMs fully commit the
  host's ~39GB RAM).
- `pci-passthrough.md` — appended this homelab's actual GPU passthrough
  values (RTX 3060 at `0000:01:00` on VM 101) to the existing generic guide.
- `setup.sh` — was an empty stub; now points to `REBUILD.md` and explains
  why host-level setup has no standalone script (it's mostly ISO-install
  prompts + the per-VM scripts).
- `infrastructure/terraform/main.tf` — added a status note: shell scripts
  are the current source of truth, Terraform migration is future work (would
  need a Proxmox API token).

## Added — TrueNAS (`homelab/infrastructure/truenas/`, new directory)

- `README.md` — pool/dataset layout (`storage-tank` 2-disk stripe no
  redundancy, `backup-tank` single disk), SMB shares, a full table of all 25
  TrueNAS Apps (name/state/image/ports/mounts) discovered via `midclt call
  app.query`, the `wgbak` WireGuard hub confirmation, cron job schedule, and
  the backup-tank incident writeup above.
- `scripts/` — migrated the live (current, root-fetched) copies of
  `backup_to_backup_tank.py`, `remux_to_mp4.py`, `nextcloud_to_paperless.py`,
  and `test_nextcloud_to_paperless.py` from the previously-untracked
  `~/Documents/homelab-ops/scripts/` folder, plus a `README.md` explaining
  each script and a restore cheat-sheet.
  - Note: `backup_to_backup_tank.py`'s live version had diverged
    significantly from the `homelab-ops` copy (added Uptime Kuma push
    monitoring, a pool-health pre-flight check, and a Part D that backs up
    every App's rendered compose config) — the newer live version was used.
  - `nextcloud_to_paperless.py` was previously undocumented anywhere.
- Copied the historical incident doc
  `docs/changelog/171026-103353_jellyfin_11850_and_storage_fixes_changelog.md`
  from `homelab-ops` into this repo's `docs/changelog/` (this repo had no
  `docs/changelog/` folder before this session).

## Added — WireGuard real topology (`homelab/networking/wireguard/`)

- `REAL-TOPOLOGY.md` — documents the two actual meshes (`wgbak` 10.77.0.0/24
  hub=TrueNAS, `wgaz` 10.78.0.0/24 hub=Azure `wg-hub`), confirmed `minos` as
  a live peer on **both** (not previously documented for `wgaz` at all).
- Added a warning header to the existing `install.sh`, which is a generic
  linuxserver/wireguard reference script that does **not** match either real
  mesh — kept for reference, not deployment.

## Updated — `docs/network-topology.md`

Was a one-line stub; now a real cross-host overview (LAN, 4 separate overlay
networks, reverse-proxy/ingress, per-host service summary, open questions for
the owner) tying together the detailed docs above.

## Not done yet (next pass — "recreation blockers" were prioritized first)

- Per-app exact `docker-compose.yaml`/env-var export for the 25 TrueNAS Apps
  (needs root/sudo on TrueNAS — blocked, see `truenas/README.md` "Known gaps").
- `wgbak`/`wgaz` peer lists and keys (needs root/sudo on TrueNAS and the
  Azure hub — the latter was also unreachable by SSH this session).
- Full documentation/compose for the "Ereuna/Aether/Lyre" platform on
  `aeacus-data-handler` (Harbor, Kafka, ClickHouse, Neo4j, OpenSearch,
  GlitchTip, Vault, MinIO, NetBird) — user has confirmed this should be
  included in a follow-up pass.
- `minos`'s own service stack (Jellyfin+Plex, its own Sonarr/Radarr/Prowlarr/
  Navidrome/Kavita/Chlio/WireGuard-server, Pi-hole) — not yet captured, and
  its relationship to `minos` not being a Proxmox guest needs the owner's
  input.
- ~25 stub `docker-compose.yml`/doc files elsewhere in the repo (devtools,
  data-services, monitoring, several media apps) still need a verify-or-flesh-out
  pass; several document services that aren't actually running anywhere
  reachable (may be legacy or TrueNAS Apps duplicating the folder name).
- `media/radarr` has no compose file at all despite Radarr running (as a
  TrueNAS App — see `infrastructure/truenas/README.md`).
- Two nested vendored `.git` repos under `homelab/media/` (`DVarr_Source/`,
  `FruidDeepLinks_Source/`) are not submodule-linked — a bare `git clone` of
  this repo will not pull their content correctly. Needs converting to
  submodules or stripping `.git` and committing content directly.
