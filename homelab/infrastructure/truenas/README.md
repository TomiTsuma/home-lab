# TrueNAS SCALE (VM 102 "aeacus-backup") — rebuild guide

This is the single biggest piece of the homelab: 25 TrueNAS "Apps" (= docker
containers managed by TrueNAS's own catalog system), 2 ZFS pools, SMB shares,
3 custom cron scripts, and the WireGuard `wgbak` mesh hub. Captured live on
2026-11-05 via `ssh truenas_admin@<tailscale-ip>` + `midclt call ...` (no
sudo/root access was available during this audit — see "Known gaps" below).

Runs as VM 102 on the `pve` Proxmox host — create the VM first with
`../proxmox/vms/create-vm-102-aeacus-backup.sh`, then come back here.

## 1. Install

TrueNAS SCALE 25.04.2.4, installed onto the 470G `scsi0` disk (boot pool).
Hostname `truenas`, single network interface on `192.168.100.10/24`
(gateway/DNS come from DHCP — TrueNAS's own `network.configuration` has no
static gateway/nameserver set). Also reachable via Tailscale at
`100.100.133.10` (Tailscale itself runs as the `tailscale` App, not as an
OS-level service — install it as an App after setup, see the apps table).

## 2. Pools and datasets

| Pool | Topology | Status | Notes |
|---|---|---|---|
| `storage-tank` | 2-disk **stripe, no redundancy** (Hitachi 500GB + WD 1TB passthrough disks, `scsi1`+`scsi2`) | ONLINE | Root dataset holds everything below. Deliberate tradeoff, not an accident — see docs/changelog. |
| `backup-tank` | single disk (WD 500GB passthrough, `scsi3`) | ONLINE (was OFFLINE until 2026-11-05, see Known Incidents) | Destination for the nightly backup script. |

Key datasets under `storage-tank`:
- `storage-tank/data-store` — bulk data: Jellyfin config/media, `Downloads`,
  `audiobooks`, `scripts/` (the 3 cron scripts, see below), `remux/`,
  `home-lab/`, `plex/`. NFSv4 ACL mode ("restricted"), so `chmod` from inside
  containers can fail — known issue, see audiobookshelf note in the Oct 2026
  changelog.
- `storage-tank/wireguard` — holds the `wgbak` WireGuard config/keys,
  root-owned (0700), not readable without sudo.
- `storage-tank/ix-apps` — TrueNAS's internal app state (docker volumes,
  app configs). Don't touch directly; it's what the Apps system manages.

Recreate pools during a fresh install via the TrueNAS UI (Storage > Create
Pool): `storage-tank` as a stripe of the two bigger disks, `backup-tank` as a
single-disk pool on the third. Then recreate the dataset layout above before
restoring data from a `backup_to_backup_tank.py` backup (see scripts/README.md).

## 3. SMB shares

| Share name | Path | Notes |
|---|---|---|
| `storage` | `/mnt/storage-tank` | Pool root — overlaps with `data-store` below; TrueNAS has previously raised a CRITICAL alert about this overlap. Not fixed as of the last review (see Oct 2026 changelog "Not changed" section) — needs an owner decision before deleting either share. |
| `data-store` | `/mnt/storage-tank/data-store` | |

No NFS shares configured.

## 4. Apps (TrueNAS catalog apps = docker containers)

Recreating these: reinstall each by name from the TrueNAS Apps catalog UI
("Discover Apps" for community-train apps), then point its config/data paths
back at the same dataset locations below so existing data is picked up.
Ports listed are TrueNAS's own auto-assigned host ports (30000+ range) —
don't need to be set manually, TrueNAS assigns them, but note them if
anything outside TrueNAS (reverse proxy, firewall rule) hardcodes one.

