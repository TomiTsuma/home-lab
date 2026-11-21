# 2026-11-17: Harbor does not come back after aeacus-data-handler reboot

**Host:** aeacus-data-handler (VM 100), Harbor v2.14.1 at `/opt/harbor`, Docker 29.1.3 / Compose v2.40.3

## Diagnosis
- Docker journal for this boot: `failed to initialize logging driver: dial tcp 127.0.0.1:1514: connect: connection refused`. Harbor services use the syslog driver pointed at the `harbor-log` container, and lose the race against it when dockerd restores containers at boot. Docker doesn't retry start failures.
- Existing workaround `harbor-boot-recover.service` (installed 2026-10-29) has recovered Harbor on every boot since (checked journals for boots on 11-08, 11-10, 11-13, 11-15, 11-17). But `docker.service` takes about 15 min to become ready on this 2-core VM (02:51 → 03:05:53 today), so Harbor was down for roughly 17 min after every reboot. It would also stay down after any non-systemd docker restart.
- Unrelated, noted: `vault-unseal.service` has been stuck in `activating` for the whole boot, so `multi-user.target` never completes. `vault-backup.service` and `snap.prometheus` are failed.

## Change
- Added `/opt/harbor/docker-compose.override.yml`: every Harbor service now uses `json-file` logging. The source is in `homelab/devtools/harbor/boot-fix/`.
- Containers recreated with `docker compose up -d` (volumes and data untouched) via `sudo bash ~/harbor-fix/apply.sh`. **Status: staged on the host, waiting for the owner to run it (needs sudo password).**
- Kept the 10-29 recover unit as a safety net. It's now also vendored in the repo.

## Tradeoff
`/var/log/harbor/*.log` no longer receives new lines; use `docker logs`. See `boot-fix/README.md`.
