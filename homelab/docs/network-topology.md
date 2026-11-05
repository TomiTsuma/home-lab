# Network Topology

Captured live 2026-11-05. Overview only — follow the links for exact configs.

## LAN

- Router/gateway: Huawei Home Gateway, `192.168.100.1` (web UI :80, telnet :23
  — only reachable from inside the LAN).
- Proxmox host `pve`: `192.168.100.145/24`, single bridge `vmbr0` on the
  onboard NIC, no VLANs. See `../infrastructure/proxmox/REBUILD.md`.
- TrueNAS (VM 102): `192.168.100.10/24` (+ Tailscale `100.100.133.10`).
- No other static LAN IPs confirmed for VMs 100/101 at audit time (reachable
  via Tailscale, see below) — `minos` is a separate host, **not** a Proxmox
  guest on `pve` (its hosting substrate is unclear — flagged for the owner to
  clarify).

## Overlay / mesh networks — four separate ones, not unified

| Network | Tech | Hosts seen on it | Notes |
|---|---|---|---|
| Tailscale | mesh VPN | pve, TrueNAS (as an App), aeacus-data-handler, aeacus-dl, minos | Primary remote-access mesh; used for SSH to everything above except direct LAN hops. |
| `wgbak` (10.77.0.0/24) | WireGuard | TrueNAS (hub), minos (peer) | Backup mesh. See `../networking/wireguard/REAL-TOPOLOGY.md`. |
| `wgaz` (10.78.0.0/24) | WireGuard | Azure `wg-hub` (102.133.162.204, hub), minos (peer) | See same doc — Azure hub was unreachable by SSH during this audit. |
| NetBird | mesh VPN | aeacus-data-handler | Found running natively during the 2026-11-05 audit; not previously documented anywhere. Purpose/scope unconfirmed — ask the owner before assuming it's load-bearing. |

## Reverse proxy / ingress

- `nginx-proxy-manager` App on TrueNAS (ports 30020/21/22) — fronts the
  TrueNAS-hosted services with real TLS certs (its own `/etc/letsencrypt`,
  invisible to TrueNAS's own certificate store).
- `homelab/networking/traefik/` — a separate single-entrypoint Traefik
  compose exists in this repo; confirm with the owner which host it's
  actually meant to run on (not confirmed running during the live audit).
- Two independent Cloudflare Tunnel clients: one on `minos`, one on
  `aeacus-data-handler` (`ereuna-cloudflared`) — separate tunnels, not shared.
- Pi-hole (DNS ad-blocking) runs on `minos`.

## Per-host service summary

- **pve**: hypervisor only, no services of its own.
- **TrueNAS (VM 102)**: 25 Apps (media, Paperless-ngx, Nextcloud, Immich,
  Gitea, Portainer, Uptime-Kuma, nginx-proxy-manager, Syncthing, the *arr
  stack, qBittorrent...) — see `../infrastructure/truenas/README.md`.
- **aeacus-data-handler (VM 100)**: Harbor registry, the "Ereuna/Aether"
  microservice platform (Postgres, Kafka, ClickHouse, Neo4j, OpenSearch,
  Redis, MinIO), "Lyre" app + GlitchTip, native Vault + MinIO, NetBird,
  MicroK8s, the real Nextcloud *desktop-sync* server (snap), Gotify, a
  Cloudflare Tunnel.
- **aeacus-dl (VM 101)**: GPU box — MLflow, Ollama, Open WebUI, code-server.
- **minos**: Jellyfin + Plex (check for redundancy), its own Sonarr/Radarr/
  Prowlarr/qBittorrent instances (separate from TrueNAS's), Navidrome,
  Kavita, a custom "Chlio" app, Pi-hole, Cloudflare Tunnel, WireGuard peer on
  both meshes.

## Known open questions (ask the owner)

- `minos`'s hosting substrate (bare metal? separate hypervisor?).
- Whether minos's Sonarr/Radarr/Prowlarr/Navidrome duplicate the TrueNAS Apps
  of the same name intentionally, or are legacy/leftover.
- Whether NetBird on `aeacus-data-handler` is active infrastructure or a
  leftover experiment.
- Whether Plex (on minos) is still wanted alongside Jellyfin.
