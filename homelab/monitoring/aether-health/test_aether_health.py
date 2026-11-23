"""Unit tests for the alert state machine, acks and rendering (pure functions; no SSH, no SMTP).

Run:  python3.11 -m unittest test_aether_health.py   (from this folder)
"""
import importlib.util
import json
import socket
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Only what an interpreter needs to start (SYSTEMROOT: Windows sockets), so no real service env leaks in.
BASE_ENV = {k: v for k, v in __import__("os").environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP")}
spec = importlib.util.spec_from_file_location("aether_health", HERE / "aether_health.py")
ah = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ah)


def c(name, status, group="Gateway"):
    return ah.check(group, name, status, "detail")


class StateMachine(unittest.TestCase):
    """Policy: email only for confirmed unreachable (critical) checks, at most once per 24 h."""

    def run_seq(self, runs, report=False, step=300, state=None):
        state, kinds = state or {}, []
        for i, checks in enumerate(runs):
            kind, state = ah.evaluate(checks, state, now=10_000_000 + i * step, report=report)
            kinds.append(kind)
        return kinds, state

    def test_single_blip_does_not_alert(self):
        kinds, _ = self.run_seq([[c("a", "crit")], [c("a", "ok")], [c("a", "ok")]])
        self.assertEqual(kinds, [None, None, None])

    def test_alert_after_two_consecutive_failures_no_recovery_mail(self):
        kinds, _ = self.run_seq([[c("a", "crit")], [c("a", "crit")], [c("a", "crit")], [c("a", "ok")]])
        self.assertEqual(kinds, [None, "alert", None, None])

    def test_at_most_one_email_per_day_even_for_a_new_outage(self):
        kinds, _ = self.run_seq([[c("a", "crit")]] * 2 + [[c("a", "crit"), c("b", "crit")]] * 3)
        self.assertEqual(kinds.count("alert"), 1)

    def test_next_day_still_failing_emails_again(self):
        state = {"fails": {"Gateway|a": 50}, "alerted": ["Gateway|a"], "last_email": 0}
        kind, _ = ah.evaluate([c("a", "crit")], state, now=ah.EMAIL_EVERY_HOURS * 3600 + 1)
        self.assertEqual(kind, "alert")
        kind, _ = ah.evaluate([c("a", "crit")], {**state, "last_email": 1000}, now=2000)
        self.assertIsNone(kind)

    def test_warnings_never_email(self):
        kinds, _ = self.run_seq([[c("a", "warn")]] * 6)
        self.assertEqual(kinds, [None] * 6)

    def test_report_flag_sends_without_using_the_daily_alert_slot(self):
        kind, state = ah.evaluate([c("a", "ok")], {}, now=5, report=True)
        self.assertEqual(kind, "report")
        self.assertEqual(state["last_email"], 0)


class Acks(unittest.TestCase):
    def test_acknowledged_crit_becomes_warn(self):
        out = ah.apply_acks([c("a", "crit"), c("b", "crit")], {"Gateway|a"})
        self.assertEqual([x["status"] for x in out], ["warn", "crit"])
        self.assertIn("acknowledged", out[0]["detail"])


class Rendering(unittest.TestCase):
    def test_html_escapes_untrusted_detail(self):
        checks = [ah.check("G", "x", "crit", "<script>alert(1)</script>")]
        body = ah.render_html("alert", checks, {}, "h")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_subject_names_the_first_new_failure(self):
        checks = [c("old", "crit"), c("new", "crit")]
        s = ah.subject_for("alert", checks, {"Gateway|new"})
        self.assertIn("new", s)
        self.assertIn("+1 more", s)


class ContainerProbeProtocols(unittest.TestCase):
    """Runs container_probe.py against fake local servers, so the protocol code is exercised for real."""

    def probe(self, env, *deps):
        r = subprocess.run([sys.executable, str(HERE / "probes" / "container_probe.py"), *deps],
                           capture_output=True, text=True, timeout=60, env={**BASE_ENV, **env})
        return {x["dep"]: x for x in json.loads(r.stdout)}

    def tcp_server(self, reply_fn):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def serve():
            conn, _ = srv.accept()
            with conn:
                reply_fn(conn)
        threading.Thread(target=serve, daemon=True).start()
        return srv.getsockname()[1]

    def test_redis_ping(self):
        port = self.tcp_server(lambda s: (s.recv(64), s.sendall(b"+PONG\r\n")))
        r = self.probe({"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(port)}, "redis")["redis"]
        self.assertTrue(r["ok"], r)

    def test_kafka_api_versions(self):
        import struct

        def broker(s):
            s.recv(4096)
            body = struct.pack("!ihi", 4242, 0, 3)            # correlation id, error 0, 3 apis
            s.sendall(struct.pack("!i", len(body)) + body)
        port = self.tcp_server(broker)
        r = self.probe({"KAFKA_BOOTSTRAP_SERVERS": f"127.0.0.1:{port}"}, "kafka")["kafka"]
        self.assertTrue(r["ok"], r)

    def test_unresolvable_minio_is_explained(self):
        r = self.probe({"MINIO_ENDPOINT": "no-such-host.invalid:9000"}, "minio")["minio"]
        self.assertFalse(r["ok"])
        self.assertIn("does not resolve", r["detail"])

    def test_secrets_are_scrubbed(self):
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"bad password hunter2-secret")

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        r = self.probe({"CLICKHOUSE_HOST": "127.0.0.1", "CLICKHOUSE_PORT": str(srv.server_port),
                        "CLICKHOUSE_PASSWORD": "hunter2-secret"}, "clickhouse")["clickhouse"]
        srv.shutdown()
        self.assertFalse(r["ok"])
        self.assertNotIn("hunter2-secret", r["detail"])


if __name__ == "__main__":
    unittest.main()
