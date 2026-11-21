#!/usr/bin/env bash
# One-time install of the ollama GPU guard (see ollama-gpu-guard.sh for the why).
# Usage (on aeacus-dl, from this directory):  sudo ./install-gpu-guard.sh
set -euo pipefail
cd "$(dirname "$0")"
install -m 0755 ollama-gpu-guard.sh /usr/local/sbin/ollama-gpu-guard.sh
install -m 0644 systemd/ollama-gpu-guard.service /etc/systemd/system/ollama-gpu-guard.service
install -m 0644 systemd/ollama-gpu-guard.timer   /etc/systemd/system/ollama-gpu-guard.timer
# daemon-reload is safe here: Docker uses the cgroupfs driver (host-gpu-prep.sh), so it does
# NOT strip /dev/nvidia* from running containers the way the systemd driver does.
systemctl daemon-reload
systemctl enable --now ollama-gpu-guard.timer
systemctl list-timers ollama-gpu-guard.timer --no-pager
