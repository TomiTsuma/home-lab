# ClickHouse MCP for Open WebUI, and why aeacus-data-handler was unreachable (2026-11-22)

## ClickHouse MCP (no ClickHouse target yet, by request)
- New `ai/open-webui/clickhouse-mcp/` (Dockerfile pinning `mcp-clickhouse==0.7.0`, `register_in_openwebui.py`) and compose service
  `clickhouse-mcp` on a new private network `chmcp` shared only with open-webui. Streamable HTTP on :8000, bearer token required,
  Host allow-list, read-only (`CLICKHOUSE_ALLOW_WRITE_ACCESS=false`), hardened container, no published port.
- Registered in Open WebUI as `clickhouse`; `server:mcp:clickhouse` default-on for all 9 models (3 tools, about 1k tokens).
- Token generated on aeacus-dl into the chmcp-600 `.env` (never in the repo); `.env.example` documents the variables.
- Tested through Open WebUI's network path: 401 without token, 421 for a wrong Host, 200 with token, tools listed, and an unconfigured
  call fails with a connection error to localhost:8123. **Not tested against a real ClickHouse** (none exists yet).
- Version note: PyPI 0.7.0 is behind the GitHub main README; env names were taken from the 0.7.0 wheel itself.
- Rollback: `docker compose rm -sf clickhouse-mcp`, remove the `clickhouse` connection in Open WebUI (Admin > External Tools) and
  `server:mcp:clickhouse` from model tool lists.

## aeacus-data-handler (VM 100) unreachable over Tailscale (fixed)
- Cause: a NetBird agent (installed Oct 19-23, hostname `*.brev.local`) gave interface `wt0` the address 100.112.111.95/10; that
  connected /10 route covers the whole 100.64.0.0/10 range Tailscale uses and is consulted before Tailscale's routing table
  (rule 105 `lookup main suppress_prefixlength 0`), so replies to Tailscale peers left via `wt0` and vanished. Tailscale's own disco
  pings still worked, hence "ping works, SSH times out" (same symptom on 2026-11-17).
- Fix: systemd unit `ts-over-netbird.service` on the VM adds `ip rule priority 100 to 100.64.0.0/10 lookup 52` at boot; NetBird peers
  (100.112.x.x) still route via `wt0`. NetBird itself was left alone (owner to decide whether it is wanted).
- VM 100 was hard-stopped/started at 03:19/03:22 (Proxmox clock) by root@pam; cause not established.
- Local address: 192.168.100.9 (ens18). Tailscale 100.127.65.29.

## Heads-up for the ClickHouse server that will run on VM 100
`clickhouse/clickhouse-server:latest` exits with "Illegal instruction" on VM 100: its vCPU is `x86-64-v2-AES` (no AVX/AVX2; the host
Xeon E3-1240 v5 has them). VM 102 already uses `cpu: host`. Fix needs a VM 100 restart (`qm set 100 --cpu host`), which briefly takes
down Ereuna/Harbor/MLflow on it; not done. A throwaway probe container was run and removed; the image `clickhouse/clickhouse-server:latest`
is still pulled on VM 100.

## Correction (2026-11-22, later)
The "Illegal instruction" crash was specific to the `clickhouse/clickhouse-server:latest` image. The owner's emergency instance runs
ClickHouse 24.10.2.80 on VM 100 without problems (HTTP 18123, native 19440), so no `cpu: host` change is needed for that version.
The MCP is now connected to it; see `221126-150000_dagster_mcp_changelog.md` (update section).
