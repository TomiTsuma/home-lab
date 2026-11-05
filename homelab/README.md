# Homelab
This repository contains scripts, configs, and documentation for a complete homelab setup — the goal is that it's enough to recreate everything from nothing.

Start here:
- `docs/network-topology.md` — cross-host overview of what runs where.
- `infrastructure/proxmox/REBUILD.md` — rebuild the physical hypervisor and its 3 VMs.
- `infrastructure/truenas/README.md` — the TrueNAS VM (pools, 25 apps, cron scripts).
- `networking/wireguard/REAL-TOPOLOGY.md` — the two actual WireGuard meshes.
- `docs/changelog/` — dated history of what changed and why, newest first.

As of 2026-11-05 this covers the Proxmox host and TrueNAS fully; `minos`'s
service stack and the Ereuna/Lyre platform on `aeacus-data-handler` are
documented at a high level in `docs/network-topology.md` but don't have
per-service compose files yet — see the latest changelog entry's "Not done
yet" section.
