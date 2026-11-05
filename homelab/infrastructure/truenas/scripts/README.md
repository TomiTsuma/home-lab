# TrueNAS cron scripts

Live copies pulled from `/mnt/storage-tank/data-store/scripts/` on TrueNAS
(2026-11-05). All three run as root via TrueNAS cron jobs (System > Advanced >
Cron Jobs in the UI) pointed at this exact path — after a rebuild, copy these
files back to `/mnt/storage-tank/data-store/scripts/` and recreate the 3 cron
jobs listed in `../README.md` section 6.

## `backup_to_backup_tank.py`

Daily (14:00) backup of irreplaceable data from `storage-tank` (2-disk
stripe, no redundancy) to `backup-tank` (separate physical disk):
- **App data**: atomic recursive ZFS snapshot of `storage-tank/ix-apps`
  (crash-consistent for the Postgres/SQLite inside every App), then
  incremental `zfs send | zfs recv` into `backup-tank/apps/ix-apps`.
- **data-store folders**: rsync of specific valuable paths (audiobooks,
  Jellyfin config/data/metadata, scripts, home-lab, etc.) from a short-lived
  snapshot into `backup-tank/data-store-files`.
- **TrueNAS config DB** (`freenas-v1.db` + `pwenc_secret`), root-only —
  enough to rebuild the NAS config on a fresh install.
- **Every App's rendered compose/config** (`docker-compose.yaml`,
  `user_config.yaml`, `metadata.yaml` per app) copied to
  `backup-tank/data-store-files/_compose/<app>/` — this is the best source
  for exact per-app recreation if you have access to a recent backup-tank
  snapshot.
- **Pre-flight**: skips cleanly (doesn't fail/hang) if `backup-tank` isn't
  ONLINE. Reports success/failure to an Uptime Kuma push monitor (URL in
  `.backup_kuma_push_url`, not committed — a secret).
- Retention: 14 days on source snapshots, 30 days on backup-tank snapshots.

Usage: `backup_to_backup_tank.py [--dry-run]`

### Restore cheat-sheet (run as root on TrueNAS)

- Browse a backed-up app dataset:
  `zfs mount backup-tank/apps/ix-apps/app_mounts/<app>/<ds>` → files appear
  under `/mnt/backup-tank/apps/ix-apps/...`; `zfs unmount` it afterwards.
- Browse an older file version:
  `ls /mnt/backup-tank/data-store-files/.zfs/snapshot/`, or
  `zfs list -t snapshot -r backup-tank/apps`.
- Full app restore: stop the app, then
  `zfs send -p backup-tank/apps/ix-apps/app_mounts/<app>@<snap> | zfs recv -F storage-tank/ix-apps/app_mounts/<app>-restored`,
  then swap the names.
- Get an app's exact compose config:
  `cat /mnt/backup-tank/data-store-files/_compose/<app>/docker-compose.yaml`.

## `remux_to_mp4.py`

Converts MKV files in the Sonarr/Radarr libraries to MP4 so Apple clients
(Jellyfin iOS/iPadOS native player = AVPlayer) can Direct Play them — AVPlayer
can't open MKV, and Jellyfin's MKV fallback (a progressive fragmented-MP4
remux with no byte-range support) fails with `AVFoundationErrorDomain -11850`.

- Video is never re-encoded; only H.264 (8-bit) and HEVC-without-Dolby-Vision
  get converted. Audio Apple can't decode → E-AC-3 640k. Text subs → sidecar
  `.srt/.ass/.vtt`; PGS → sidecar `.sup`; DVD/DVB bitmap subs are skipped
  (left as MKV for Jellyfin to HLS-transcode).
- Writes to a temp file, verifies (duration, stream counts, size), renames
  into place, only then deletes the source MKV — never a moment with neither
  file present.
- Skips files that share ZFS-cloned blocks with a Downloads-folder twin
  (converting them would **add** disk space rather than being free, since
  Sonarr/Radarr "copy" imports are block clones).
- Runs ffmpeg/ffprobe inside a throwaway container built from the live
  Jellyfin image (TrueNAS itself has no ffmpeg).
- After converting, asks Sonarr/Radarr to rescan and refreshes Jellyfin.

Usage: `remux_to_mp4.py` (dry run, prints the plan) or
`remux_to_mp4.py --apply [--limit N] [--max-minutes N]` (actually convert;
the live cron uses `--apply --max-minutes 360`).

Full incident writeup: `../../../docs/changelog/171026-103353_jellyfin_11850_and_storage_fixes_changelog.md`.

## `nextcloud_to_paperless.py` + `test_nextcloud_to_paperless.py`

Runs every 15 minutes. Mirrors specific Nextcloud folders ("research",
"Personal") and moves new items into the Paperless-ngx consume inbox, so
documents dropped into Nextcloud get auto-ingested into Paperless. Discovered
during the 2026-11-05 audit — not previously documented anywhere. State is
tracked in `nextcloud_to_paperless.state.json` (not committed — runtime
state, regenerates on first run). Has its own test suite
(`test_nextcloud_to_paperless.py`) — run it after any change to the script.
