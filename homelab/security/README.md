# Homelab security monitoring

Autonomous, **detection-only** security monitoring for the whole homelab. Nothing is ever blocked automatically.

| When | What | Email |
|---|---|---|
| continuously | **Suricata** network IDS on pve watches the VM bridge (`vmbr0`) and Tailscale (`tailscale0`) with ~46k Emerging Threats Open rules, and logs every flow | — |
| hourly (:07) | high-risk IDS alerts (malware/C2/exploit/admin-compromise classes) from the last hour | 🚨 immediately, at most once per rule per 24 h |
| 01:30 | **Trivy** scans every running container image on aeacus-data-handler, minos and TrueNAS | — (feeds the evening report) |
| 04:30 | Suricata rule update (ET Open) | — |
| **19:00** | **Evening security report**: score, what needs attention, what changed since yesterday, coverage | 🛡️ daily |

## Components
```
homelab/security/
  suricata/        IDS config for pve: homelab.yaml (include), memory-capped systemd drop-in, rule updates, logrotate
  trivy-server/    TrueNAS custom app: vulnerability DB server (LAN-only, token)
  report/          security_report.py (runs on pve) + probes/host_audit.py + probes/truenas_audit.py + timers
```

### What the evening report checks
| Area | Source | Flags |
|---|---|---|
| **Intrusion** | Suricata alerts | attack-class rules (trojan, C2, exploit, web attack, admin/user compromise) high; other sev-1 medium; policy/info rules (P2P, STUN, user agents) low/info |
| | SSH journal on pve, aeacus-data-handler, aeacus-dl, minos | logins or failed attempts **from the internet**, ≥10 failures from one LAN/tailnet host, password logins, failed sudo, account changes, extra UID-0 accounts |
| | TrueNAS middleware audit log | failed logins, logins from a never-seen address |
| **Unusual traffic** | Suricata flow records | internet → LAN connections (port forwards/UPnP actually being used), per-host internet volume > 5× its 7-day median, outbound to uncommon ports, top destinations |
| **Exposure** | nmap from pve, `tailscale status`, `ss` | new LAN devices, new open ports, risky LAN services (telnet, RDP, VNC, unauth Redis/Mongo/Docker API), new tailnet devices, new listening sockets, database/admin ports published on all interfaces by docker |
| **Integrity** | sha256 baseline | changes to authorized_keys, sudoers, passwd/group/shadow, sshd config, cron, `ld.so.preload`, profile.d, new systemd units |
| **Vulnerabilities** | Trivy + apt | fixable critical/high CVEs per host and image, pending OS security updates, reboot required |
| **Hardening** | sshd config, docker inspect, TrueNAS config | password SSH, root login, privileged / host-network / docker.sock containers, SMB guest shares, open NFS exports, TrueNAS 2FA |

**Score:** 100 − (20 × critical + 8 × high + 3 × medium + 1 × low); A ≥ 90, B ≥ 80, C ≥ 65, D ≥ 50, F below.
Baselines (file hashes, ports, devices) are recorded on the first run. Every later report lists the differences and then accepts them as the new baseline,
so each change is reported once.

## Design decisions and tradeoffs
- **Detect only (owner's choice).** CrowdSec/fail2ban-style auto-blocking can lock you out if a rule misfires, so
  it's left off. Everything internet-facing goes through Cloudflare Tunnel or Tailscale, so there are few direct targets to defend.
- **Suricata on pve, as is (owner's choice).** pve is the only machine that sees all VM traffic. pve is short on RAM
  (VMs are allocated more than the host has), so Suricata is tuned small (`detect.profile: low`, small memcaps) and
  **hard-capped by systemd (`MemoryMax=800M`, no swap)**. If it grows, the kernel kills Suricata, not a VM, and systemd restarts it.
  Measured: ~550 MB with the full ruleset.
- **What Suricata can't see:** traffic of other LAN devices (phones, laptops, TVs) that doesn't touch the VMs, and
  anything inside Tailscale/WireGuard tunnels beyond endpoints. Seeing all of that needs a mirror port on a managed switch or the router.
- **Trivy client/server.** The vulnerability DB is downloaded once a day by trivy-server on TrueNAS. Hosts only send package lists.
  The Java DB (~600 MB) is skipped, so JAR-level CVEs are only partially covered.
- **No agents on hosts.** Everything is read over SSH (users are in `adm`, so journals are readable without sudo) or via
  the QEMU guest agent for TrueNAS. **aeacus-dl containers are not scanned:** its user isn't in the `docker` group and VM 101 has no guest agent.
  Fix either one and add it to `SCAN_HOSTS`.
- **Why not Wazuh / Security Onion?** They're the full-SIEM answer, but each needs 4–16 GB RAM for its indexer, which this hardware
  doesn't have. At larger scale the path is Suricata → EVE shipped to OpenSearch/Wazuh, plus CrowdSec agents with a central LAPI, plus Trivy Operator.

## Operating
```sh
cd suricata && ./install.sh          # IDS on pve (idempotent)
cd trivy-server && ./deploy.sh       # vulnerability DB server on TrueNAS (.env holds TRIVY_TOKEN)
cd report && ./install.sh            # report + timers on pve

# on pve (one time): who receives the email (kept out of the repo)
echo 'REPORT_TO=you@example.org' > /etc/homelab-security/report.env

ssh root@100.124.198.112 python3 /opt/homelab-security/security_report.py --dry-run   # print tonight's report now
ssh root@100.124.198.112 python3 /opt/homelab-security/security_report.py --dry-run --ids   # finding ids, for acks
ssh root@100.124.198.112 systemctl start homelab-security-scan       # run the vulnerability scan now
ssh root@100.124.198.112 'jq -c "select(.event_type==\"alert\")" /var/log/suricata/eve.json | tail'   # raw IDS alerts
python3.11 -m unittest report/test_security_report.py                  # 21 tests
```
**Silencing a known finding:** add its id (from `--ids`, e.g. `suricata-sid:2008583`, `exposed:aeacus-data-handler`,
`device:aa:bb:cc:dd:ee:ff`; a trailing `*` matches a prefix) to `/etc/homelab-security/acknowledged.txt` on pve. It's then shown as info.
**Noisy IDS rules:** add `re:`/`group:` lines to `suricata/disable.conf`, run `./install.sh`.
