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
    def run_seq(self, runs, report=False):
        state, kinds = {}, []
        for i, checks in enumerate(runs):
            kind, state = ah.evaluate(checks, state, now=1000 + i * 300, report=report)
            kinds.append(kind)
        return kinds, state

    def test_single_blip_does_not_alert(self):
        kinds, _ = self.run_seq([[c("a", "crit")], [c("a", "ok")], [c("a", "ok")]])
        self.assertEqual(kinds, [None, None, None])

    def test_alert_after_two_consecutive_failures_then_recovered(self):
        kinds, state = self.run_seq([[c("a", "crit")], [c("a", "crit")], [c("a", "crit")], [c("a", "ok")]])
        self.assertEqual(kinds, [None, "alert", None, "recovered"])
        self.assertEqual(state["alerted"], [])

    def test_new_failure_while_alerting_sends_another_alert(self):
        kinds, state = self.run_seq([[c("a", "crit")], [c("a", "crit")],
                                     [c("a", "crit"), c("b", "crit")], [c("a", "crit"), c("b", "crit")]])
        self.assertEqual(kinds, [None, "alert", None, "alert"])
        self.assertEqual(state["alerted"], ["Gateway|a", "Gateway|b"])

    def test_reminder_after_remind_hours(self):
        state = {"fails": {"Gateway|a": 5}, "alerted": ["Gateway|a"], "last_email": 0}
        kind, _ = ah.evaluate([c("a", "crit")], state, now=ah.REMIND_HOURS * 3600 + 1)
        self.assertEqual(kind, "reminder")
        kind, _ = ah.evaluate([c("a", "crit")], state, now=60)
        self.assertIsNone(kind)

    def test_warnings_never_alert(self):
        kinds, _ = self.run_seq([[c("a", "warn")]] * 4)
        self.assertEqual(kinds, [None] * 4)

    def test_report_flag_always_sends(self):
        kinds, _ = self.run_seq([[c("a", "ok")]], report=True)
        self.assertEqual(kinds, ["report"])

    def test_no_recovery_mail_while_another_alerted_check_still_fails(self):
        kinds, state = self.run_seq([[c("a", "crit"), c("b", "crit")]] * 2 + [[c("a", "ok"), c("b", "crit")]])
        self.assertEqual(kinds, [None, "alert", None])
        self.assertEqual(state["alerted"], ["Gateway|b"])


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
