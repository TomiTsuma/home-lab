# MLflow tracking server: setup + repo documentation

**2026-11-08**

## What changed

Stood up a real MLflow 3.7.0 tracking server on `aeacus-data-handler`
(`~/mlflow/`, container `mlflow`, port 5001), backing the experiment-tracking
design in `Aether/research/well_logs_project/docs/TRACKING.md` (Jira
`KAN-106`). Reused existing shared infra rather than adding new services:

- **Postgres**: new role `mlflow_user` + database `mlflow` inside the
  existing `ereuna_postgres` container (not a new Postgres instance).
- **MinIO**: new bucket `mlflow-artifacts` on the existing native MinIO
  service (`/mnt/data`, systemd, ports 9000/9001) — not a new object store.
- **New container**: `mlflow` (custom image `ereuna/mlflow-server:3.7.0`,
  built from `homelab/compute/mlflow/Dockerfile`), joined to the existing
  `ereuna_network`, published on host port 5001 (5000 is Harbor's).
- Basic-auth protected (MLflow's experimental `--app-name basic-auth`),
  `--allowed-hosts` locked to known client addresses.
- Verified end-to-end from `aeacus-dl`: a real run's params/metrics landed in
  Postgres and its artifact landed in `s3://mlflow-artifacts/...` via the
  server's artifact proxy (`--serve-artifacts`).

Full design/rationale is in `homelab/compute/mlflow/README.md`.

## Repo changes

- `homelab/compute/mlflow/`: filled in the previously-stub `docker-compose.yml`
  (was a one-line placeholder) with the real, parameterized compose file, and
  added `Dockerfile`, `entrypoint.sh`, `.env.example`, `README.md`.
- `homelab/ai/mlflow/mlflow-install.sh`: this was a **different, never-deployed
  design** (native systemd install, SQLite backend, local-file artifacts,
  port 5000 — which would have collided with Harbor). Replaced its content
  with a pointer to `homelab/compute/mlflow/`, rather than maintaining two
  contradictory MLflow scaffolds.

## Known gaps / flagged for the owner

1. **MinIO credentials are root, not scoped.** MinIO Inc. has locked down
   free distribution of their admin tooling — the `mc` client binary
   (`dl.min.io`) and the `minio/mc` Docker image both returned access-denied
   when this was built. Without `mc admin user add`/`policy attach`, there
   was no quick path to a bucket-scoped service account, so the mlflow
   server's `.env` currently holds the MinIO **root** access key/secret
   (same ones in `/etc/systemd/system/minio.service`). Acceptable for a
   single-operator homelab for now; revisit if a scoped-credential path
   reopens (MinIO Python SDK admin support, or raw signed admin-API calls).
2. **Cloudflare hostname: done.** `tracking.ereuna.org` was added as a
   Public Hostname on the "Ereuna" tunnel (ID
   `b76fe173-5ecd-4cc2-b76f-2c48e55b11af`) pointing at `localhost:5001`,
   added by hand via the dashboard (no Cloudflare API token with
   tunnel/DNS-edit scope was available to automate it). Verified reachable
   over the public internet (health check + basic auth both 200). The
   server also stays reachable over the tailnet directly at
   `http://<aeacus-data-handler-tailscale-ip>:5001`.
3. **`--allowed-hosts` needs updating per new client.** mlflow's
   DNS-rebinding guard's default allowlist doesn't cover Tailscale/CGNAT
   addresses — every training host's IP (port-qualified for IP:port access)
   must be added to `MLFLOW_ALLOWED_HOSTS` and the container recreated.
   Only `aeacus-dl` is allowlisted today.
4. DVC and `results/experiments.csv` (the rest of `TRACKING.md`'s design)
   are not part of this change — MLflow only.

## Also resolves

The open question in `well_logs_tracking_decision` (project memory,
Jira `KAN-106`'s 2026-11-01 comment recommending a CSV registry instead of
MLflow, vs. `TRACKING.md`'s full-MLflow design): the owner explicitly
requested the full MLflow build, so that's the settled direction for this
project going forward.
