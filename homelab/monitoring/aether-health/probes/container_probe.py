"""Runs INSIDE an Aether service container (`docker exec -i <c> python - <dep> ...`).

Tests each dependency the way the service itself would reach it: same network namespace, same DNS, same
env vars (hosts, ports, credentials). Prints one JSON list of results on stdout. Standard library only,
plus psycopg2/psycopg if the image already has them (for a real authenticated `SELECT 1`).

Every check is: DNS resolve -> TCP connect -> protocol-level request. A bare TCP connect is not enough
(a port can accept connections while the server behind it is wedged), so each dependency gets a request
that only a working server answers correctly.

Secrets: values of env vars whose names contain PASS/SECRET/KEY/TOKEN are scrubbed from every message.
"""
import base64
import json
import os
import re
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 5.0
E = os.environ.get
SECRETS = [v for k, v in os.environ.items() if re.search(r"PASS|SECRET|KEY|TOKEN", k) and len(v) >= 4]


def scrub(s):
    s = str(s)
    for v in SECRETS:
        s = s.replace(v, "***")
    return s[:300]


def resolve(host):
    return socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)[0][4][0]


def tcp(host, port):
    return socket.create_connection((host, int(port)), timeout=TIMEOUT)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed by server")
        buf += chunk
    return buf


def http(url, user=None, password=None, insecure=False):
    req = urllib.request.Request(url, headers={"User-Agent": "aether-health"})
    if user:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{password or ''}".encode()).decode())
    ctx = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, e.read(65536)


# ----------------------------------------------------------------------------- dependency checks
# Each returns (ok: bool, detail: str). Raising also counts as a failure.

def check_postgres():
    host, port = E("POSTGRES_HOST", "postgres"), E("POSTGRES_PORT", "5432")
    resolve(host)
    dsn = E("DATABASE_URL") or E("SQLALCHEMY_DATABASE_URI")
    for mod in ("psycopg2", "psycopg"):
        try:
            lib = __import__(mod)
        except ImportError:
            continue
        kw = dict(host=host, port=int(port), user=E("POSTGRES_USER", "postgres"),
                  password=E("POSTGRES_PASSWORD", ""), dbname=E("POSTGRES_DB", "postgres"), connect_timeout=5)
        if dsn and dsn.startswith("postgres"):
            conn = lib.connect(re.sub(r"^postgres(ql)?\+\w+://", "postgresql://", dsn), connect_timeout=5)
        else:
            conn = lib.connect(**kw)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()
        return True, f"authenticated SELECT 1 via {mod}"
    # No driver in the image: protocol handshake (SSLRequest); a live server answers 'S' or 'N'.
    with tcp(host, port) as s:
        s.sendall(struct.pack("!ii", 8, 80877103))
        r = recv_exact(s, 1)
    return r in (b"S", b"N"), f"protocol handshake answered {r!r} (no driver in image)"


def check_redis():
    host, port = E("REDIS_HOST", "redis"), E("REDIS_PORT", "6379")
    resolve(host)
    with tcp(host, port) as s:
        pw = E("REDIS_PASSWORD")
        if pw:
            s.sendall(f"*2\r\n$4\r\nAUTH\r\n${len(pw)}\r\n{pw}\r\n".encode())
            s.recv(256)
        s.sendall(b"*1\r\n$4\r\nPING\r\n")
        r = s.recv(256)
    return r.startswith(b"+PONG"), f"PING -> {r.strip()[:40]!r}"


def check_kafka():
    servers = E("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092").split(",")
    host, port = servers[0].strip().rsplit(":", 1)
    resolve(host)
    # ApiVersions v0 request: a working broker replies with our correlation id and error_code 0.
    corr, client = 4242, b"aether-health"
    body = struct.pack("!hhih", 18, 0, corr, len(client)) + client
    with tcp(host, port) as s:
        s.sendall(struct.pack("!i", len(body)) + body)
        size = struct.unpack("!i", recv_exact(s, 4))[0]
        resp = recv_exact(s, size)
    rcorr, err = struct.unpack("!ih", resp[:6])
    n_apis = struct.unpack("!i", resp[6:10])[0] if len(resp) >= 10 else 0
    return rcorr == corr and err == 0, f"ApiVersions ok, {n_apis} APIs" if err == 0 else f"ApiVersions error_code={err}"


