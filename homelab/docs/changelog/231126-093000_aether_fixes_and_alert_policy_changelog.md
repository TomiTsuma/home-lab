# Aether fixes + alert policy: only unreachable services, max one email a day (2026-11-23)

**Request:** resolve the Aether issues behind the health email; email only when a container is down or a service
can't be reached, not every time, and at most once a day.

## Aether fixes (production, aeacus-data-handler). Details in the Aether repo changelog `231126-100000_...`
- `dataset.datasets` was missing `dataset_type`, `visibility` and `owner_id`, causing a 500 on the list endpoint. Backed up the schema,
  then added the columns idempotently.
- repository: `OPENSEARCH_USE_SSL=true` (OpenSearch is TLS-only).
- datasets: recreated with `--env-file`, so `MINIO_ENDPOINT` is now `s3.ereuna.org` over TLS (it was a non-existent `minio:9000`).
  Removed the overriding MinIO defaults from the compose `x-service-env`.
- **OpenSearch admin password was the 2.11 demo default `admin`.** It is now rotated to the `.env.production` value
  via `securityadmin.sh` (the reserved user can't be changed through the API).
- auth, repository and platform now use `--workers 1`. With `--workers 2`, uvicorn's 5 s heartbeat turned memory stalls into
  a respawn loop. That loop recurred today at 08:40 after the containers were recreated.
- Backups in `~/ereuna/backups/`: dataset schema dump, `.env.production`, and `docker-compose.prod.yml` before each change.
- Result: the full health check went from 4 critical to **66 checks, 0 critical**.

## Alert policy (homelab/monitoring/aether-health)
- Critical now means *unreachable* only:
  - a container down or unhealthy;
  - no answer from a service;
  - nginx 502/503/504;
  - a dependency that can't be reached;
  - a datastore down;
  - the public site down;
  - aeacus itself unreachable.
- Host load/RAM/pressure, pve RAM/swap, worker restarts, HTTP 500s and slowness are now warnings only.
- Emails: alert after 2 failing runs, then nothing for 24 h. There are no reminder, recovery or scheduled daily-report emails. The
  07:15 `aether-health-report.timer` is removed (the service stays, for a manual full report).
- Probe fix: an unset `OPENSEARCH_USE_SSL` now means TLS (that's the code default). This removes the datasets false positive.
- Tests rewritten for the new policy (13 passing).

## Security scan fix (homelab/security)
- The first full Trivy scan unpacked ~28 images on aeacus-data-handler and was a main driver of today's second load spike.
  Scanner containers are now capped at `--cpus 0.5 --memory 1g`, use a persistent layer cache (`homelab-trivy-cache`),
  and the scan skips a host whose load exceeds its CPU count (previous results are kept).
