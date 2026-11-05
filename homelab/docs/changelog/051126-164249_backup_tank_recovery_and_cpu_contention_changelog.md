# 2026-11-05 — backup-tank data loss + recovery, CPU contention fix, Gitea→S3 backup

Continuation of the same live-action session as the Cloudflare/GlitchTip
changelog earlier today. Triggered by two user reports: Lyre playback
lagging, and a request to verify backup-tank + Gitea backups.

## CPU contention diagnosis (Lyre playback lag)

- `aeacus-data-handler` has only **2 CPU cores** but runs ~29 containers —
  Lyre plus the entire Ereuna platform (Kafka, ClickHouse, Neo4j,
  OpenSearch, Zookeeper, multiple microservices) and Harbor. Load average
  was sitting at 3.2–3.5 on 2 cores; `ereuna_platform_service` and
  `ereuna_datasets_service` were caught spiking to 35%/22% in one snapshot,
  near-zero moments later (bursty, not constant) — this is a pre-existing
  structural issue, not something introduced today.
- Contributing factor introduced today: enabling `GLITCHTIP_EMBED_WORKER`
  (see the earlier changelog) started a worker that drained a 61-item
  stale event backlog and now runs periodic maintenance/alert-check tasks
  indefinitely — a real, continuous addition on a box with zero headroom.
- **Fix**: capped the `glitchtip` container to `cpus: 0.5, mem_limit: 512m`
  in both the live compose and this repo's copy, so it can't meaningfully
  compete with Lyre again regardless of what its worker is doing.
- **Not fixed** (bigger decision, left for the owner): the underlying 2-core
  oversubscription on `aeacus-data-handler`. Needs either more vCPUs or
  moving/lightening some Ereuna services — not something to do unilaterally.

## backup-tank: disk was wiped, not just disconnected

See the updated "Known incidents" section in `infrastructure/truenas/README.md`
for the full writeup. Summary: the disk behind backup-tank had zero
partitions (ZFS label gone, not just unmounted) — everything it held before
is lost. Recovered by exporting the dead pool record and recreating the pool
+ its two datasets (`apps` lz4, `data-store-files` zstd) fresh on the same
disk. Confirmed via the cron log that the pool was down for **18 straight
days** (SUSPENDED 2026-10-18, then MISSING every day through 2026-11-04) —
the backup script's pool-health pre-flight check correctly no-op'd the whole
time rather than writing partial/corrupt data.

Triggered a real backup run immediately via `midclt call cronjob.run 2`
(the script needs root, which this session's `truenas_admin` account
doesn't have — TrueNAS's own cron daemon runs it as root regardless) to
confirm the fix actually works end-to-end, not just that the pool shows
ONLINE. It was still running a full initial sync (all ~62 app datasets,
including Immich's photo library) as of this writeup — future runs will be
fast incrementals.

### API usage notes (for next time)

- `midclt call <method> 'JSON'` passes each *token* as its own positional
  argument already JSON-decoded — `'[2]'` as one token means "the list
  `[2]`", not "the integer `2`". For multi-arg methods, pass each argument
  as a separate shell token: `midclt call pool.export 2 '{"cascade": true}'`.
- `pool.dataset.query` with no `extra.flat: false` only returns each pool's
  *direct* children, not the full recursive tree — `storage-tank/ix-apps`
  genuinely exists (confirmed via `docker.config`'s `dataset` field, and by
  watching `zfs send`/`recv` processes actively operate on it) but never
  shows up in a default query. Don't conclude a dataset is missing from a
  query result alone.

## Added: Gitea repos → S3 backup (`gitea_repos_to_s3.py`)

Per the user's request, in addition to the local storage-tank → backup-tank
coverage (which already includes all of Gitea's app data), added a second,
independent backup of just the bare git repositories to the `gitea-backup`
bucket on MinIO (`aeacus-data-handler`). See
`infrastructure/truenas/scripts/README.md` for full detail. Headlines:

- Uses the **LAN IP** of aeacus-data-handler (`192.168.100.9:9000`, found by
  checking `ip -4 -o addr show` on that host and confirmed by the user) —
  deliberately not the public `s3.ereuna.org` domain, so this runs over the
  local network only, per the user's explicit request.
- First run: **980 files uploaded successfully**, verified by listing the
  bucket afterward (not just trusting the script's own exit code).
- Runs daily at 14:30 TrueNAS time via a new cron job (id 5).
- **Compromise, flagged not fixed**: uses MinIO's root credentials (same
  account already flagged for password reuse in the earlier recreation-audit
  changelog) because a properly scoped, bucket-limited access key couldn't
  be created — MinIO discontinued free public downloads of its `mc` admin
  client (`dl.min.io` now returns `410 Gone` for the binary and its
  checksum). The credentials file is at least locked down path-wise: stored
  next to the script, permissions tightened to `0700`/owner-only via
  `midclt filesystem.setacl` (plain `chmod` doesn't work on this dataset —
  see below).

### NFSv4 ACL gotcha (confirmed, matches prior memory)

`storage-tank/data-store` has `aclmode=restricted` — `chmod` fails outright
("Operation not permitted") even for files owned by the calling user, and
every new file inherits a wide-open `everyone@: FULL_CONTROL` ACL from the
parent directory. Fixed by using `midclt call filesystem.setacl` to set an
explicit `owner@: FULL_CONTROL` ACL with `NOINHERIT` instead of relying on
POSIX `chmod` — this actually works on this dataset type.
