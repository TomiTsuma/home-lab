"""Open WebUI tool-server health check: switch dead MCP/OpenAPI connections off, back on when they recover.

Why: Open WebUI connects to every ENABLED tool server on each chat request that uses tools. One
unreachable server therefore delays every such request (up to MCP_INITIALIZE_TIMEOUT seconds),
including requests to Claude models. A disabled connection is not contacted and is not offered
in the tool picker, so it cannot be toggled on while it is down.

How it works (every CHECK_INTERVAL seconds):
  1. GET /api/v1/configs/tool_servers                 -> the connections
  2. POST /api/v1/configs/tool_servers/verify (each)  -> Open WebUI itself connects and lists the
     tools (the same call as the "Verify connection" button). The probe therefore runs from
     Open WebUI's own network position, so this container needs no access to the isolated MCP
     networks (gws, chmcp, dgmcp, ghmcp, gtmcp).
  3. FAILS_TO_DISABLE consecutive failures -> config.enable=false plus a marker
     (config.auto_disabled_by="mcp-healthcheck"). OKS_TO_ENABLE consecutive successes on a
     connection carrying that marker -> enable again and drop the marker.

Safety rules:
  * A connection you disabled by hand (no marker) is never probed or re-enabled.
  * If you re-enable a marked connection by hand, the marker is simply dropped.
  * If every probe fails (>= 3 connections), the problem is Open WebUI or the network, not the
    servers: nothing is changed and no failure is counted.
  * If Open WebUI itself is unreachable or rejects our token, the cycle is skipped.
  * DRY_RUN=true logs the decisions but changes nothing.

Auth: a short-lived (2 min) admin JWT signed with WEBUI_SECRET_KEY, minted per cycle with stdlib
only (HS256). The container holds that key (it can sign a token for any user), so it is kept
minimal: read-only filesystem, no capabilities, no published ports, only the default network.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

MARKER = "auto_disabled_by"
MARKER_VALUE = "mcp-healthcheck"
SYSTEMIC_MIN_PROBES = 3

log = logging.getLogger("mcp-healthcheck")


class OpenWebUIUnavailable(Exception):
    """Open WebUI did not answer sanely (down, overloaded, bad token). Not the servers' fault."""


# --------------------------------------------------------------------------------------------
# Pure decision logic (unit-tested in tests/test_decide.py)
# --------------------------------------------------------------------------------------------
def conn_id(conn):
    return (conn.get("info") or {}).get("id")


def is_enabled(conn):
    return bool((conn.get("config") or {}).get("enable"))


def is_ours(conn):
    return (conn.get("config") or {}).get(MARKER) == MARKER_VALUE


def should_probe(conn):
    """Probe enabled connections, and disabled ones only if WE disabled them."""
    return bool(conn_id(conn)) and (is_enabled(conn) or is_ours(conn))


def decide(conns, results, state, fails_to_disable=2, oks_to_enable=2):
    """Update `state` (per-id consecutive counters) and return [(id, action)].

    results: {id: bool} for the probed connections. action is "disable", "enable" or "clear".
    """
    if len(results) >= SYSTEMIC_MIN_PROBES and not any(results.values()):
        log.warning("all %d probes failed: assuming an Open WebUI/network problem, changing nothing", len(results))
        return []
    actions = []
    for conn in conns:
        cid = conn_id(conn)
        if cid not in results:
            continue
        st = state.setdefault(cid, {"fail": 0, "ok": 0})
        if results[cid]:
            st["ok"] += 1
            st["fail"] = 0
        else:
            st["fail"] += 1
            st["ok"] = 0
        if is_enabled(conn):
            if is_ours(conn):
                actions.append((cid, "clear"))  # re-enabled by hand: stop owning it
            elif st["fail"] >= fails_to_disable:
                actions.append((cid, "disable"))
        elif is_ours(conn) and st["ok"] >= oks_to_enable:
            actions.append((cid, "enable"))
    return actions


def apply_actions(conns, actions, now_iso):
    """Return a copy of `conns` with the actions applied (nothing else touched)."""
    by_id = dict(actions)
    out = []
    for conn in conns:
        act = by_id.get(conn_id(conn))
        if not act:
            out.append(conn)
            continue
        conn = json.loads(json.dumps(conn))
        cfg = conn.setdefault("config", {})
        if act == "disable":
            cfg["enable"] = False
            cfg[MARKER] = MARKER_VALUE
            cfg["auto_disabled_at"] = now_iso
        else:  # enable / clear
            if act == "enable":
                cfg["enable"] = True
            cfg.pop(MARKER, None)
            cfg.pop("auto_disabled_at", None)
        out.append(conn)
    return out


