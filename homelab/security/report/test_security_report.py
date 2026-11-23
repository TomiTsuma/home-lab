"""Unit tests for the security report's pure logic (no SSH, nmap, Suricata or SMTP needed).

Run:  python3.11 -m unittest test_security_report.py   (from this folder)
"""
import importlib.util
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("security_report", HERE / "security_report.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

TS = "2026-11-23T10:00:00.000000+0300"


def flow(src, sport, dst, dport, proto="UDP", state="established", to_client=1, b=100):
    return {"timestamp": TS, "event_type": "flow", "src_ip": src, "src_port": sport, "dest_ip": dst, "dest_port": dport,
            "proto": proto, "flow": {"bytes_toserver": b, "bytes_toclient": b, "pkts_toclient": to_client, "state": state}}


def alert(sig, sev=1, classtype="", src="1.2.3.4", dst="192.168.100.9", sid=1):
    return {"timestamp": TS, "event_type": "alert", "src_ip": src, "dest_ip": dst, "dest_port": 22,
            "alert": {"signature": sig, "severity": sev, "signature_id": sid, "category": "",
                      "metadata": {"classtype": [classtype]} if classtype else {}}}


def host(**over):
    d = {"auth": {"readable": True, "failed_by_ip": {}, "failed_users": {}, "accepted": {}, "sudo_cmds": 0, "sudo_fail": 0,
                  "sudo_by_user": {}, "account_changes": []},
         "integrity": {"/etc/passwd": "aaa", "/root/.ssh/authorized_keys": "bbb"}, "uid0": ["root"], "login_users": ["root"],
         "listening": ["tcp 0.0.0.0:22"], "updates": {"total": 0, "security": 0, "security_pkgs": [], "reboot_required": False},
         "sshd": {"passwordauthentication": "no", "permitrootlogin": "prohibit-password", "source": "sshd -T"},
         "docker": {"available": False}, "unattended_upgrades": "active"}
    d.update(over)
    return d


class Traffic(unittest.TestCase):
    def test_udp_reply_to_outbound_port_is_not_inbound(self):
        evs = [flow("192.168.100.9", 41641, "3.3.3.3", 41641), flow("3.3.3.3", 41641, "192.168.100.9", 41641)]
        self.assertEqual(sr.analyze_suricata(evs)["inbound"], {})

    def test_unsolicited_udp_to_listening_port_is_inbound(self):
        evs = [flow("5.5.5.5", 6000, "192.168.100.10", 51413)]
        self.assertIn("192.168.100.10:51413/UDP", sr.analyze_suricata(evs)["inbound"])

    def test_tcp_syn_without_answer_is_not_reachable(self):
        evs = [flow("5.5.5.5", 6000, "192.168.100.10", 22, proto="TCP", state="new", to_client=0)]
        self.assertEqual(sr.analyze_suricata(evs)["inbound"], {})

    def test_external_bytes_counted_per_internal_host(self):
        evs = [flow("192.168.100.9", 5000, "8.8.8.8", 443, proto="TCP", b=1000)]
        self.assertEqual(sr.analyze_suricata(evs)["ext_bytes"], {"192.168.100.9": 2000})

    def test_engine_events_are_noise(self):
        r = sr.analyze_suricata([alert("SURICATA IPv4 truncated packet", sev=3)])
        self.assertEqual((r["alerts"], r["noise"]), ([], 1))


class AlertSeverity(unittest.TestCase):
    def sev(self, ev):
        s = sr.analyze_suricata([ev])
        return sr.suricata_findings({**s, "active": True}, [])[0]["sev"]

    def test_trojan_high(self):
        self.assertEqual(self.sev(alert("ET MALWARE Win32/Foo CnC Checkin", 1, "trojan-activity")), sr.HIGH)

    def test_policy_p2p_low(self):
        self.assertEqual(self.sev(alert("ET P2P BitTorrent DHT ping request", 1, "policy-violation")), sr.LOW)

    def test_info_rule_info(self):
        self.assertEqual(self.sev(alert("ET INFO Session Traversal Utilities for NAT", 3)), sr.INFO)

    def test_suricata_down_is_high(self):
        f = sr.suricata_findings({"active": False}, [])
        self.assertEqual(f[0]["sev"], sr.HIGH)


class HostFindings(unittest.TestCase):
    def test_login_from_internet_is_high(self):
        d = host()
        d["auth"]["accepted"] = {"root|8.8.8.8|publickey": 1}
        f, _ = sr.host_findings("pve", d, {})
        self.assertTrue(any(x["sev"] == sr.HIGH and "from the internet" in x["title"] for x in f))

    def test_lan_login_is_fine(self):
        d = host()
        d["auth"]["accepted"] = {"root|192.168.100.145|publickey": 50}
        f, _ = sr.host_findings("pve", d, {})
        self.assertEqual(f, [])

    def test_authorized_keys_change_is_high_and_listed(self):
        base = {"integrity": {"pve": {"/etc/passwd": "aaa", "/root/.ssh/authorized_keys": "OLD"}}}
        f, changes = sr.host_findings("pve", host(), base)
        self.assertTrue(any(x["sev"] == sr.HIGH and "authorized_keys" in x["title"] for x in f))
        self.assertIn("pve: /root/.ssh/authorized_keys changed", changes)

    def test_first_run_records_without_findings(self):
        f, changes = sr.host_findings("pve", host(), {})
        self.assertEqual((f, changes), ([], []))

    def test_extra_uid0_is_critical(self):
        f, _ = sr.host_findings("pve", host(uid0=["root", "toor"]), {})
        self.assertEqual(f[0]["sev"], sr.CRIT)

    def test_new_listening_port(self):
        base = {"listening": {"pve": ["tcp 0.0.0.0:22"]}}
        f, _ = sr.host_findings("pve", host(listening=["tcp 0.0.0.0:22", "tcp 0.0.0.0:4444"]), base)
        self.assertIn("tcp 0.0.0.0:4444", f[0]["detail"])


class NetworkAndScore(unittest.TestCase):
    def test_new_lan_device(self):
        net = {"devices": {"aa:bb": {"ip": "192.168.100.50", "vendor": "Acme"}}, "ports": {}, "tailnet": {}}
        f, c = sr.network_findings(net, {"lan_devices": {}, "lan_ports": {}, "tailnet": {}})
        self.assertEqual(f[0]["sev"], sr.MED)
        self.assertTrue(c)

    def test_ack_downgrades_to_info(self):
        f = [sr.finding(sr.HIGH, "Intrusion", "network", "x", fid="suricata-sid:42")]
        self.assertEqual(sr.apply_acks(f, {"suricata-sid:42"})[0]["sev"], sr.INFO)
        self.assertEqual(sr.apply_acks(f, {"suricata-sid:*"})[0]["sev"], sr.INFO)

    def test_score(self):
        self.assertEqual(sr.score([]), (100, "A"))
        self.assertEqual(sr.score([sr.finding(sr.CRIT, "a", "b", "c")] * 3), (40, "F"))

    def test_render_escapes_untrusted_text(self):
        f = [sr.finding(sr.HIGH, "Intrusion", "network", "IDS: <script>x</script>")]
        subject, text, body = sr.render(f, [], {"summary_text": "", "summary_html": ""})
        self.assertNotIn("<script>", body)


class Trivy(unittest.TestCase):
    def test_summarize_counts_fixable(self):
        rep = {"Results": [{"Vulnerabilities": [
            {"VulnerabilityID": "CVE-1", "Severity": "CRITICAL", "PkgName": "openssl", "InstalledVersion": "1", "FixedVersion": "2"},
            {"VulnerabilityID": "CVE-2", "Severity": "CRITICAL", "PkgName": "zlib", "InstalledVersion": "1"},
            {"VulnerabilityID": "CVE-3", "Severity": "HIGH", "PkgName": "curl", "InstalledVersion": "1", "FixedVersion": "3"}]}]}
        s = sr.summarize_trivy("img", rep)
        self.assertEqual((s["critical"], s["fixable_critical"], s["high"], s["fixable_high"]), (2, 1, 1, 1))

    def test_stale_scan_flagged(self):
        f = sr.vuln_findings({"finished": time.time() - 3 * 86400, "hosts": {}})
        self.assertIn("old", f[0]["title"])


if __name__ == "__main__":
    unittest.main()
