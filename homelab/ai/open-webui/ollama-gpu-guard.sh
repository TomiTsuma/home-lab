#!/usr/bin/env bash
# ollama-gpu-guard.sh — make sure the `ollama` container is actually running on the GPU.
#
# WHY THIS EXISTS (2026-11-21 incident, see docs/changelog/211126-*_ollama_gpu_guard_and_web_search_changelog.md):
#   Ollama probes for GPUs once, at container start, by spawning llama-server against each
#   backend dir (vulkan, cuda_v12, cuda_v13) with a hard 30s watchdog per probe. Loading the
#   CUDA backend means reading ~800 MB of shared libraries (libggml-cuda.so alone is 250 MB).
#   aeacus-dl's disk is a virtual disk on a single shared 5400/7200rpm HDD on the Proxmox host
#   (same spindle as VM100 + TrueNAS boot), measured at 17-42 MB/s with io PSI "full" ~75%.
#   So the probe regularly times out -> Ollama silently falls back to `library=cpu` and stays
#   there until restarted. No error is surfaced in Open WebUI; models are just ~10x slower.
#
# WHAT IT DOES:
#   1. Reads the newest `msg="inference compute"` line from the ollama container's log.
#   2. If it says library=cpu (and the container has been up long enough for discovery to
#      have finished), it pre-reads the CUDA libs into the page cache (so the next probe reads
#      from RAM, ~0s instead of ~90s) and restarts the container, then re-checks. Up to 3 tries.
#
# Runs from ollama-gpu-guard.timer (3 min after boot, then every 15 min). Safe to run by hand:
#   sudo ./ollama-gpu-guard.sh
set -euo pipefail

CONTAINER="${CONTAINER:-ollama}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
DISCOVERY_GRACE_S="${DISCOVERY_GRACE_S:-240}"   # don't judge a container younger than this
LIBS_GLOB='/usr/lib/ollama/cuda_v13/* /usr/lib/ollama/*.so* /usr/lib/ollama/llama-server'

log() { echo "[ollama-gpu-guard] $*"; }

# Note: `docker logs` was observed returning truncated output on this host's json log (binary
# bytes from llama-server progress output), so read the json log file directly with grep -a.
last_compute_line() {
    local logpath
    logpath=$(docker inspect -f '{{.LogPath}}' "$CONTAINER")
    tail -c 20000000 "$logpath" | grep -a 'msg=\\"inference compute\\"' | tail -1 || true
}

uptime_s() {
    local started
    started=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")
    echo $(( $(date +%s) - $(date -d "$started" +%s) ))
}

wait_for_discovery() {
    # After a restart, wait until a new "inference compute" line appears (or give up).
    local before="$1" i line
    for i in $(seq 1 100); do
        line=$(last_compute_line)
        if [ -n "$line" ] && [ "$line" != "$before" ]; then echo "$line"; return 0; fi
        sleep 3
    done
    return 1
}

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    log "container '$CONTAINER' not found; nothing to do"; exit 0
fi

line=$(last_compute_line)
if echo "$line" | grep -q 'library=CUDA'; then
    log "OK: ollama is on CUDA"; exit 0
fi
if [ "$(uptime_s)" -lt "$DISCOVERY_GRACE_S" ]; then
    log "container up < ${DISCOVERY_GRACE_S}s, discovery may still be running; skipping this round"; exit 0
fi

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
    log "ollama is on CPU (attempt $attempt/$MAX_ATTEMPTS): pre-warming CUDA libs into page cache"
    start=$(date +%s)
    docker exec "$CONTAINER" sh -c "cat $LIBS_GLOB > /dev/null" || true
    log "pre-warm took $(( $(date +%s) - start ))s; restarting $CONTAINER"
    before=$(last_compute_line)
    docker restart "$CONTAINER" >/dev/null
    if line=$(wait_for_discovery "$before") && echo "$line" | grep -q 'library=CUDA'; then
        log "OK: ollama is on CUDA after restart"; exit 0
    fi
done

log "FAILED: ollama still not on CUDA after $MAX_ATTEMPTS attempts — check 'nvidia-smi' and the container log"
exit 1
