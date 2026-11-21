# Harbor boot-race fix (aeacus-data-handler, `/opt/harbor`)

## Symptom
After a reboot of `aeacus-data-handler` (VM 100), Harbor (`:5000`) is down. All
containers except `harbor-log` are in `Exited`/`Created` state.

## Root cause
Upstream Harbor's generated `docker-compose.yml` sends every service's logs to
the `harbor-log` container through Docker's `syslog` logging driver
(`tcp://localhost:1514`). Docker opens that connection when it **creates the
container task**. At boot, dockerd restores every `restart: always` container
in parallel, so any Harbor service that starts before `harbor-log` is
listening fails with:

```
failed to create task for container: failed to initialize logging driver:
dial tcp 127.0.0.1:1514: connect: connection refused
```

Docker's restart policy covers containers that *exit*, not ones that *fail to
start*, so those containers stay down.

## Fixes (layered)

| File | Installed at | Role |
|---|---|---|
| `docker-compose.override.yml` | `/opt/harbor/docker-compose.override.yml` | **Primary fix (2026-11-17).** Switches all 8 services to `json-file` (20 MB x 5 rotation), removing the dependency on `harbor-log` at start. `docker compose` auto-loads it, and `./prepare` / `./install.sh` don't touch it, so it survives Harbor upgrades. |
| `harbor-boot-recover.sh` + `.service` | `/usr/local/sbin/`, `/etc/systemd/system/` | **Safety net (2026-10-29).** Oneshot that re-runs `docker compose up -d` after docker starts and waits for `harbor-db`. Kept, but it only runs once `docker.service` is ready, which takes about 15 min on this 2-core VM. Without the override, Harbor was down for that whole window. |
| `apply.sh` | n/a | One-time installer for the override: `sudo bash ~/harbor-fix/apply.sh` |

## Tradeoff
`/var/log/harbor/*.log` (written by `harbor-log`) stops receiving new lines.
Use `docker logs <container>` instead (`harbor-core`, `nginx`, `registry`, ...).
`harbor-log` is left running and harmless. To restore central log files later,
ship Docker logs with an agent (Vector, Fluent Bit, Promtail) rather than going
back to the syslog driver.

## Verify
```bash
docker inspect -f '{{.Name}} {{.HostConfig.LogConfig.Type}}' $(docker ps -aq --filter label=com.docker.compose.project=harbor)
# Simulate the race: Harbor must come up with harbor-log stopped
docker stop harbor-log && docker restart registry harbor-core nginx && docker ps --filter label=com.docker.compose.project=harbor
docker start harbor-log
```
