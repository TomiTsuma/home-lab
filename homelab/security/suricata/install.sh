#!/usr/bin/env bash
# Install/update Suricata (IDS, detection only) on the Proxmox host.
# Usage (from this folder on the laptop): ./install.sh [root@100.124.198.112]
set -euo pipefail
cd "$(dirname "$0")"
PVE="${1:-root@100.124.198.112}"

ssh "$PVE" 'DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends suricata suricata-update jq'
scp -q homelab.yaml disable.conf update.yaml "$PVE:/etc/suricata/"
ssh "$PVE" 'mkdir -p /etc/systemd/system/suricata.service.d'
scp -q suricata.override.conf "$PVE:/etc/systemd/system/suricata.service.d/override.conf"
scp -q logrotate-suricata "$PVE:/etc/logrotate.d/suricata"
scp -q suricata-update.service suricata-update.timer "$PVE:/etc/systemd/system/"
scp -q suricata-update-homelab "$PVE:/usr/local/sbin/" && ssh "$PVE" chmod 755 /usr/local/sbin/suricata-update-homelab
ssh "$PVE" 'grep -q "^include: homelab.yaml" /etc/suricata/suricata.yaml || echo "include: homelab.yaml" >> /etc/suricata/suricata.yaml
            /usr/local/sbin/suricata-update-homelab --no-test -q
            systemd-run --wait --pipe -q -p MemoryMax=800M -p MemorySwapMax=0 suricata -T -c /etc/suricata/suricata.yaml
            systemctl daemon-reload
            systemctl enable --now suricata suricata-update.timer
            systemctl restart suricata
            sleep 20; systemctl --no-pager status suricata | head -12'
