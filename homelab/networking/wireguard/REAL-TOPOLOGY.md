# Actual WireGuard topology (as of 2026-11-05)

`install.sh` in this folder is a **generic linuxserver/wireguard reference
script** (single server, dummy 10.13.13.0/24 subnet) — it does **not** match
what's actually deployed. This file documents the real topology so a rebuild
targets the right thing.

There are two separate WireGuard meshes:

## `wgbak` — backup/home mesh, 10.77.0.0/24

- **Hub: TrueNAS** (VM 102 `aeacus-backup`). Confirmed live (`wgbak` kernel
  interface UP on TrueNAS), configured by hand via `/etc/wireguard/wgbak.conf`
  + `wg-quick` — **not** a TrueNAS App (the `wg-easy` App exists but is
  STOPPED, so it isn't the active mechanism). Config/keys likely live under
  the root-only `storage-tank/wireguard` ZFS dataset.
- **Peer: `minos`** — confirmed live, interface IP `10.77.0.4/24`.
- Router port-forward for UDP 51820 to the hub was still pending as of the
  last homelab memory check — confirm it's done before relying on this mesh
  from outside the LAN.

## `wgaz` — Azure mesh, 10.78.0.0/24

- **Hub: Azure VM "wg-hub"**, public IP `102.133.162.204`. **Not reachable by
  SSH during the 2026-11-05 audit** (connection timed out on port 22) — check
  the Azure NSG allows SSH from your current IP, or that sshd is listening,
  before assuming this box is down. Its WireGuard config could not be
  inspected this round.
- **Peer: `minos`** — confirmed live, interface IP `10.78.0.4/24`. This was
  not previously documented anywhere (prior notes only described the Azure
  hub, not which hosts peer with it).

## What's still unverified

- Full peer lists and public keys for both meshes — both ends need root/sudo
  (`wg show`) to export, not available during this audit.
- Whether `minos` is meant to be a long-term dual-mesh bridge, or whether
  this is incidental/leftover config.
- Whether any other host (e.g. `aeacus-data-handler`, which separately runs
  **NetBird** — a different mesh VPN — as of this audit) also peers on either
  mesh.

## Recreating from scratch

1. Stand up the Azure `wg-hub` VM (if also lost) and TrueNAS (`wgbak` hub)
   first — see `../../infrastructure/truenas/README.md`.
2. On each hub, `wg genkey` a fresh keypair (don't reuse old private keys),
   configure `/etc/wireguard/<iface>.conf` with the subnet above, bring up
   with `wg-quick up <iface>`, enable at boot (`systemctl enable wg-quick@<iface>`).
3. On each peer (confirmed: `minos` on both), generate a peer keypair, add it
   to the hub's `[Peer]` config with an allowed IP in the mesh's /24, and
   configure the peer's own `wgbak.conf`/`wgaz.conf` pointing at the hub's
   public endpoint + public key.
4. Forward UDP 51820 (or whatever port each hub uses — not confirmed to be
   the same port on both) on the router/firewall in front of each hub.