def check_clickhouse():
    host = E("CLICKHOUSE_HOST", "clickhouse")
    port = E("CLICKHOUSE_PORT") or E("CLICKHOUSE_HTTP_PORT", "8123")
    resolve(host)
    code, body = http(f"http://{host}:{port}/?query=SELECT%201", E("CLICKHOUSE_USER", "default"), E("CLICKHOUSE_PASSWORD", ""))
    if code == 200 and body.strip() == b"1":
        return True, "authenticated SELECT 1"
    code2, body2 = http(f"http://{host}:{port}/ping")
    return False, f"query HTTP {code}: {scrub(body[:120])!s}; /ping HTTP {code2} {body2.strip()[:20]!r}"


def check_opensearch():
    host, port = E("OPENSEARCH_HOST", "opensearch"), E("OPENSEARCH_PORT", "9200")
    resolve(host)
    # Unset means the code's own default, which is TLS for both services (repository settings default
    # True; datasets hard-codes use_ssl=True). Only an explicit false means plain HTTP.
    scheme = "http" if E("OPENSEARCH_USE_SSL", "true").lower() in ("0", "false", "no") else "https"
    user, pw = E("OPENSEARCH_USER"), E("OPENSEARCH_PASSWORD") or E("OPENSEARCH_INITIAL_ADMIN_PASSWORD")
    try:
        code, body = http(f"{scheme}://{host}:{port}/_cluster/health", user, pw, insecure=True)
    except Exception as e:  # noqa: BLE001
        # Diagnose the classic mismatch: the service speaks one scheme, the server the other.
        other = "http" if scheme == "https" else "https"
        try:
            ocode, _ = http(f"{other}://{host}:{port}/_cluster/health", user, pw, insecure=True)
            return False, (f"{scheme} fails ({type(e).__name__}) but the server answers {other} (HTTP {ocode}) — "
                           f"service has OPENSEARCH_USE_SSL={E('OPENSEARCH_USE_SSL', 'unset')}, so its search calls fail")
        except Exception:  # noqa: BLE001
            raise e
    if code != 200:
        return False, f"_cluster/health HTTP {code}" + (" — credentials rejected" if code in (401, 403) else "")
    st = json.loads(body).get("status")
    return st in ("green", "yellow"), f"cluster {st}"


def check_minio():
    ep = E("MINIO_ENDPOINT", "minio:9000")
    secure = E("MINIO_SECURE", "false").lower() in ("1", "true", "yes")
    host = ep.split(":")[0]
    try:
        resolve(host)
    except socket.gaierror:
        return False, f"MINIO_ENDPOINT={ep}: host '{host}' does not resolve — no such container/DNS name"
    code, _ = http(f"{'https' if secure else 'http'}://{ep}/minio/health/live")
    return code == 200, f"{ep} /minio/health/live HTTP {code}"


def check_auth_jwks():
    # The exact call repository/platform make to verify every user JWT.
    url = E("AUTH_SERVICE_URL", "http://auth-service:8000").rstrip("/") + "/.well-known/jwks.json"
    resolve(url.split("//")[1].split(":")[0].split("/")[0])
    code, body = http(url)
    keys = json.loads(body).get("keys", []) if code == 200 else []
    return code == 200 and len(keys) > 0, f"JWKS HTTP {code}, {len(keys)} key(s)"


CHECKS = {"postgres": check_postgres, "redis": check_redis, "kafka": check_kafka, "clickhouse": check_clickhouse,
          "opensearch": check_opensearch, "minio": check_minio, "auth-jwks": check_auth_jwks}


def main(deps):
    out = []
    for dep in deps:
        t = time.monotonic()
        try:
            ok, detail = CHECKS[dep]()
        except Exception as e:  # noqa: BLE001 - every failure mode becomes a reported result
            ok, detail = False, f"{type(e).__name__}: {e}"
        out.append({"dep": dep, "ok": bool(ok), "detail": scrub(detail), "ms": round((time.monotonic() - t) * 1000)})
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1:])
