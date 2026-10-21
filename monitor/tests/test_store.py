import unittest

from velop_doctor.config import Config
from velop_doctor.demo import make_snapshot
from velop_doctor.store import Store


def finding(key="k", sev="warning", secs=0):
    return {"key": key, "severity": sev, "title": f"T {key}", "detail": "d", "min_seconds": secs}


class ConditionTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(":memory:")

    def kinds(self):
        return [e["kind"] for e in reversed(self.s.events())]

    def test_immediate_finding_opens_and_clears(self):
        self.s.update_conditions(1000, [finding()])
        self.assertEqual([f["key"] for f in self.s.active_findings()], ["k"])
        self.s.update_conditions(1060, [])
        self.assertEqual(self.s.active_findings(), [])
        self.assertEqual(self.kinds(), ["problem", "cleared"])

    def test_finding_waits_for_min_seconds(self):
        self.s.update_conditions(1000, [finding(secs=300)])
        self.s.update_conditions(1100, [finding(secs=300)])
        self.assertEqual(self.s.active_findings(), [])
        self.s.update_conditions(1300, [finding(secs=300)])
        self.assertEqual(len(self.s.active_findings()), 1)

    def test_blip_never_alerts_and_leaves_no_events(self):
        self.s.update_conditions(1000, [finding(secs=300)])
        self.s.update_conditions(1060, [])
        self.assertEqual(self.s.events(), [])

    def test_long_gap_restarts_the_clock(self):
        self.s.update_conditions(1000, [finding(secs=300)], gap_seconds=200)
        self.s.update_conditions(9000, [finding(secs=300)], gap_seconds=200)
        self.assertEqual(self.s.active_findings(), [], "a sleeping laptop is not 2 hours of problem")

    def test_critical_sorts_first(self):
        self.s.update_conditions(1, [finding("a", "info"), finding("b", "critical"), finding("c", "warning")])
        self.assertEqual([f["key"] for f in self.s.active_findings()], ["b", "c", "a"])


class SnapshotTests(unittest.TestCase):
    def test_roundtrip_history_and_prune(self):
        s, cfg = Store(":memory:"), Config(primary="x")
        for t in (1000, 1060, 1120):
            s.add_snapshot(make_snapshot(cfg, "healthy", now=t))
        self.assertEqual(s.latest_snapshot()["ts"], 1120)
        self.assertEqual(s.latest_snapshot(offset=1)["ts"], 1060)
        series = s.history(0)
        self.assertIn("node:Hall", series)
        self.assertIn("internet:1.1.1.1", series)
        self.assertEqual(len(series["node:Hall"]), 3)
        s.prune(1120 + 86400 * 8, 7)
        self.assertIsNone(s.latest_snapshot())


if __name__ == "__main__":
    unittest.main()
