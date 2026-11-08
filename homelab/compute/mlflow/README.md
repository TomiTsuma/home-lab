# MLflow tracking server

Experiment-tracking server for Ereuna's ML work (first consumer: the
`well_logs_project` research repo's `docs/TRACKING.md` design). Deployed on
`aeacus-data-handler` (VM 100), reusing that host's existing shared Postgres
and MinIO rather than standing up new infrastructure — matching how Lyre's
GlitchTip and the rest of the Aether stack are deployed there.

Live at `https://tracking.ereuna.org` (Cloudflare Tunnel — see
"Cloudflare hostname" below) and `http://<aeacus-data-handler-tailscale-ip>:5001`
directly. First deployed and smoke-tested 2026-11-08.

## Architecture

```
training client (e.g. aeacus-dl) ──HTTPS, basic auth──► tracking.ereuna.org
                                                           │ (Cloudflare Tunnel,
                                                           │  dashboard-managed,
                                                           │  routes to localhost:5001)
                                                           ▼
                                            aeacus-data-handler:5001
                                                           │
                                     ┌─────────────────────┼─────────────────────┐
                                     ▼                                           ▼
                        ereuna_postgres (container,                  native MinIO (systemd,
                        db `mlflow`, role `mlflow_user`)              bucket `mlflow-artifacts`)
```

- The `mlflow` container joins the existing external `ereuna_network`
  (created by `ereuna/docker-compose.prod.yml`, not by this compose file) so
  it can reach `ereuna_postgres` by container name.
- MinIO is a **native systemd service** on this host, not a container — the
  `mlflow` container reaches it via the bridge gateway `172.22.0.1:9000`,
  the same route already used for the public `s3.ereuna.org` endpoint (see
  `../../data-services/minio/`).
- `--serve-artifacts` is on, so the server proxies artifact up/downloads —
  training clients only need `MLFLOW_TRACKING_URI` + basic-auth credentials,
  never MinIO credentials directly.
- Port **5001**, not MLflow's usual 5000 — Harbor's nginx already owns 5000
  on this host.
- `ereuna-cloudflared` runs with `network_mode: host`, so its tunnel routes
  reach any host-published port via `localhost:<port>`.

## Deploying

```sh
# on aeacus-data-handler
mkdir -p ~/mlflow && cd ~/mlflow
# copy Dockerfile, entrypoint.sh, docker-compose.yml, .env.example from this
# directory, then:
cp .env.example .env
# fill in .env — see its comments for how each value was generated/sourced
docker compose up -d --build
```

One-time Postgres + MinIO setup (already done on the live deployment, kept
here for disaster recovery): see the SQL block and `aws s3 mb` command in
`.env.example`'s comments.

### Verifying

```sh
curl -s http://localhost:5001/health
# from a training host, with MLFLOW_TRACKING_URI/USERNAME/PASSWORD set:
python3 -c "
import mlflow
mlflow.set_experiment('smoke-test')
with mlflow.start_run():
    mlflow.log_param('p', 1)
    mlflow.log_metric('m', 1.0)
"
```
Confirmed working end-to-end 2026-11-08 from `aeacus-dl` (run id
`cf0c5a8b1fd84041a8822e0ad820540f`): experiment/run metadata landed in
`ereuna_postgres`, and the logged artifact landed in
`s3://mlflow-artifacts/1/cf0c5a8b1fd84041a8822e0ad820540f/artifacts/smoke/`.

## Cloudflare hostname

**Live** as of 2026-11-08: `https://tracking.ereuna.org` → the "Ereuna"
tunnel (ID `b76fe173-5ecd-4cc2-b76f-2c48e55b11af`, same tunnel that already
serves `s3.ereuna.org` and `logs.ereuna.org`) → `localhost:5001`. Added via
the Cloudflare dashboard (ingress for this tunnel is dashboard-managed —
there's no local config file to edit, and no Cloudflare API token with
Tunnel/DNS-edit scope is kept anywhere in this repo's secrets).

For disaster recovery, recreate it by hand:

1. Cloudflare Zero Trust dashboard → Networks → Tunnels → "Ereuna" tunnel →
   Public Hostname → Add a public hostname.
2. Subdomain `tracking`, domain `ereuna.org`, type `HTTP`, URL
   `localhost:5001`.
3. The dashboard flow creates the DNS CNAME automatically
   (`tracking.ereuna.org` → `b76fe173-5ecd-4cc2-b76f-2c48e55b11af.cfargotunnel.com`,
   proxied).

The server also stays reachable over the tailnet directly at
`http://<aeacus-data-handler-tailscale-ip>:5001`, which is what the
end-to-end smoke test above used.

## Known limitations / TODO

- **MinIO uses root credentials, not a scoped service account.** MinIO's
  `mc` client (both the `dl.min.io` binary download and the `minio/mc`
  Docker image) returned access-denied when this was built (2026-11-08) —
  MinIO Inc. has locked down free distribution of their admin/IAM tooling.
  Without `mc admin user add` + `policy attach`, there was no quick way to
  mint a bucket-scoped key, so `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`
  in `.env` are the MinIO server's own root credentials. Acceptable for a
  single-operator homelab short-term; revisit via the MinIO Python SDK's
  admin surface or raw signed admin-API calls if this ever needs to be
  properly scoped.
- **`--allowed-hosts` is a flat list, not a pattern.** mlflow's DNS-rebinding
  guard defaults to "localhost + private IPs," which does **not** cover
  Tailscale/CGNAT addresses (100.64.0.0/10) — every client hostname/IP,
  port-qualified for IP:port access, must be added to
  `MLFLOW_ALLOWED_HOSTS` by hand and the container recreated. A new training
  host (e.g. a second GPU VM) needs this step before it can log runs.
- **Basic auth is MLflow's own "experimental" feature** (its log output says
  so on every boot) — fine for a solo-founder deployment, but don't treat it
  as a long-term auth story if this ever gets a second real user; MLflow's
  docs note the API may change without warning.
- `results/experiments.csv` (the append-only frozen record described in
  `well_logs_project/docs/TRACKING.md`) and DVC are a separate, still-pending
  piece of that project's tracking design — this server only covers the
  MLflow leg.
