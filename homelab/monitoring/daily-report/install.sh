#!/usr/bin/env bash
# Install/update the daily homelab report on the Proxmox host.
# Usage (from this folder on the laptop): ./install.sh [root@100.124.198.112]
set -euo pipefail
cd "$(dirname "$0")"
PVE="${1:-root@100.124.198.112}"

ssh "$PVE" 'mkdir -p /opt/homelab-report /var/lib/homelab-report'
scp -q homelab_report.py "$PVE:/opt/homelab-report/homelab_report.py"
scp -q homelab-report.service homelab-report.timer "$PVE:/etc/systemd/system/"
ssh "$PVE" 'chmod 755 /opt/homelab-report/homelab_report.py &&
            systemctl daemon-reload &&
            systemctl enable --now homelab-report.timer &&
            systemctl list-timers homelab-report.timer --no-pager'
