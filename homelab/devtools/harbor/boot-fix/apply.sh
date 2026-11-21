#!/usr/bin/env bash
# Installs the Harbor logging override and recreates the Harbor containers.
# Run as:  sudo bash ~/harbor-fix/apply.sh
set -euo pipefail
SRC="$(dirname "$(readlink -f "$0")")/docker-compose.override.yml"
DST=/opt/harbor/docker-compose.override.yml

cd /opt/harbor
cp -a docker-compose.yml "docker-compose.yml.bak-$(date +%Y%m%d-%H%M%S)"
install -o root -g root -m 0644 "$SRC" "$DST"
echo "[apply] installed $DST"

docker compose config -q
echo "[apply] merged compose config is valid"

# Recreates only the 8 services whose logging config changed (harbor-log is untouched).
docker compose up -d

echo "[apply] waiting 60s for health checks..."
sleep 60
docker compose ps
echo "[apply] log driver per container:"
for c in registry registryctl harbor-db harbor-core harbor-portal harbor-jobservice redis nginx; do
  printf "  %-18s %s\n" "$c" "$(docker inspect -f '{{.HostConfig.LogConfig.Type}}' "$c")"
done
