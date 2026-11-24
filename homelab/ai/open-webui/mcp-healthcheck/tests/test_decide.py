"""Unit tests for the decision logic. Run: python3.11 -m unittest discover -s mcp-healthcheck/tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import healthcheck as hc  # noqa: E402


def conn(cid, enable=True, ours=False):
    cfg = {"enable": enable}
    if ours:
        cfg[hc.MARKER] = hc.MARKER_VALUE
    return {"type": "mcp", "url": f"http://{cid}/mcp", "key": "secret", "config": cfg, "info": {"id": cid}}


class DecideTests(unittest.TestCase):
    def test_two_consecutive_failures_disable(self):
        conns, state = [conn("a"), conn("b")], {}
        self.assertEqual(hc.decide(conns, {"a": False, "b": True}, state), [])
        self.assertEqual(hc.decide(conns, {"a": False, "b": True}, state), [("a", "disable")])

    def test_single_failure_then_success_resets(self):
        conns, state = [conn("a"), conn("b")], {}
        hc.decide(conns, {"a": False, "b": True}, state)
        hc.decide(conns, {"a": True, "b": True}, state)
        self.assertEqual(hc.decide(conns, {"a": False, "b": True}, state), [])

    def test_manually_disabled_is_never_probed_or_enabled(self):
        c = conn("a", enable=False)
        self.assertFalse(hc.should_probe(c))
        self.assertTrue(hc.should_probe(conn("a", enable=False, ours=True)))
        self.assertTrue(hc.should_probe(conn("a")))

    def test_recovery_needs_consecutive_successes(self):
        conns, state = [conn("a", enable=False, ours=True), conn("b")], {}
        self.assertEqual(hc.decide(conns, {"a": True, "b": True}, state), [])
        self.assertEqual(hc.decide(conns, {"a": True, "b": True}, state), [("a", "enable")])

    def test_still_down_stays_disabled(self):
        conns, state = [conn("a", enable=False, ours=True), conn("b")], {}
        for _ in range(3):
            self.assertEqual(hc.decide(conns, {"a": False, "b": True}, state), [])

    def test_manual_reenable_drops_marker(self):
        conns, state = [conn("a", enable=True, ours=True), conn("b")], {}
        self.assertEqual(hc.decide(conns, {"a": True, "b": True}, state), [("a", "clear")])

    def test_all_failing_is_systemic_and_changes_nothing(self):
        conns, state = [conn("a"), conn("b"), conn("c")], {}
        for _ in range(3):
            self.assertEqual(hc.decide(conns, {"a": False, "b": False, "c": False}, state), [])
        self.assertEqual(state, {})  # failures not counted either

    def test_two_servers_both_down_is_not_systemic(self):
        conns, state = [conn("a"), conn("b")], {}
        hc.decide(conns, {"a": False, "b": False}, state)
        self.assertEqual(sorted(hc.decide(conns, {"a": False, "b": False}, state)), [("a", "disable"), ("b", "disable")])

    def test_apply_only_touches_target_and_keeps_secrets(self):
        conns = [conn("a"), conn("b")]
        out = hc.apply_actions(conns, [("a", "disable")], "T")
        self.assertFalse(out[0]["config"]["enable"])
        self.assertEqual(out[0]["config"][hc.MARKER], hc.MARKER_VALUE)
        self.assertEqual(out[0]["key"], "secret")
        self.assertEqual(out[1], conns[1])
        self.assertTrue(conns[0]["config"]["enable"])  # input not mutated

    def test_apply_enable_removes_marker(self):
        out = hc.apply_actions([conn("a", enable=False, ours=True)], [("a", "enable")], "T")
        self.assertTrue(out[0]["config"]["enable"])
        self.assertNotIn(hc.MARKER, out[0]["config"])

    def test_jwt_shape(self):
        parts = hc.mint_jwt("k", "uid").split(".")
        self.assertEqual(len(parts), 3)


if __name__ == "__main__":
    unittest.main()
