# Homelab security monitoring: IDS, vulnerability scanning, evening report (2026-11-23)

**Request:** autonomous security checks that scan for vulnerabilities, flag unusual traffic and detect intrusions
across the whole homelab, with an evening email on where security stands.

**Owner decisions:**
- Suricata runs on pve "as is" (no RAM freed first).
- Detect and email only, never auto-block.
- Fix pve DNS, but don't install the pending updates.

## What was built (`homelab/security/`, see README.md)
- **Suricata 7.0.10 on pve**, IDS only.
  - Sniffs `vmbr0` and `tailscale0` with ET Open (~46.7k enabled rules) and logs EVE alerts and flows.
  - Memory: ~550 MB measured. systemd `MemoryMax=800M` with no swap, so an overrun kills Suricata, not a VM.
  - GRO is turned off on `enp0s31f6` (it was making packets uninspectable). Rules update daily at 04:30.
- **trivy-server** TrueNAS app (`aquasec/trivy:0.58.1`, port 4954 bound to the LAN IP, token in a gitignored `.env`).
  - Scanners run in client mode, so only TrueNAS downloads the vulnerability DB. The link is slow (~10 KB/s at times).
- **security_report.py** on pve, with three timers:
  - 19:00 evening report
  - 01:30 container CVE scan of aeacus-data-handler, minos and TrueNAS
  - hourly urgent alerts for attack-class IDS hits
  - Probes run over SSH and the QEMU agent; nothing is installed on the hosts.
  - The probes cover logins, sudo, account changes, file-integrity baselines, listening ports, updates, sshd and docker risks,
    TrueNAS audit/2FA/shares, nmap LAN inventory, tailnet inventory, and Suricata traffic analysis.
- Email recipient comes from `/etc/homelab-security/report.env` on pve (a privacy guard blocks putting the address in code).
- Tests: `report/test_security_report.py`, 21 passing (traffic heuristics, severity mapping, integrity, acks, score, escaping, Trivy summary).

## Changes made to systems
- **pve DNS fixed.** `/etc/resolv.pre-tailscale-backup.conf` was empty, so Tailscale had no upstream and SERVFAILed every
  public name. It now lists `192.168.100.1` and `1.1.1.1` (original saved as `/root/resolv.pre-tailscale-backup.conf.orig-empty`),
  and tailscaled was restarted. MagicDNS still works and `apt update` works again.
- pve: installed `suricata`, `suricata-update`, `jq`.
  - Added `/etc/suricata/homelab.yaml` (+ `include:` line), the systemd drop-in, logrotate,
    `/usr/local/sbin/suricata-update-homelab`, and `/opt/homelab-security`, `/etc/homelab-security`, `/var/lib/homelab-security`.
  - The wrapper exists because Debian's suricata-update 1.3.4 crashes on Python 3.13 when a rule file has non-UTF-8 bytes.
- TrueNAS: new custom app `trivy-server`.
- aeacus-data-handler and minos: pulled the `aquasec/trivy:0.58.1` image.

## Problems hit
- Debian's suricata-update crash (above). The first ET download was also truncated by the slow link: that cache file was corrupt
  and only 429 rules loaded until it was re-downloaded.
- My `pkill -f suricata-update` matched the remote shell running it, which killed the session and the installer. Re-ran the steps.
- An `include:` replaces whole YAML maps:
  - the `vars` override dropped `$HTTP_SERVERS` and the other variables, which disabled rules, so the full variable set is restated;
  - an `app-layer` override would have replaced every protocol setting, so it was removed.
- `suricata -q` means NFQUEUE id, not quiet. The first config test never ran.
- Inbound-UDP heuristic: replies to flows Suricata had already expired (cloudflared QUIC, WireGuard keepalives) looked like
  unsolicited inbound. Now UDP only counts when the internal port never sent outbound. Side effect: a forwarded torrent UDP
  port that also sends outbound (51413 on TrueNAS) isn't flagged.

## First report (dry run, 08:00): C, 71/100
- **No intrusion indicators.** All SSH, TrueNAS and Tailscale logins came from LAN/tailnet addresses. No failed logins. No attack-class IDS alerts.
- **High:** pve has 65 pending security updates.
- **Medium:**
  - aeacus-data-handler publishes 11 database/admin ports on all interfaces (Postgres, Redis, Kafka, ClickHouse, OpenSearch, Vault…).
  - aeacus-dl has 4 security updates pending.
  - The router (192.168.100.1) has telnet open on the LAN.
- **Low/info:**
  - pve allows root SSH (key-only).
  - TrueNAS UI 2FA is off.
  - TrueNAS user `aeacus` has SSH-password plus sudo (password login is disabled globally).
  - TrueNAS runs a BitTorrent client: P2P rules fire, and Spamhaus-listed IPs reach it on 51413.
  - aeacus-data-handler sends ~7k STUN requests a day to AWS hosts on UDP 51820. Worth identifying which container does this.
- **Not covered yet:** aeacus-dl container images, because its user isn't in the `docker` group and VM 101 has no guest agent.
