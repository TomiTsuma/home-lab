# 2026-10-17 — Jellyfin "-11850" playback fix + Proxmox/TrueNAS remediation

Hosts: Proxmox `pve` (100.124.198.112, TZ EAT) · TrueNAS SCALE VM 102 (LAN 192.168.100.10, Tailscale 100.100.133.10, TZ America/Los_Angeles)

## Root cause of the playback error

`AVFoundationErrorDomain -11850` ("server is not correctly configured") = AVPlayer could not make byte-range requests.

- The Jellyfin iOS/iPadOS 1.8.0 app uses AVPlayer, which cannot open MKV.
- For some MKVs (e.g. H.264/AC-3) Jellyfin fell back to a **progressive fragmented-MP4 remux** (`-f mp4 -movflags frag_keyframe+empty_moov`), served as `200 OK`, `Accept-Ranges: none`, chunked, no length.
- AVPlayer requires byte ranges for progressive HTTP media → fails at 0 ms.
- Verified with curl on the failing episode: static file → `206` + ranges; progressive remux → `Accept-Ranges: none`; HLS → OK.
- It looked random because the outcome depended on each file's container/codec.

## Changes made

| # | Where | Change | Undo |
|---|---|---|---|
| 1 | TrueNAS user `aeacus` | shell `nologin` → `/usr/bin/bash`, password SSH enabled (needed for the SSH access the owner asked to use) | TrueNAS UI → Credentials → Users → aeacus |
| 2 | Proxmox VM 102 | `net0` e1000 → **virtio** (same MAC). Applied live by Proxmox hotplug, which dropped the TrueNAS default route for ~2 min (restored with `ip route replace default via 192.168.100.1`; DHCP-managed again after reboot) | `qm set 102 --net0 e1000=BC:24:11:0C:B0:E6,bridge=vmbr0,firewall=1` |
| 3 | Proxmox VM 102 | `scsi0` gets `discard=on,ssd=1`; QEMU guest agent enabled; clean restart 09:22–09:35 EAT | backup: `/root/config-backups/102.conf.20261017` on pve |
| 4 | TrueNAS | `zpool set autotrim=on boot-pool` + `zpool trim boot-pool` → thin pool `local-lvm` **99.74% → 70.17%** (~103 GB returned) | `zpool set autotrim=off boot-pool` |
| 5 | Proxmox VM 101 (aeacus-dl) | `onboot: 0` — it was OOM-killed at every boot (Oct 13/15/16); 60 GB of VM RAM promised on a 39 GB host | `qm set 101 --onboot 1` (lower its memory first) |
| 6 | Jellyfin app | added host path `/mnt/storage-tank/data-store/Downloads` → `/media/downloads` **read-only** (mount had been lost in an app update; 166 library items pointed to it) | config backup: `jellyfin_app_config_backup_20261017.json` (session scratchpad) |
| 7 | Jellyfin "home" library | removed dead paths `/media/Downloads`, `/media/Downloads/Downloads` (edited `root/default/home` directly; the API can't delete them because it matches paths case-insensitively) | backup: `data-store/scripts/jellyfin-home-library-backup-20261017/` |
| 8 | navidrome app | data + music paths `data-store/music` (missing) → `data-store/media/music` (where the music and `navidrome.db` now live); app running again | backup: `navidrome_app_config_backup_20261017.json` |
| 9 | audiobookshelf container | restart loop stopped (`docker update --restart=no`); app was already STOPPED in TrueNAS | start the app from the TrueNAS UI |
| 10 | Jellyfin | new API key `remux-to-mp4` stored at `/root/.remux_jellyfin_key` (root-only) on TrueNAS | Dashboard → API Keys |
| 11 | TrueNAS | **MKV → MP4 conversion**: script `data-store/scripts/remux_to_mp4.py` (copy in `../../scripts/`); backlog started as systemd unit `remux-backlog` (8 h budget); nightly cron job id 1 at 14:30 LA = 00:30 EAT (6 h budget) | TrueNAS UI → System → Advanced → Cron Jobs |

## How the conversion works

- Covers `media/tv` and `media/movies` (Sonarr/Radarr libraries). `Downloads` is untouched (qBittorrent is seeding it).
- Video is copied, never re-encoded. Apple-incompatible audio (DTS/TrueHD/PCM/FLAC/Opus) → E-AC-3 640k. Text subs → sidecar `.srt/.ass/.vtt`, PGS → `.sup`.
- Skipped and left for Jellyfin to HLS-transcode: Dolby Vision, 10-bit H.264, other codecs, DVD-bitmap subs. Dry run: 418 convertible, 1 skipped (Black Widow DV).
- Writes to a temp file, verifies duration and stream counts, renames into place, then deletes the MKV. Keeps owner and mtime.
- Asks Sonarr/Radarr to rescan afterwards (verified: Sonarr tracks the new `.mp4`) and refreshes Jellyfin.
- Verified on Avatar Seven Havens S01E03: Jellyfin serves it `206 Partial Content`, `Accept-Ranges: bytes`, `video/mp4`.
- Log: `/mnt/storage-tank/data-store/scripts/remux_to_mp4.log`. Stop a run with `systemctl stop remux-backlog`.

## Not changed (needs a decision or hardware)

- **SMB share `storage`** at the pool root (`/mnt/storage-tank`, POSIX) overlaps `data-store` (NFSv4) and raises a CRITICAL alert. TrueNAS refuses even to disable it while the mismatch exists; fix by deleting the share (data-store share covers the data) — not done without owner OK.
- **Host crashes**: 4 boots end with no shutdown/panic logs (Oct 10, 13, 14, 16) → likely power loss or a hard hang. A UPS and PSU check are recommended.
- **No redundancy**: `storage-tank` is a 2-disk stripe; the Hitachi drive has ~40,900 power-on hours. Add a mirror or a backup.
- **Thin pool overcommit**: 820 GB of virtual disks on a 350 GB pool, VG has 572 MB free. VM 100 has discard on — run `fstrim -av` inside it. Long term, reinstall TrueNAS onto a ~32 GB system disk.
- **audiobookshelf** fails migrations because the NFSv4 "restricted" ACL mode blocks chmod. Fix: move its `/config` to an ix-volume, or set data-store aclmode to passthrough.
- 21 app updates and a TrueNAS system update are pending.
- Stopgap for files not converted yet: Jellyfin iOS app → Settings → turn off "Native Video Player".

## Update — space audit (same day, ~10:50 EAT)

- **Found:** 422 GB of `storage-tank` is ZFS block-cloned. Sonarr/Radarr "copy" imports from `Downloads` share blocks with the library file, so most media costs no extra space. Converting a cloned MKV writes a full new MP4 while the Downloads copy keeps the old blocks, so it **adds** space.
- **Action:** paused the backlog after 17 conversions (~7 GB net growth), cleaned temp files, and briefly disabled cron job 1.
- **Script change:** `remux_to_mp4.py` now skips any MKV with a same-size twin in `Downloads` (log line `SKIP shares blocks with a Downloads copy`). Dry run: 49 files / 91 GB are convertible at no space cost; 352 wait until their torrents are removed. Cron job 1 re-enabled.
- Nothing was deleted in the space audit; the candidates are listed in the session report for owner approval.

## Update — backup pool `backup-tank` (option D, ~11:00–11:35 EAT)

| Change | Detail | Undo |
|---|---|---|
| Wiped host disk `sdc` | WDC WD5000AAKX, serial WD-WMAYUR371592 (741 h, healthy). Held an abandoned Windows Server install (1.9 GB, no user files; inspected read-only first). `sgdisk --zap-all` + `wipefs -a` | n/a (data was not kept) |
| Attached it to VM 102 | `qm set 102 --scsi3 /dev/disk/by-id/ata-WDC_WD5000AAKX-083CA1_WD-WMAYUR371592,serial=WD-WMAYUR371592,iothread=1` (hot-plugged; appears as `sdd` inside TrueNAS) | `qm set 102 --delete scsi3` after exporting the pool |
| Created pool `backup-tank` | single disk, 460 G; datasets `apps` (lz4) and `data-store-files` (zstd); weekly scrub moved to Wednesday 00:00 (storage-tank scrubs Sunday) | export/destroy pool in UI |
| Backup script | `data-store/scripts/backup_to_backup_tank.py` (copy in `../../scripts/`), log `backup_to_backup_tank.log` next to it | — |
| Cron job id 2 | daily 14:00 TrueNAS time (= 00:00 EAT), runs before the remux job (14:30) | disable in UI |

**What is backed up (44 GB initially; daily runs are incremental, ~1 min):**
- **App data** — every dataset under `storage-tank/ix-apps` (62 datasets: Immich photos + Postgres, Nextcloud data + Postgres, Gitea, *arr/Prowlarr/Seerr configs, NPM certs, Tailscale state, Portainer, Uptime-Kuma, app definitions…). Uses an atomic recursive snapshot + per-dataset `zfs send -p -I | zfs recv` → `backup-tank/apps/ix-apps`. The replica is read-only and `canmount=noauto`, with mountpoint `/mnt/backup-tank/apps/ix-apps`, so it can never cover the live `/mnt/.ix-apps`. Excluded: `docker` (images), `truenas_catalog`, orphaned `jellyfin/storage_entry`. TrueNAS' own snapshot/replication tasks refuse `ix-apps` ("Dataset not found"), hence the script.
- **data-store folders** (rsync from a temporary snapshot, then a snapshot of the target): audiobooks, Jellyfin config/data/metadata/plugins/root/log, remux, home-lab, plex, scripts, Downloads/qBittorrent (client state), tst.yml.
- **TrueNAS config**: `freenas-v1.db` (sqlite `.backup`) + `pwenc_secret` in root-only `_truenas-config/`. That is enough to rebuild the NAS config on a fresh install.
- Retention: source `bk-` snapshots 14 days, backup-tank snapshots 30 days.
- Verified: Immich 3,908/3,908 files and Nextcloud 16,588/16,588 match the live data; the backed-up `jellyfin.db` passes `integrity_check`; second and third runs were incremental.
- **Not backed up on purpose:** media/ and Downloads/ (re-downloadable, ~1.1 TB, would not fit).

**Restore cheat-sheet (run as root on TrueNAS):**
- Browse a backed-up app dataset: `zfs mount backup-tank/apps/ix-apps/app_mounts/<app>/<ds>` → files appear under `/mnt/backup-tank/apps/ix-apps/...`; `zfs unmount` it afterwards.
- Older version: `ls /mnt/backup-tank/data-store-files/.zfs/snapshot/` (data-store files), or `zfs list -t snapshot -r backup-tank/apps`.
- Full app restore: stop the app, then `zfs send -p backup-tank/apps/ix-apps/app_mounts/<app>@<snap> | zfs recv -F storage-tank/ix-apps/app_mounts/<app>-restored`, then swap the names.
