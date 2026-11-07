#!/bin/bash
# One-time host prep for running the ollama/litellm/open-webui stack in this directory
# with working GPU passthrough. Run on the VM that has the GPU (aeacus-dl), as a user
# with sudo, BEFORE `docker compose up -d`. Safe to re-run.
#
# Fixes two gotchas discovered the hard way (see ../../docs/changelog/
# 071126-091000_ollama_gpu_and_claude_integration_changelog.md for the full story):
#
#   1. NVIDIA persistence mode defaults to OFF on Ubuntu (nvidia-persistenced ships with
#      --no-persistence-mode). Without it, CUDA context creation after a cold boot can
#      take long enough to blow past Ollama's internal ~30s GPU-discovery timeout, which
#      makes Ollama silently fall back to CPU-only inference with no obvious error.
#
#   2. If Docker uses the "systemd" cgroup driver (the default on recent Ubuntu with
#      cgroups v2), ANY `systemctl daemon-reload` rewrites the running containers'
#      device cgroup allowlist and REVOKES their access to /dev/nvidia* — the container
#      keeps running but nvidia-smi inside it starts failing with
#      "Failed to initialize NVML: Unknown Error" until it's recreated. Switching to the
#      "cgroupfs" driver stops systemd from doing this.
set -euo pipefail

echo "==> Enabling NVIDIA persistence mode at boot"
sudo mkdir -p /etc/systemd/system/nvidia-persistenced.service.d
sudo tee /etc/systemd/system/nvidia-persistenced.service.d/override.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/nvidia-persistenced --user nvidia-persistenced --verbose
EOF
sudo systemctl daemon-reload
sudo systemctl restart nvidia-persistenced.service
sudo nvidia-smi -pm 1 >/dev/null
echo "    $(nvidia-smi -q | grep 'Persistence Mode')"

echo "==> Checking Docker cgroup driver"
CURRENT_DRIVER=$(docker info 2>/dev/null | grep -i 'Cgroup Driver' | awk '{print $3}')
if [ "$CURRENT_DRIVER" = "systemd" ]; then
  echo "    Docker is on the 'systemd' cgroup driver — switching to 'cgroupfs'."
  [ -f /etc/docker/daemon.json ] && sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.bak
  sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
    "exec-opts": ["native.cgroupdriver=cgroupfs"],
    "runtimes": {
        "nvidia": {
            "args": [],
            "path": "nvidia-container-runtime"
        }
    }
}
EOF
  sudo systemctl restart docker
  sleep 5
  echo "    Docker restarted. Any running containers lost their GPU binding and must be"
  echo "    recreated: (cd here && docker compose up -d --force-recreate)"
else
  echo "    Already on '$CURRENT_DRIVER' — no change needed."
fi

echo "==> Done. Bring up the stack with: docker compose up -d"
