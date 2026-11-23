#!/usr/bin/env bash
# Install/update Aether health alerts on the Proxmox host.
# Usage (from this folder on the laptop): ./install.sh [root@100.124.198.112]
set -euo pipefail
cd "$(dirname "$0")"
PVE="${1:-root@100.124.198.112}"

ssh "$PVE" 'mkdir -p /opt/aether-health/probes /var/lib/aether-health /etc/aether-health &&
            touch /etc/aether-health/acknowledged.txt'
scp -q aether_health.py "$PVE:/opt/aether-health/"
scp -q probes/host_probe.py probes/container_probe.py "$PVE:/opt/aether-health/probes/"
scp -q aether-health.service aether-health.timer aether-health-report.service "$PVE:/etc/systemd/system/"
# No daily report timer (owner, 2026-11-23: email only when something is unreachable, max once a day).
# aether-health-report.service stays for a manual full report: systemctl start aether-health-report
ssh "$PVE" 'systemctl disable --now aether-health-report.timer 2>/dev/null; rm -f /etc/systemd/system/aether-health-report.timer
            systemctl daemon-reload &&
            systemctl enable --now aether-health.timer &&
            systemctl list-timers "aether-health*" --no-pager'
