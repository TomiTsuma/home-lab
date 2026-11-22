# Daily homelab status email (2026-11-22)

**Request:** a morning email reporting on the homelab (services, internet speed, CPU usage, etc.), sent through the new mail relay.

## What was built
- `homelab/monitoring/daily-report/`: `homelab_report.py` (standard library only), `homelab-report.service` + `.timer` (07:00 EAT,
  `Persistent=true`), `install.sh`, `test_homelab_report.py` (9 unit tests, passing), `README.md`.
- Runs on the **Proxmox host** (always on, root, local `pvesh`). TrueNAS is read via `qm guest exec 102` (root, no stored credentials), the
  three Linux hosts over SSH on the LAN, and speed and endpoint tests run on TrueNAS.
- Mail goes to `192.168.100.10:587` (mail-relay), from `homelab@ereuna.org` to `tomitsuma@ereuna.org`, as HTML with a plain-text fallback.

## Access changes
- Added pve's root public key to `~/.ssh/authorized_keys` of `aeacus@aeacus-data-handler` (192.168.100.9), `aeacus@aeacus-dl` (192.168.100.17)
  and `minos@minos` (192.168.100.12). Host keys were accepted into pve's `known_hosts`. pve root already controls VMs 100-102 through Proxmox,
  so this adds little new exposure for those. minos is not a pve guest, so for minos this is new access.

## Problems hit while building
- pve can't resolve public names. `/etc/resolv.conf` is managed by Tailscale (100.100.100.100), and that doesn't forward public lookups.
  Not changed (needs owner OK). Worked around by running the internet and endpoint checks on TrueNAS. The report flags it daily.
- `df` parsing broke on a minos mount path containing spaces (`rsplit` fixes it). A mounted Ubuntu ISO showed as a 100% full disk
  (`iso9660`/`udf` are now excluded).
- `app.query`'s `upgrade_available` looked true for every app. The list now comes from TrueNAS's "update is available" alerts, which
  also name 25 apps, so it is accurate.
- Speed varies a lot between runs (download 2.2 / 26 / 5.4 Mbit/s today). The report shows the 7-day median for context.

## Verified
- Dry runs on pve: all 6 sections collect (~80 s per run).
- First real run via systemd at 13:09: `sent to tomitsuma@ereuna.org: 2 critical, 8 warnings`. The relay log shows `status=sent (250 …)` from Brevo.
- Timer next fires at `Mon 2026-11-23 07:00 EAT`.

## First report's findings (not fixed, for the owner)
pve memory 99% and public DNS broken; logs.ereuna.org 502; storage-tank 93%; TrueNAS SMB ACL critical alert; 25 app updates;
failed units on data-handler (prometheus snap, vault-backup), aeacus-dl (journal-flush) and minos (medusa, radarr); aeacus-dl memory 93%.
