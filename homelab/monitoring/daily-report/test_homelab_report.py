"""Unit tests for the pure parts of homelab_report.py (no network, no SSH).

Run: python3 -m unittest test_homelab_report.py
"""
import unittest

import homelab_report as r


class TestHelpers(unittest.TestCase):
    def test_pct_status(self):
        self.assertEqual(r.pct_status(50, 85, 95), r.OK)
        self.assertEqual(r.pct_status(85, 85, 95), r.WARN)
        self.assertEqual(r.pct_status(97, 85, 95), r.CRIT)
        self.assertEqual(r.pct_status(99, 90), r.WARN)  # no crit threshold

    def test_human_bytes(self):
        self.assertEqual(r.human_bytes(512), "512 B")
        self.assertEqual(r.human_bytes(1536), "1.5 KB")
        self.assertEqual(r.human_bytes(2 * 1024**4), "2.0 TB")

    def test_human_duration(self):
        self.assertEqual(r.human_duration(90061), "1d 1h")
        self.assertEqual(r.human_duration(3720), "1h 2m")

    def test_parse_sections(self):
        out = "@load\n0.1 0.2 0.3 1/2 3\n4\n@docker\n\n@gpu\n"
        sec = r.parse_sections(out)
        self.assertEqual(sec["load"], ["0.1 0.2 0.3 1/2 3", "4"])
        self.assertEqual(sec["docker"], [])
        self.assertEqual(sec["gpu"], [])


class TestContainers(unittest.TestCase):
    def c(self, state, status, name="x"):
        return {"State": state, "Status": status, "Names": name}

    def test_healthy_and_clean_exit_are_fine(self):
        self.assertIsNone(r.classify_container(self.c("running", "Up 3 days (healthy)")))
        self.assertIsNone(r.classify_container(self.c("exited", "Exited (0) 2 weeks ago")))

    def test_problems(self):
        self.assertEqual(r.classify_container(self.c("running", "Up 1 hour (unhealthy)"))[0], r.WARN)
        self.assertEqual(r.classify_container(self.c("restarting", "Restarting (1) 5 seconds ago"))[0], r.CRIT)
        self.assertEqual(r.classify_container(self.c("exited", "Exited (137) 3 hours ago"))[0], r.WARN)


class TestRendering(unittest.TestCase):
    def sections(self):
        a = r.Section("A")
        a.add(r.OK, "fine", "yes")
        b = r.Section("B")
        b.add(r.WARN, "disk", "88%")
        b.add(r.CRIT, "pool", "DEGRADED")
        return [a, b]

    def test_summary_orders_critical_first(self):
        headline, issues = r.summary_line(self.sections())
        self.assertEqual(headline, "1 critical, 1 warning")
        self.assertEqual(issues[0][0], r.CRIT)

    def test_all_healthy(self):
        a = r.Section("A")
        a.add(r.OK, "x", "y")
        self.assertEqual(r.summary_line([a])[0], "All systems healthy")

    def test_html_escapes(self):
        s = r.Section("<b>")
        s.add(r.WARN, "x", "<script>")
        out = r.render_html([s], "today")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


if __name__ == "__main__":
    unittest.main()