| App | State | Image | Host port(s) | Key host mount(s) |
|---|---|---|---|---|
| jellyfin | RUNNING | jellyfin/jellyfin:10.11.11 | 30013 (http), 30014 (https) | `data-store` → `/config`, `data-store/media` → `/media`, `data-store/Downloads` → `/media/downloads` (read-only) |
| paperless-ngx | RUNNING | paperlessngx/paperless-ngx:3.3.2 + postgres:18.6 + valkey:9.1.2 | 30070 | `app_mounts/paperless-ngx/{data,media,trash,consume,postgres_data}` |
| nextcloud | RUNNING | ix-nextcloud:32.0.2 + postgres:18.1 + valkey:9.0.0 | 30027 | html, data, postgres_data |
| immich | RUNNING | immich-server:v2.3.1 + immich-ml + postgres(pgvecto) + valkey | 30041 | data, postgres_data, ml-cache |
| gitea | RUNNING | gitea/gitea:1.25.5-rootless + postgres:18.3 | 30008, 30009 | config, data, postgres_data |
| portainer | RUNNING | portainer/portainer-ce:2.43.0 | 31015 | data + docker.sock |
| uptime-kuma | RUNNING | louislam/uptime-kuma:2.0.2 | 31050 | data + docker.sock |
| nginx-proxy-manager | RUNNING | jc21/nginx-proxy-manager:2.13.5 | 30020 (admin 81), 30021 (80), 30022 (443) | data + `/etc/letsencrypt` |
| syncthing | RUNNING | syncthing/syncthing:2.0.12 | 20910, 20978/tcp, 20970/udp | config |
| tailscale | RUNNING | ghcr.io/tailscale/tailscale:v1.92.5 | — | state dir |
| prowlarr | RUNNING | ghcr.io/home-operations/prowlarr:2.6.6.5717 | 30050 | config, `data-store` → `/home` |
| sonarr | RUNNING | ghcr.io/home-operations/sonarr:4.0.18.2978 | 30113 | `data-store/media/tv`, config, Downloads |
| radarr | RUNNING | ghcr.io/home-operations/radarr:6.4.0.10523 | 30025 | `data-store/media/movies`, config, Downloads |
| lidarr | RUNNING | ghcr.io/home-operations/lidarr:3.1.1.4876 | 30071 | config, Downloads, `media/music` |
| qbittorrent | RUNNING | ghcr.io/home-operations/qbittorrent:5.1.4 | 30024, 51413 (tcp/udp) | Downloads (config and data both point at Downloads) |
| navidrome | RUNNING | deluan/navidrome:0.60.3 | 30043 | `data-store/media/music` |
| audiobookshelf | RUNNING | advplyr/audiobookshelf:2.36.1 | 30067 | `data-store/audiobooks` (+config/metadata subdirs) |
| lazylibrarian | RUNNING | linuxserver/lazylibrarian | 31089 | `data-store/audiobooks` → `/books` |
| seerr2 | RUNNING | ghcr.io/seerr-team/seerr:v3.4.1 | 30303 | config |
| plex1 | STOPPED | plex:1.43.4 | — | — |
| seerr | STOPPED | seerr:v3.4.1 (older, superseded by seerr2) | — | — |
| lyrion-music-server | STOPPED | 9.0.4 | — | — |
| wg-easy | STOPPED | 15.1.0 | — | has app_configs from Dec 2025 |
| adguard-home | STOPPED | v0.107.70 | — | — |
| readarr | STOPPED | 0.4.18.2805 | — | — |
| tdarr | STOPPED | — | — | app_config dir exists but was being created/torn down during this audit — check its state before assuming it's wanted |

**Not recreatable from this table alone**: exact environment variables (DB
passwords, API keys, PUID/PGID) are inside TrueNAS's rendered
`docker-compose.yaml` per app at `/mnt/.ix-apps/app_configs/<app>/versions/<ver>/templates/rendered/`,
which is root-only (0600) — this audit only had a non-root admin account.
**To export exact configs for every app**, run as root on TrueNAS:
```sh
for d in /mnt/.ix-apps/app_configs/*/; do
  app=$(basename "$d")
  ver=$(ls "$d/versions")
  echo "=== $app ==="
  cat "$d/versions/$ver/templates/rendered/docker-compose.yaml"
done
```
and commit the output into this directory (e.g. `apps/<name>/docker-compose.yaml`)
for a bit-for-bit recreation path. Not done yet as of 2026-11-05 — needs sudo access.

## 5. WireGuard (`wgbak`)

