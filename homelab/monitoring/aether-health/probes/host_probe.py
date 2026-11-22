"""Runs on aeacus-data-handler (piped over SSH by aether_health.py: `ssh aeacus@... python3 -`).

aether_health.py prepends `CONTAINER_PROBE_SRC = '...'` (the source of container_probe.py) before sending.
Prints one JSON object: {"checks": [...], "meta": {...}}. Each check is
{"group", "name", "status": ok|warn|crit, "detail", "ms"}.

Layers tested, bottom-up, so a failure points at where the break is:
  1. Host       - CPU/IO pressure, load, memory on the VM itself.
  2. Containers - every ereuna_* container: running, Docker health, restarts, OOM kills, recent restart.
  3. Stability  - uvicorn "Child process died" (worker respawn loop) and tracebacks in the last N minutes.
  4. Direct API - host -> each service's own port: /health latency, auth /ready (touches Postgres).
  5. Gateway    - host -> nginx (ereuna_web) -> service -> its datastore, through the same /api/* routes
                  the browser uses. Each path is chosen so the service has to query its backing store.
  6. Service -> dependency - container_probe.py inside each service container (its own DNS, env, creds).
  7. Infra      - datastores checked natively (pg_isready, redis PING, ClickHouse /ping, OpenSearch health,
                  Neo4j bolt handshake, Kafka ApiVersions, ZooKeeper TCP).
  8. Public     - https://app.ereuna.org through Cloudflare (tunnel -> nginx -> services).
"""
import json
import os
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request

WINDOW_MIN = int(os.environ.get("AETHER_WINDOW_MIN", "10"))   # log-scan window
SLOW_MS = 2000                                                # API latency warn threshold
TIMEOUT = 8                                                   # per HTTP request (s)
PUBLIC_BASE = "https://app.ereuna.org"

APP_CONTAINERS = {
    # container: (host port, health path, dependencies it really uses — from each service's config/settings.py)
    "ereuna_auth_service": (8010, "/health", ["postgres", "redis", "kafka"]),
    "ereuna_repository_service": (8001, "/health", ["postgres", "kafka", "opensearch", "minio", "auth-jwks"]),
    "ereuna_datasets_service": (8002, "/health", ["postgres", "kafka", "clickhouse", "opensearch", "minio"]),
    "ereuna_platform_service": (8003, "/health", ["postgres", "auth-jwks"]),
    "ereuna_inference_service": (8004, "/health", []),   # stub service, no backing stores yet
}
INFRA_CONTAINERS = ["ereuna_web", "ereuna_postgres", "ereuna_redis", "ereuna_kafka", "ereuna_zookeeper",
                    "ereuna_clickhouse", "ereuna_opensearch", "ereuna_neo4j", "ereuna-cloudflared", "mlflow"]

# (name, path via nginx :8080, acceptable status codes, what a correct answer proves)
GATEWAY_ROUTES = [
    ("auth", "/api/auth/users/me", {401}, "nginx -> auth-service (unauthenticated request rejected by the app)"),
    ("repository: list", "/api/repository/repositories?limit=1", {200}, "nginx -> repository -> Postgres"),
    ("repository: search", "/api/repository/repositories/search?q=a&limit=1", {200}, "nginx -> repository -> OpenSearch"),
    ("datasets: list", "/api/datasets/datasets/?limit=1", {200}, "nginx -> datasets -> Postgres"),
    ("platform: experiments", "/api/platform/experiments?limit=1", {200}, "nginx -> platform -> Postgres"),
    ("platform: deployments", "/api/platform/deployments?limit=1", {200}, "nginx -> platform -> Postgres"),
    ("inference", "/api/inference/health", {200}, "nginx -> inference-service"),
]
PUBLIC_ROUTES = [
    ("web app", "/", {200}),
    ("repository API", "/api/repository/repositories?limit=1", {200}),
    ("inference API", "/api/inference/health", {200}),
]

checks = []


def add(group, name, status, detail, ms=None):
    checks.append({"group": group, "name": name, "status": status, "detail": str(detail)[:300], "ms": ms})


