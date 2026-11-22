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
scp -q aether-health.service aether-health.timer aether-health-report.service aether-health-report.timer \
    "$PVE:/etc/systemd/system/"
ssh "$PVE" 'systemctl daemon-reload &&
            systemctl enable --now aether-health.timer aether-health-report.timer &&
            systemctl list-timers "aether-health*" --no-pager'
