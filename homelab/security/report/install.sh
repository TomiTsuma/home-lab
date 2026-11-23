#!/usr/bin/env bash
# Install/update the homelab security report on the Proxmox host.
# Usage (from this folder on the laptop): ./install.sh [root@100.124.198.112]
# Prereqs: ../suricata/install.sh done; trivy-server deployed (../trivy-server/deploy.sh) and its .env present.
set -euo pipefail
cd "$(dirname "$0")"
PVE="${1:-root@100.124.198.112}"
TOKEN=$(grep '^TRIVY_TOKEN=' ../trivy-server/.env | cut -d= -f2-)

ssh "$PVE" 'command -v nmap >/dev/null || apt-get install -y -qq nmap
            mkdir -p /opt/homelab-security/probes /var/lib/homelab-security /etc/homelab-security
            touch /etc/homelab-security/acknowledged.txt; chmod 700 /etc/homelab-security /var/lib/homelab-security'
printf '%s\n' "$TOKEN" | ssh "$PVE" 'umask 077; cat > /etc/homelab-security/trivy.token'
scp -q security_report.py "$PVE:/opt/homelab-security/"
scp -q probes/host_audit.py probes/truenas_audit.py "$PVE:/opt/homelab-security/probes/"
scp -q homelab-security-*.service homelab-security-*.timer "$PVE:/etc/systemd/system/"
ssh "$PVE" 'systemctl daemon-reload
            systemctl enable --now homelab-security-report.timer homelab-security-scan.timer homelab-security-urgent.timer
            [ -s /etc/homelab-security/report.env ] || echo "!! create /etc/homelab-security/report.env with REPORT_TO=<your address>"
            systemctl list-timers "homelab-security*" --no-pager'
