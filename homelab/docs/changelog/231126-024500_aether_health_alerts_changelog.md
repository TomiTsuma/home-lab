# Aether service health alerts, plus the outage found while building them (2026-11-23)

**Request:** a separate email alert with a full report of whether the Aether (ereuna) services on
aeacus-data-handler are up, working, and able to reach one another, with robust communication tests.

## What was built (`homelab/monitoring/aether-health/`)
- `aether_health.py` runs on pve. It ships the probes over SSH, adds pve-side RAM/swap checks, runs the alert state machine
  (alert after 2 consecutive failing runs, reminder every 6 h, recovery mail) and sends HTML + text email via mail-relay.
- `probes/host_probe.py` runs on aeacus: host pressure, 15 containers, uvicorn worker-death scan, direct `/health`,
  gateway routes through nginx that force each service to query its datastore, native datastore checks, and the public URL.
- `probes/container_probe.py` runs inside each service container: DNS → TCP → real protocol request to each dependency
  with the container's own env and credentials. Secrets are scrubbed from output.
- `aether-health.timer` (every 5 min) and `aether-health-report.timer` (07:15 EAT), both serialized with `flock`.
- `/etc/aether-health/acknowledged.txt` on pve silences known issues.
- `test_aether_health.py`: 14 tests (state machine, acks, HTML escaping, and the probe protocols against fake Redis, Kafka and HTTP servers). Passing.
- Verified: first report sent 02:39 and the first alert 02:40, both `status=sent (250)` via Brevo.

## Incident: Aether outage, ~01:30–02:20 EAT (found while building this)
- **Symptom:** every Aether API timed out while Docker showed all containers *healthy*. Load on aeacus reached 285 on 2 vCPUs.
- **Cause chain:** pve has 40 GB RAM, but its VMs get 44.9 GB (100: 16 GB, 101: 12 GB pinned by GPU passthrough, 102: 16 GB).
  pve had ~300 MB free and had swapped 2.7 GB of VM 100's memory. Guest threads stall on every swapped page.
  uvicorn with `--workers 2` (auth, repository, platform) kills a worker that misses its 5 s heartbeat and spawns
  a new one. Under starvation the new one can't boot in 5 s either, so workers died about every 5 s and that loop kept the CPU busy.
  datasets and inference run a single process, have no supervisor, and did not loop.
- **Actions (the owner chose "restart containers only"):** restarted the 5 app containers, which stopped the loop. The restart
  command timed out and left datasets stopped, so it was started again. It took ~13 min to come up under the starvation.
  Restarting gave platform, inference and datasets new IPs, and nginx (ereuna_web) had resolved upstream names only at startup,
  so it kept proxying to the old IPs and returned 502. Fixed with `docker exec ereuna_web nginx -s reload`.
- **Not fixed (owner decision):** pve memory overcommit. VM 100 still has ~2.3 GB on pve swap, so this can recur.
  Options: lower VM 102's RAM or ZFS ARC; lower VM 100/101 RAM; add RAM; run uvicorn with one worker or a longer
  `--timeout-worker-healthcheck` on this 2-vCPU VM; add `resolver 127.0.0.11 valid=10s` plus variable upstreams in
  `WebClient/nginx.conf` so nginx survives service restarts.
- My probe requests (a few dozen HTTP calls) overlapped with the start of the spiral. They are too small to cause it, but
  I can't rule out that they tipped an already starved VM.

## Pre-existing bugs the new checks found (not fixed, Aether repo)
1. `GET /api/datasets/datasets/` returns 500: `column datasets.dataset_type does not exist`, a migration missing in prod.
2. Repository and datasets → OpenSearch: OpenSearch is HTTPS-only, but the services use HTTP (`OPENSEARCH_USE_SSL=false`/unset).
   Repository search still answers 200, which suggests it swallows the error.
3. datasets → MinIO: `MINIO_ENDPOINT=minio:9000` (compose default), but no `minio` host exists. Repository uses `s3.ereuna.org`.
4. Neo4j HTTP :7474 doesn't answer (bolt does). No service uses Neo4j yet.
5. Reading container env during diagnosis showed that the JWT private key is passed as a container env var,
   so anyone with `docker inspect` on aeacus can read it. Prefer a mounted secret file, or Vault, which is already configured.
