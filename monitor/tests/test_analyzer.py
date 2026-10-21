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


class ProbeFindingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(primary="192.168.1.1")
        self.snap = make_snapshot(self.cfg, "healthy", steering=True)

    def findings(self):
        return {f["key"]: f for f in analyzer.evaluate(self.snap, self.cfg)}

    def test_healthy_probes_raise_nothing_beyond_info(self):
        self.snap["probes"]["dns"][1]["ok"] = True
        self.snap["probes"]["dns"][1]["ms"] = 20.0
        self.assertEqual({f["severity"] for f in self.findings().values()} - {"info"}, set())

    def test_router_dns_failing_while_public_works_is_critical(self):
        self.snap["probes"]["dns"][0].update(ok=False, ms=None)
        self.assertEqual(self.findings()["dns_router"]["severity"], "critical")

    def test_dns_failure_is_not_blamed_when_public_dns_also_fails(self):
        for d in self.snap["probes"]["dns"]:
            d.update(ok=False, ms=None)
        self.assertNotIn("dns_router", self.findings())

    def test_slow_dns_and_isp_dns(self):
        self.snap["probes"]["dns"][0]["ms"] = 400
        self.snap["probes"]["dns"][1].update(ok=False, ms=None)
        k = self.findings()
        self.assertIn("dns_slow", k)
        self.assertIn("dns_isp", k)

    def test_tcp_blocked_only_when_ping_still_works(self):
        for t in self.snap["probes"]["tcp"]:
            t["ms"] = None
        self.assertIn("tcp_blocked", self.findings())
        for i in self.snap["internet"]:
            i["loss"] = 100.0
        self.assertNotIn("tcp_blocked", self.findings())

    def test_mtu_and_jitter_and_weak_host_wifi(self):
        self.snap["probes"]["mtu"] = 1492
        self.snap["probes"]["host_wifi"] = {"signal_dbm": -78, "snr_db": 15}
        self.snap["internet"][0]["jitter_ms"] = 80
        k = self.findings()
        self.assertEqual({"mtu_low", "host_wifi_weak", "jitter_internet"} - set(k), set())

    def test_instability_is_located_with_the_router_ping(self):
        self.snap["internet"][0]["loss"] = 30.0
        self.snap["probes"]["router_ping"] = {"target": "1.1.1.1", "loss": 0.0, "rtt_ms": 9.0}
        self.assertIn("between this computer and the router", self.findings()["internet_lossy"]["detail"])
        self.snap["probes"]["router_ping"] = {"target": "1.1.1.1", "loss": 40.0, "rtt_ms": 50.0}
        self.assertIn("upstream", self.findings()["internet_lossy"]["detail"])

    def test_slow_devices_crowded_24ghz_and_dhcp(self):
        self.snap["clients"][2].update(kind="wireless", mbps=2, age_s=10)
        [n for n in self.snap["nodes"] if n["id"] == "id-main"][0]["clients_24ghz"] = 14
        self.snap["dhcp"] = {"pool": 100, "leases": 90}
        k = self.findings()
        self.assertIn("slow_devices", k)
        self.assertIn("crowded_24:id-main", k)
        self.assertEqual(k["dhcp_full"]["severity"], "warning")

    def test_flapping_node(self):
        counts = {"Hall": 4, "Bedroom": 1}
        out = analyzer.flapping_findings(self.snap, lambda kind, name, since: counts.get(name, 0), 10_000)
        self.assertEqual([f["key"] for f in out], ["flapping:id-hall"])

    def test_node_going_offline_and_back_emits_events_with_subject(self):
        a = copy.deepcopy(self.snap)
        b = copy.deepcopy(self.snap)
        [n for n in b["nodes"] if n["id"] == "id-hall"][0]["reachable"] = False
        events = analyzer.diff_events(a, b)
        self.assertEqual([(e[1], e[3]) for e in events], [("node_offline", "Hall")])
        self.assertEqual([(e[1], e[3]) for e in analyzer.diff_events(b, a)], [("node_online", "Hall")])
