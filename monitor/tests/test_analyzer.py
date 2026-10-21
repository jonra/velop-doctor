import copy
import unittest

from velop_doctor import analyzer
from velop_doctor.config import Config
from velop_doctor.demo import make_snapshot


def keys(snap, cfg=None):
    return {f["key"] for f in analyzer.evaluate(snap, cfg or Config(primary="x"))}


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(primary="192.168.1.1")

    def test_healthy_mesh_only_reports_informational_findings(self):
        snap = make_snapshot(self.cfg, "healthy", steering=True)
        findings = analyzer.evaluate(snap, self.cfg)
        self.assertEqual({f["severity"] for f in findings} - {"info"}, set(), findings)

    def test_problem_scenario_finds_the_known_faults(self):
        k = keys(make_snapshot(self.cfg, "problems"), self.cfg)
        self.assertIn("node_sync:id-bed", k)
        self.assertIn("ghost:id-ghost", k)
        self.assertIn("steering_off", k)
        self.assertIn("double_nat", k)
        self.assertIn("weak_clients", k)

    def test_idle_node_is_flagged_only_with_enough_wifi_clients(self):
        snap = make_snapshot(self.cfg, "healthy", steering=True)
        for n in snap["nodes"]:
            if n["id"] == "id-bed":
                n["clients"] = 0
        for c in snap["clients"]:
            if c["node_id"] == "id-bed":
                c["node_id"], c["node"] = "id-main", "Living room (main)"
        self.assertIn("idle:id-bed", keys(snap, self.cfg))
        snap["clients"] = snap["clients"][:3]
        self.assertNotIn("idle:id-bed", keys(snap, self.cfg))

    def test_ssid_mismatch_means_second_network(self):
        snap = make_snapshot(self.cfg, "healthy", steering=True)
        bed = [n for n in snap["nodes"] if n["id"] == "id-bed"][0]
        for r in bed["radios"]:
            r["ssid"] = "Other network"
        self.assertIn("ssid:id-bed", keys(snap, self.cfg))

    def test_factory_default_node(self):
        snap = make_snapshot(self.cfg, "healthy", steering=True)
        [n for n in snap["nodes"] if n["id"] == "id-bed"][0]["default_pw"] = True
        self.assertIn("node_unconfigured:id-bed", keys(snap, self.cfg))

    def test_slow_wired_link_and_firmware_mismatch(self):
        snap = make_snapshot(self.cfg, "healthy", steering=True)
        bed = [n for n in snap["nodes"] if n["id"] == "id-bed"][0]
        bed["backhaul"]["mbps"] = 100
        bed["firmware"] = "2.0.0.1"
        k = keys(snap, self.cfg)
        self.assertIn("slow_link:id-bed", k)
        self.assertIn("firmware_mismatch", k)

    def test_node_down_and_internet_down(self):
        snap = make_snapshot(self.cfg, "healthy", steering=True)
        [n for n in snap["nodes"] if n["id"] == "id-hall"][0].update(reachable=False, loss=100.0)
        for i in snap["internet"]:
            i["loss"] = 100.0
        k = keys(snap, self.cfg)
        self.assertIn("node_down:id-hall", k)
        self.assertIn("internet_down", k)

    def test_stale_wireless_entries_do_not_count_as_weak(self):
        snap = make_snapshot(self.cfg, "healthy", steering=True)
        for c in snap["clients"]:
            c["age_s"] = 99999
        self.assertNotIn("weak_clients", keys(snap, self.cfg))

    def test_unreachable_primary_short_circuits(self):
        snap = make_snapshot(self.cfg, "healthy")
        snap["primary"]["reachable"] = False
        self.assertIn("primary_unreachable", keys(snap, self.cfg))
        self.assertNotIn("steering_off", keys(snap, self.cfg))

    def test_rejected_password(self):
        snap = make_snapshot(self.cfg, "healthy")
        snap["primary"]["auth_ok"] = False
        self.assertEqual(keys(snap, self.cfg) & {"auth_failed"}, {"auth_failed"})


class DiffEventTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(primary="192.168.1.1")
        self.a = make_snapshot(self.cfg, "healthy", steering=True)

    def kinds(self, b):
        return [e[1] for e in analyzer.diff_events(self.a, b)]

    def test_no_change_no_events(self):
        self.assertEqual(self.kinds(copy.deepcopy(self.a)), [])

    def test_backhaul_switches_to_wireless(self):
        b = copy.deepcopy(self.a)
        [n for n in b["nodes"] if n["id"] == "id-hall"][0]["backhaul"]["type"] = "Wireless"
        self.assertIn("backhaul_type", self.kinds(b))

    def test_node_leaves_mesh_and_client_roams(self):
        b = copy.deepcopy(self.a)
        [n for n in b["nodes"] if n["id"] == "id-bed"][0]["in_mesh"] = False
        mover = [c for c in b["clients"] if c["kind"] == "wireless" and c["node_id"] == "id-main"][0]
        mover["node_id"] = "id-hall"
        self.assertEqual(sorted(set(self.kinds(b))), ["mesh_membership", "roam"])

    def test_first_snapshot_has_no_events(self):
        self.assertEqual(analyzer.diff_events(None, self.a), [])


if __name__ == "__main__":
    unittest.main()