def sh(cmd, timeout=30, stdin=None):
    r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, input=stdin)
    return r.returncode, r.stdout, r.stderr


def http(url, timeout=TIMEOUT):
    t = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "aether-health"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, body = r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read(65536)
    return code, body, round((time.monotonic() - t) * 1000)


def is_json(body):
    try:
        json.loads(body)
        return True
    except ValueError:
        return False


def latency_status(ms):
    return "warn" if ms > SLOW_MS else "ok"


# ----------------------------------------------------------------------------- 1. host
def host_checks():
    ncpu = os.cpu_count() or 1
    load1, load5, _ = os.getloadavg()
    st = "crit" if load5 > ncpu * 8 else "warn" if load5 > ncpu * 2 else "ok"
    add("Host", "Load (5 min)", st, f"{load5:.1f} on {ncpu} vCPU (1 min {load1:.1f})")
    for res in ("cpu", "io", "memory"):
        try:
            line = open(f"/proc/pressure/{res}").readline()   # "some avg10=.. avg60=.. avg300=.. total=.."
            avg60 = float(line.split()[2].split("=")[1])
            st = "crit" if avg60 > 60 else "warn" if avg60 > 25 else "ok"
            add("Host", f"{res.upper()} pressure", st, f"{avg60:.0f}% of the last minute stalled waiting for {res}")
        except OSError:
            pass
    mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
    avail = mem["MemAvailable"] / mem["MemTotal"] * 100
    add("Host", "Memory available", "crit" if avail < 5 else "warn" if avail < 10 else "ok",
        f"{mem['MemAvailable'] // 1024} MB of {mem['MemTotal'] // 1024} MB ({avail:.0f}%)")


# ----------------------------------------------------------------------------- 2. containers + 3. stability
def container_checks():
    names = list(APP_CONTAINERS) + INFRA_CONTAINERS
    rc, out, err = sh(["docker", "inspect", *names], timeout=60)
    found = {c["Name"].lstrip("/"): c for c in json.loads(out or "[]")}
    now = time.time()
    for n in names:
        c = found.get(n)
        if not c:
            add("Containers", n, "crit", "container does not exist")
            continue
        s = c["State"]
        health = (s.get("Health") or {}).get("Status")
        restarts = c.get("RestartCount", 0)
        started = s.get("StartedAt", "")[:19]
        try:
            up_s = now - _utc(started)   # Docker reports StartedAt in UTC
        except ValueError:
            up_s = None
        if s.get("Status") != "running":
            st, detail = "crit", f"{s.get('Status')} (exit {s.get('ExitCode')}{', OOM-killed' if s.get('OOMKilled') else ''})"
        elif health == "unhealthy":
            st, detail = "crit", "running but Docker healthcheck failing"
        else:
            st = "ok"
            detail = f"up {_dur(up_s)}" + (f", {health}" if health else "")
            if health == "starting":
                st = "warn"
            if up_s is not None and up_s < 15 * 60:
                st, detail = "warn", detail + " — restarted recently"
        if restarts:
            detail += f", {restarts} auto-restart(s)"
        if s.get("OOMKilled"):
            st, detail = "crit", detail + ", last exit was an OOM kill"
        add("Containers", n, st, detail)

    since = f"{WINDOW_MIN}m"
    for n in APP_CONTAINERS:
        if n not in found:
            continue
        rc, out, err = sh(["docker", "logs", "--since", since, n], timeout=60)
        logs = out + err
        died = sum(1 for l in logs.splitlines() if "Child process" in l and "died" in l)
        tracebacks = logs.count("Traceback (most recent call last)")
        errors5xx = sum(1 for l in logs.splitlines() if '" 50' in l or "→ 50" in l)
        st = "crit" if died >= 3 else "warn" if died else "ok"
        add("Worker stability", f"{n}: worker restarts", st,
            f"{died} uvicorn worker deaths in {WINDOW_MIN} min" + (" — respawn loop (CPU/memory starvation?)" if died >= 3 else ""))
        if tracebacks or errors5xx:
            add("Worker stability", f"{n}: errors", "warn", f"{tracebacks} tracebacks, {errors5xx} HTTP 5xx in {WINDOW_MIN} min")


