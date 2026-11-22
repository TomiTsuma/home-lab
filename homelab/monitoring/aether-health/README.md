# Aether health alerts

Every **5 minutes** the Proxmox host checks the whole Aether (ereuna) stack on aeacus-data-handler, end to end.
It emails `tomitsuma@ereuna.org` through the TrueNAS [mail-relay](../../messaging/mail-relay/README.md) when
something breaks and again when it recovers. A full report goes out every morning at **07:15 EAT**.

This is separate from the [daily homelab report](../daily-report/README.md). That one covers machines; this one
covers whether Aether's services work and can reach one another.

## What it tests (~65 checks, 8 layers, bottom-up)
The layers run bottom-up, so the first failing layer shows where the break is.

| Layer | How | Catches |
|---|---|---|
| Proxmox host | pve `/proc/meminfo`, VmSwap of VM 100's `kvm` process | pve out of RAM swapping the VM's memory. This caused the 2026-11-22 outage |
| Host | aeacus load, PSI cpu/io/memory pressure, RAM | starvation that makes every service slow |
| Containers | `docker inspect` of 15 containers | stopped, unhealthy, OOM-killed, auto-restarted, restarted < 15 min ago |
| Worker stability | `docker logs --since 10m` | uvicorn `Child process died` respawn loops (the outage's symptom; Docker still said *healthy*), tracebacks, 5xx |
| Service API (direct) | host → each service's port, `/health` plus auth `/ready` (Postgres) | process up but hung (timeout) or slow (> 2 s) |
| Gateway | host → nginx `:8080` → service → datastore, same `/api/*` routes the browser uses | nginx can't reach a service (502), service bug (500), datastore down. Each path makes the service query its store |
| Service → dependency | [`container_probe.py`](probes/container_probe.py) run **inside** each service container via `docker exec`, using the container's own DNS, env and credentials | wrong host or port in env, wrong scheme, bad credentials, network split. Each dependency gets a real protocol request: Postgres authenticated `SELECT 1`, Redis `PING`, Kafka `ApiVersions`, ClickHouse authenticated `SELECT 1`, OpenSearch `_cluster/health`, MinIO `/minio/health/live`, auth JWKS (the call repository and platform make to verify every JWT) |
| Datastores | native checks: `pg_isready`, redis-cli, ClickHouse `/ping`, OpenSearch TLS, Neo4j HTTP and bolt handshake, Kafka, ZooKeeper | the store itself down, independent of any service |
| Public | `https://app.ereuna.org` (Cloudflare tunnel → nginx → services) | tunnel or DNS problems, things users actually see |

Which dependencies each service is tested against comes from each service's `config/settings.py`
(`APP_CONTAINERS` in [`host_probe.py`](probes/host_probe.py)):
auth → postgres, redis, kafka · repository → postgres, kafka, opensearch, minio, auth-jwks ·
datasets → postgres, kafka, clickhouse, opensearch, minio · platform → postgres, auth-jwks · inference → none (stub).

## Alerting rules
- **ALERT:** a *critical* check fails on **2 consecutive runs** (10 min). A single blip never emails.
- **REMINDER:** every 6 h while it stays broken. **RECOVERED:** once everything that was alerted on passes again.
- Warnings (slow responses, recent restarts, pressure) never page. They appear in alerts and the daily report.
- **Acknowledging a known issue:** put its `Group|Check name` (as shown in the email) on a line in
  `/etc/aether-health/acknowledged.txt` on pve. It is then shown as a warning and stops paging. Remove the line once it's fixed.

## Design notes and tradeoffs
- **Why not just Docker healthchecks?** During the 2026-11-22 outage every container stayed *healthy*
  while every API timed out. The healthcheck runs inside the container and tolerates 3 × 30 s failures. A check
  from outside, through the real request path, is the only reliable signal.
- **Why run probes inside the containers?** "Can service A reach B" depends on A's network namespace, DNS
  aliases, env vars and credentials. Testing from the host would pass while the service itself fails (the
  OpenSearch scheme mismatch and the unresolvable `minio` host below are both invisible from the host).
- **Runs on pve, not on aeacus:** a monitor on the box it monitors goes silent exactly when that box is starved.
  pve also sees the layer underneath (host RAM and swap) that aeacus can't.
- **Cost:** one SSH session and four `docker exec` Python starts every 5 min, run under `nice`. A run takes about 60 s on a
  healthy host. Under starvation each step has its own timeout and the run is capped at 7 min.
- **Standard library only**, no agents to install. At larger scale (several hosts, many services, SLOs) the standard
  tools are Prometheus + blackbox_exporter + Alertmanager. The check list here maps 1:1 onto blackbox HTTP and TCP modules
  plus Alertmanager's `for: 10m` and `repeat_interval: 6h`.
- **No authenticated end-to-end flow yet** (login → JWT → call repository with it). It needs a dedicated
  monitoring account in production. The JWKS check covers the inter-service half of it.

## Install / operate
```sh
./install.sh                                                                   # copy to pve + enable both timers
ssh root@100.124.198.112 'python3 /opt/aether-health/aether_health.py --dry-run'   # print the report, don't send
ssh root@100.124.198.112 'systemctl start aether-health-report.service'           # email a full report now
ssh root@100.124.198.112 'journalctl -u aether-health -n 20'                       # what recent runs decided
python3.11 -m unittest test_aether_health.py                                       # 14 tests: state machine, rendering, probe protocols
```
State: `/var/lib/aether-health/state.json`. To reset alerting, delete it.

## Known issues found on the first runs (2026-11-22/23)
- **Outage 01:30–02:20 EAT:** pve ran out of RAM (VMs get 44.9 GB, host has 40 GB) and swapped ~2.7 GB of
  aeacus-data-handler's memory. The VM stalled, and uvicorn's 5 s worker heartbeat failed in auth, repository and platform
  (`--workers 2`). Workers were killed and respawned every ~5 s, so load hit 285 on 2 vCPUs. Containers were restarted.
  Restarting gave services new IPs, and nginx kept the old ones until `nginx -s reload`. The underlying RAM shortage is not fixed.
- **datasets list returns 500:** the image expects column `dataset.datasets.dataset_type`, but the production DB lacks it (missing migration).
- **repository/datasets → OpenSearch broken:** OpenSearch serves HTTPS only, but the services use `OPENSEARCH_USE_SSL=false` or have it unset.
  Repository search still returns 200, which suggests it swallows the error.
- **datasets → MinIO broken:** `MINIO_ENDPOINT=minio:9000` (compose default); there is no `minio` host. Repository uses `s3.ereuna.org`.
- Neo4j HTTP :7474 doesn't answer (bolt works). No service uses Neo4j yet, so this is a warning only.
