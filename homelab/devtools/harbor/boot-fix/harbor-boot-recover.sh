#!/usr/bin/env bash
set -uo pipefail
cd /opt/harbor
log() { echo "[harbor-boot-recover] $*"; }

log "docker compose up -d (initial)"
docker compose up -d

log "waiting for harbor-db readiness (up to 5 min)..."
db_ready=false
for i in $(seq 1 60); do
  if docker exec harbor-db pg_isready -q >/dev/null 2>&1; then
    db_ready=true
    log "harbor-db ready after $((i*5))s"
    break
  fi
  sleep 5
done
[ "$db_ready" = true ] || log "WARNING: harbor-db not confirmed ready after 5 min, continuing anyway"

log "re-applying docker compose up -d to recover anything that raced ahead of the DB"
for attempt in 1 2 3; do
  docker compose up -d
  sleep 10
  exited=$(docker ps -a --filter "label=com.docker.compose.project=harbor" --filter "status=exited" --format '{{.Names}}')
  if [ -z "$exited" ]; then
    log "all harbor containers running after attempt $attempt"
    break
  fi
  log "attempt $attempt: still exited: $exited -- retrying"
done

log "final state:"
docker compose ps
