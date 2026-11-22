# Daily homelab report

Every morning at **07:00 EAT**, the Proxmox host emails a status report to `tomitsuma@ereuna.org` through the
TrueNAS [mail-relay](../../messaging/mail-relay/README.md). The subject line carries the verdict, for example
`⚠️ Homelab Mon 23 Nov: 2 critical, 8 warnings`. The body lists the problems first, then every check by host.

## What it checks
| Section | Source | Checks |
|---|---|---|
| Proxmox host | local `pvesh`, `/proc` | uptime, load, CPU 24h avg/peak (RRD), memory, storage (`local`, `local-lvm`), each VM's state and RAM, public DNS |
| Internet & public services | run **on TrueNAS** | download/upload via `speed.cloudflare.com` (10 MB each), latency to 1.1.1.1, public IP, HTTP check of the `*.ereuna.org` endpoints (< 500 counts as up), 7-day median speed |
| TrueNAS | `qm guest exec 102` as root, `midclt` | pool health, pool space, apps not running (except the ones stopped on purpose), alerts (CRITICAL/ERROR → critical, WARNING → warning), newest `ix-apps@bk-*` backup snapshot age, mail-relay queue |
| aeacus-data-handler, aeacus-dl, minos | SSH (LAN) | uptime, load per core, memory, disks ≥ 2 GB, docker containers (unhealthy, restarting, or non-zero exit), failed systemd units, NVIDIA GPU |

Thresholds are constants at the top of `homelab_report.py`: disk 85/95%, memory 90%, load 1.5 per core, GPU 85 °C, backup 36 h.
Speed tests and endpoint checks run on TrueNAS because pve can't resolve public DNS names (see Known issues).

## Design notes
- Python standard library only, so there's nothing to install on the Proxmox host. One file, one systemd oneshot service, one timer (`Persistent=true`,
  so a missed 07:00 run happens at the next boot).
- Each collector is isolated. An unreachable host becomes a `❌ Reachable: no` / `Collector failed` row, not a missing email.
- Access: pve's root key (`/root/.ssh/id_rsa`) is authorized for `aeacus@aeacus-data-handler`, `aeacus@aeacus-dl` and `minos@minos`
  (added 2026-11-22). TrueNAS is read through the QEMU guest agent, so no TrueNAS credentials are stored.
- Speed history is kept in `/var/lib/homelab-report/history.jsonl` on pve (one line a day).
- A single cron-style job is enough for a homelab. If this grows into alerting on thresholds, the better tool is Prometheus + Alertmanager
  (a Prometheus snap already exists on aeacus-data-handler, currently failed) or Uptime Kuma (already on TrueNAS) for endpoint checks.

## Install / update
```sh
./install.sh                       # copies to pve:/opt/homelab-report, installs + enables the timer
ssh root@100.124.198.112 'python3 /opt/homelab-report/homelab_report.py --dry-run'   # print, don't send
ssh root@100.124.198.112 'systemctl start homelab-report.service'                    # send one now
python3 -m unittest test_homelab_report.py                                             # unit tests (pure functions)
```
Change the recipient with `REPORT_TO=` (an `Environment=` line in the service), and add hosts or URLs in `SSH_HOSTS` / `ENDPOINTS`.

## Known issues found by the first report (2026-11-22)
- pve: memory 99% used (VMs are allocated 44 GB against 39 GB physical), and public DNS fails because `resolv.conf` is Tailscale-only.
- logs.ereuna.org 502. The aeacus tunnel still points at the stopped GlitchTip; it now runs on minos.
- TrueNAS: storage-tank 93% full; SMB "ACL type mismatch" critical alert on `data-store`; 25 apps have updates.
- Failed units: aeacus-data-handler `snap.prometheus.prometheus`, `vault-backup`; aeacus-dl `systemd-journal-flush`;
  minos `medusa@minos`, `radarr`.
- aeacus-dl memory 93% (Ollama/GPU processes; RTX 3060 has 8.3 of 12 GB VRAM in use while idle).