TrueNAS genuinely is the `wgbak` mesh hub (10.77.0.0/24) — confirmed by a
live `wgbak` kernel interface (`ip -br link` shows it UP), configured
**outside** the Apps system via a plain `/etc/wireguard/wgbak.conf` +
`wg-quick`, with persistent config/keys likely under the `storage-tank/wireguard`
dataset (root-owned, 0700 — not readable without sudo). The `wg-easy` App
exists but is STOPPED, so it is not the active mechanism.

`minos` (another homelab host) is a confirmed live peer on `wgbak` — see
`../../networking/wireguard/REAL-TOPOLOGY.md` for the full mesh picture
including `wgaz` (the separate Azure-hub mesh).

**Known gap**: peer list and keys need root/sudo to export
(`wg show wgbak`, `cat /etc/wireguard/wgbak.conf`) — not done yet. To fully
recreate `wgbak` on a fresh TrueNAS install, run those as root and save the
config (keys excepted — generate fresh keys for a true rebuild, or restore
from a secrets backup if preserving existing peers matters).

## 6. Cron jobs

All four run from `/mnt/storage-tank/data-store/scripts/` — see
`scripts/README.md` in this directory for what each one does and the full
restore procedure. Scripts are mirrored into `scripts/` here.

| Script | Schedule (TrueNAS local time) | Purpose |
|---|---|---|
| `backup_to_backup_tank.py` | 14:00 daily | storage-tank → backup-tank: app data (ZFS replication), key data-store folders (rsync), TrueNAS config DB, and every App's rendered compose/config |
| `remux_to_mp4.py --apply --max-minutes 360` | 14:30 daily | MKV → MP4 conversion fix for the Jellyfin iOS AVPlayer `-11850` bug |
| `nextcloud_to_paperless.py` | every 15 min | Mirrors Nextcloud "research"/"Personal" folders into the Paperless-ngx consume inbox |
| `gitea_repos_to_s3.py` | 14:30 daily | Mirrors Gitea's bare repos to the `gitea-backup` bucket on MinIO (LAN-direct to `aeacus-data-handler`, not through the internet) — added 2026-11-05, see `scripts/README.md` |

There's also a `tdarr_worker_watchdog.sh` cron job (id 4, found 2026-11-05)
that wasn't investigated or migrated into this repo yet — Tdarr itself is a
stopped TrueNAS App (see section 4), so this watchdog's current relevance is
unclear.

## 7. Known incidents

- **2026-11-05 — backup-tank disk was wiped, not just disconnected; pool
  recreated from scratch.** VM 102's `scsi3` PCI/SCSI mapping for the
  backup-tank disk had been dropped at some point (the physical disk was
  untouched on the Proxmox host, just unmapped from the VM) — this alone
  was fixed by re-running
  `qm set 102 --scsi3 /dev/disk/by-id/ata-WDC_WD5000AAKX-083CA1_WD-WMAYUR371592,serial=WD-WMAYUR371592,iothread=1`
  on `pve` (safe hotplug). But re-importing the pool failed
  (`pool.import_find` found nothing) because **the disk had zero partitions
  — its ZFS label was gone, not just unmounted** (confirmed two ways via the
  TrueNAS API: `disk.get_unused` and `device.get_disks` both showed
  `"parts": []`). Cause unknown — the disk sat unmapped at the Proxmox-host
  level for an unknown period and something wiped it during that window.
  **Everything backup-tank held before this (app data, data-store files,
  compose exports) is gone** — there was nothing to import or recover.
  Fixed by exporting the dead pool record (`pool.export 2`) and recreating
  the pool fresh on the same disk (`pool.create`), then recreating the
  `apps` (lz4) and `data-store-files` (zstd) datasets the backup script
  expects. The daily cron job log confirms the exact outage window:
  SUSPENDED from 2026-10-18, then MISSING every single day through
  2026-11-04 (18 days with zero successful backups) — the job's own
  pool-health pre-flight check correctly refused to write during all of it,
  rather than failing partially or corrupting anything.
- See `../../docs/changelog/171026-103353_jellyfin_11850_and_storage_fixes_changelog.md`
  for the original Oct 2026 incident this backup/remux tooling was built for.

## Known gaps in this doc (need root/sudo access to close)

- Exact per-app `docker-compose.yaml` / env vars (see section 4).
- `wgbak` peer list and keys (section 5).
- Whether the SMB share overlap (`storage` vs `data-store`) was ever resolved.