def _utc(iso):
    import calendar
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%S"))


def _dur(s):
    if s is None:
        return "?"
    s = int(s)
    return f"{s // 86400}d {s % 86400 // 3600}h" if s >= 86400 else f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else f"{s // 60}m"


# ----------------------------------------------------------------------------- 4. direct + 5. gateway
def api_checks():
    for n, (port, path, _) in APP_CONTAINERS.items():
        svc = n.replace("ereuna_", "").replace("_service", "")
        try:
            code, body, ms = http(f"http://127.0.0.1:{port}{path}")
            st = "crit" if code != 200 else latency_status(ms)
            add("Service API (direct)", f"{svc} {path}", st, f"HTTP {code} in {ms} ms", ms)
        except Exception as e:  # noqa: BLE001
            add("Service API (direct)", f"{svc} {path}", "crit", f"{type(e).__name__}: {e}")
    try:
        code, body, ms = http("http://127.0.0.1:8010/ready")
        add("Service API (direct)", "auth /ready (Postgres)", "crit" if code != 200 else latency_status(ms), f"HTTP {code} in {ms} ms", ms)
    except Exception as e:  # noqa: BLE001
        add("Service API (direct)", "auth /ready (Postgres)", "crit", f"{type(e).__name__}: {e}")

    for name, path, ok_codes, proves in GATEWAY_ROUTES:
        try:
            code, body, ms = http(f"http://127.0.0.1:8080{path}")
            if code in ok_codes and is_json(body):
                add("Gateway (nginx → service → datastore)", name, latency_status(ms), f"HTTP {code} in {ms} ms — {proves}", ms)
            else:
                hint = (" (nginx can't reach the service)" if code in (502, 503, 504)
                        else " (the service crashed handling it — see its traceback in docker logs)" if code == 500 else "")
                add("Gateway (nginx → service → datastore)", name, "crit",
                    f"HTTP {code}{hint}, expected {sorted(ok_codes)} JSON — {proves}: {body[:100]!r}", ms)
        except Exception as e:  # noqa: BLE001
            add("Gateway (nginx → service → datastore)", name, "crit", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------------------- 6. service -> dependency
def dependency_checks():
    for n, (_, _, deps) in APP_CONTAINERS.items():
        if not deps:
            continue
        svc = n.replace("ereuna_", "").replace("_service", "")
        try:
            rc, out, err = sh(["docker", "exec", "-i", n, "python", "-", *deps], timeout=75, stdin=CONTAINER_PROBE_SRC)  # noqa: F821
            results = json.loads(out.strip().splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            add("Service → dependency", f"{svc} → *", "crit", f"probe could not run in container: {type(e).__name__}: {str(e)[:150]}")
            continue
        for r in results:
            st = "ok" if r["ok"] else "crit"
            if r["ok"] and r["ms"] > SLOW_MS:
                st = "warn"
            add("Service → dependency", f"{svc} → {r['dep']}", st, f"{r['detail']} ({r['ms']} ms)", r["ms"])


# ----------------------------------------------------------------------------- 7. infra
def infra_checks():
    g = "Datastores"

    def run(name, cmd, good, timeout=30):
        t = time.monotonic()
        try:
            rc, out, err = sh(cmd, timeout=timeout)
            ms = round((time.monotonic() - t) * 1000)
            ok = rc == 0 and good(out)
            add(g, name, "ok" if ok else "crit", (out or err).strip()[:150] or f"exit {rc}", ms)
        except Exception as e:  # noqa: BLE001
            add(g, name, "crit", f"{type(e).__name__}: {e}")

    run("Postgres", ["docker", "exec", "ereuna_postgres", "pg_isready", "-U", "postgres"], lambda o: "accepting" in o)
    # requirepass is set; NOAUTH proves the server is alive. The authenticated PING runs from auth-service.
    run("Redis", ["docker", "exec", "ereuna_redis", "redis-cli", "ping"], lambda o: "PONG" in o or "NOAUTH" in o)
    try:
        code, body, ms = http("http://127.0.0.1:8123/ping")
        add(g, "ClickHouse", "ok" if code == 200 and body.strip() == b"Ok." else "crit", f"/ping HTTP {code} {body.strip()[:20]!r}", ms)
    except Exception as e:  # noqa: BLE001
        add(g, "ClickHouse", "crit", f"{type(e).__name__}: {e}")
    # OpenSearch serves TLS only (security plugin). An HTTP status (even 401) over TLS proves the node answers;
    # cluster status with credentials is checked from each service container (Service -> dependency).
    import ssl
    t = time.monotonic()
    try:
        req = urllib.request.Request("https://127.0.0.1:9200/", headers={"User-Agent": "aether-health"})
        try:
            code = urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl._create_unverified_context()).status
        except urllib.error.HTTPError as e:
            code = e.code
        add(g, "OpenSearch node (TLS :9200)", "ok" if code in (200, 401) else "crit", f"HTTPS answers {code}",
            round((time.monotonic() - t) * 1000))
    except Exception as e:  # noqa: BLE001
        add(g, "OpenSearch node (TLS :9200)", "crit", f"{type(e).__name__}: {e}")
    try:
        code, body, ms = http("http://127.0.0.1:7474/", timeout=10)
        add(g, "Neo4j (HTTP :7474)", "ok" if code == 200 else "warn", f"HTTP {code} in {ms} ms", ms)
    except Exception as e:  # noqa: BLE001
        add(g, "Neo4j (HTTP :7474)", "warn", f"{type(e).__name__}: {e} (no Aether service uses Neo4j yet)")
    # Neo4j bolt handshake: magic + 4 proposed versions; a live server answers with a non-zero version.
    t = time.monotonic()
    try:
        with socket.create_connection(("127.0.0.1", 7687), timeout=10) as s:
            s.settimeout(10)
            s.sendall(b"\x60\x60\xb0\x17" + struct.pack("!IIII", 0x0404, 0x0304, 4, 3))
            ver = s.recv(4)
        add(g, "Neo4j (bolt)", "ok" if ver and ver != b"\x00\x00\x00\x00" else "crit",
            f"bolt handshake -> {ver.hex()}", round((time.monotonic() - t) * 1000))
    except Exception as e:  # noqa: BLE001
        add(g, "Neo4j (bolt)", "crit", f"{type(e).__name__}: {e}")
    for name, port in (("Kafka broker (host :9092)", 9092), ("ZooKeeper", 2181)):
        t = time.monotonic()
        try:
            socket.create_connection(("127.0.0.1", port), timeout=5).close()
            add(g, name, "ok", "TCP accepts", round((time.monotonic() - t) * 1000))
        except Exception as e:  # noqa: BLE001
            add(g, name, "crit", f"{type(e).__name__}: {e}")


# ----------------------------------------------------------------------------- 8. public
def public_checks():
    for name, path, ok_codes in PUBLIC_ROUTES:
        try:
            code, body, ms = http(PUBLIC_BASE + path, timeout=15)
            st = "ok" if code in ok_codes else "crit"
            if st == "ok" and ms > 4000:
                st = "warn"
            add("Public (Cloudflare → app.ereuna.org)", name, st, f"HTTP {code} in {ms} ms", ms)
        except Exception as e:  # noqa: BLE001
            add("Public (Cloudflare → app.ereuna.org)", name, "crit", f"{type(e).__name__}: {e}")


def main():
    t = time.monotonic()
    for fn in (host_checks, container_checks, api_checks, dependency_checks, infra_checks, public_checks):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - one broken layer must not hide the others
            add("Probe", fn.__name__, "crit", f"layer crashed: {type(e).__name__}: {e}")
    print(json.dumps({"checks": checks, "meta": {"host": socket.gethostname(), "seconds": round(time.monotonic() - t)}}))


main()