# --------------------------------------------------------------------------------------------
# Open WebUI API
# --------------------------------------------------------------------------------------------
def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_jwt(secret, user_id, ttl=120):
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"id": user_id, "exp": int(time.time()) + ttl}).encode())
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


class OpenWebUI:
    def __init__(self, base_url, secret, admin_id, http_timeout):
        self.base, self.secret, self.admin_id, self.timeout = base_url.rstrip("/"), secret, admin_id, http_timeout

    def call(self, method, path, body=None, timeout=None):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {mint_jwt(self.secret, self.admin_id)}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OpenWebUIUnavailable(f"{method} {path}: {e}") from e

    def list_connections(self):
        status, data = self.call("GET", "/api/v1/configs/tool_servers")
        if status != 200 or data is None:
            raise OpenWebUIUnavailable(f"GET tool_servers -> HTTP {status} (bad token or Open WebUI unhealthy)")
        return data.get("TOOL_SERVER_CONNECTIONS") or []

    def probe(self, conn):
        """True if Open WebUI can connect to the server and list its tools."""
        status, data = self.call("POST", "/api/v1/configs/tool_servers/verify", conn)
        if status == 200 and data and data.get("status"):
            return True
        if status in (401, 403, 404, 405, 422) or status >= 500:
            # Not a verdict on the server: wrong token, endpoint changed, Open WebUI broken.
            raise OpenWebUIUnavailable(f"verify {conn_id(conn)} -> HTTP {status}")
        return False  # 400 = "Failed to create MCP client" / OpenAPI fetch failed

    def save(self, conns):
        status, _ = self.call("POST", "/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": conns}, timeout=120)
        if status != 200:
            raise OpenWebUIUnavailable(f"POST tool_servers -> HTTP {status}")


# --------------------------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------------------------
def run_cycle(api, state, cfg):
    conns = api.list_connections()
    targets = [c for c in conns if should_probe(c)]
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(targets)))) as pool:
        verdicts = list(pool.map(api.probe, targets))
    results = {conn_id(c): v for c, v in zip(targets, verdicts)}
    actions = decide(conns, results, state, cfg["fails_to_disable"], cfg["oks_to_enable"])
    summary = " ".join(f"{k}:{'ok' if v else 'FAIL(%d)' % state[k]['fail']}" for k, v in sorted(results.items()))
    log.info("probed %d: %s", len(results), summary or "-")
    if not actions:
        return
    for cid, act in actions:
        log.warning("%s %s%s", act.upper(), cid, " (dry run)" if cfg["dry_run"] else "")
    if cfg["dry_run"]:
        return
    # Re-read right before writing: the POST replaces the whole list, so keep the window with
    # the UI small and apply the decisions to the freshest copy.
    fresh = api.list_connections()
    api.save(apply_actions(fresh, actions, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    for cid, act in actions:
        state[cid] = {"fail": 0, "ok": 0}


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
    cfg = {
        "interval": float(os.environ.get("CHECK_INTERVAL", "45")),
        "fails_to_disable": int(os.environ.get("FAILS_TO_DISABLE", "2")),
        "oks_to_enable": int(os.environ.get("OKS_TO_ENABLE", "2")),
        "dry_run": os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes"),
    }
    api = OpenWebUI(os.environ.get("OWUI_URL", "http://open-webui:8080"), os.environ["WEBUI_SECRET_KEY"],
                    os.environ["OWUI_ADMIN_ID"], float(os.environ.get("PROBE_HTTP_TIMEOUT", "30")))
    heartbeat = os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat")
    once = "--once" in sys.argv
    log.info("started: interval=%ss disable after %d fails, enable after %d oks, dry_run=%s",
             cfg["interval"], cfg["fails_to_disable"], cfg["oks_to_enable"], cfg["dry_run"])
    state = {}
    while True:
        try:
            run_cycle(api, state, cfg)
            open(heartbeat, "w").write(str(time.time()))
        except OpenWebUIUnavailable as e:
            log.warning("cycle skipped, nothing changed: %s", e)
        except Exception:  # keep the loop alive; the heartbeat goes stale and Docker marks us unhealthy
            log.exception("unexpected error")
        if once:
            return
        time.sleep(cfg["interval"])


if __name__ == "__main__":
    main()
