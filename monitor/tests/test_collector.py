import unittest

from velop_doctor.collector import collect
from velop_doctor.jnap import AuthError

from fakes import FakeClient, StubProbes, cfg, pinger_ok, world


def run(table=None, pinger=pinger_ok, now=1_792_000_000, probe=None):
    t = table or world()
    return collect(cfg(), "pw", client=lambda h, u, p: FakeClient(t, h), pinger=pinger, probe=probe or StubProbes(), now=lambda: now)


class CollectorTests(unittest.TestCase):
    def test_builds_nodes_and_membership(self):
        snap = run()
        by = {n["name"]: n for n in snap["nodes"]}
        self.assertEqual(by["Main"]["role"], "primary")
        self.assertTrue(by["Hall"]["in_mesh"])
        self.assertFalse(by["Old unit"]["in_mesh"], "registered but no backhaul entry = ghost")
        self.assertEqual(by["Hall"]["backhaul"]["mbps"], 1024)
        self.assertEqual(by["Bedroom"]["backhaul"]["mbps"], 100)

    def test_detects_out_of_sync_node(self):
        by = {n["name"]: n for n in run()["nodes"]}
        self.assertTrue(by["Hall"]["auth_ok"])
        self.assertIs(by["Bedroom"]["auth_ok"], False)

    def test_clients_get_node_and_readable_names(self):
        snap = run()
        names = {c["name"]: c for c in snap["clients"]}
        self.assertEqual(names["Jon iPhone"]["node"], "Main")
        self.assertEqual(names["Jon iPhone"]["dbm"], -62)
        uuid_named = [c for c in snap["clients"] if c["mac"] == "AA:00:00:00:00:02"][0]
        self.assertTrue(uuid_named["name"].startswith("iPhone ("), uuid_named["name"])
        self.assertEqual(uuid_named["node"], "Hall")
        self.assertEqual(snap["offline_known"], 1)

    def test_wan_and_steering(self):
        snap = run()
        self.assertTrue(snap["wan"]["double_nat"])
        self.assertIs(snap["steering"], False)
        self.assertEqual(snap["upstream"]["target"], "192.168.178.1")

    def test_primary_down_returns_early(self):
        snap = run({"unrelated": {}}, pinger=lambda ip, count=3: (100.0, None))
        self.assertFalse(snap["primary"]["reachable"])
        self.assertEqual(snap["nodes"], [])
        self.assertTrue(all(i["loss"] == 100 for i in snap["internet"]))

    def test_wrong_password_is_reported_not_raised(self):
        t = world()
        t["192.168.1.1"]["devicelist/GetDevices3"] = AuthError("_ErrorUnauthorized")
        snap = run(t)
        self.assertIs(snap["primary"]["auth_ok"], False)
        self.assertEqual(snap["nodes"], [])

    def test_node_that_does_not_answer_pings_is_marked_down(self):
        snap = run(pinger=lambda ip, count=3: (100.0, None) if ip == "192.168.1.96" else (0.0, 5.0))
        hall = [n for n in snap["nodes"] if n["name"] == "Hall"][0]
        self.assertIs(hall["reachable"], False)

    def test_probe_results_are_collected(self):
        t = world()
        t["192.168.1.1"]["router/GetWANStatus3"] = {"wanStatus": "Connected", "wanConnection": {
            "wanType": "DHCP", "ipAddress": "192.168.178.150", "gateway": "192.168.178.1",
            "dnsServer1": "84.116.46.23"}}
        snap = run(t, probe=StubProbes(mtu=1492, wifi={"signal_dbm": -60, "snr_db": 30}))
        pr = snap["probes"]
        self.assertEqual([d["label"] for d in pr["dns"]], ["router", "isp", "public"])
        self.assertEqual(pr["dns"][1]["server"], "84.116.46.23")
        self.assertTrue(all(d["ok"] for d in pr["dns"]))
        self.assertEqual(pr["mtu"], 1492)
        self.assertEqual(pr["router_ping"]["loss"], 0.0)
        self.assertEqual(snap["dhcp"], {"pool": 245, "leases": 40})

    def test_wifi_scan_never_overlaps_latency_measurements(self):
        """Regression: macOS scans for networks when asked for the link, wrecking pings."""
        import threading
        lock, state = threading.Lock(), {"pinging": 0, "overlap": False, "wifi_called": False}

        def pinger(ip, count=3):
            with lock:
                state["pinging"] += 1
                if state["wifi_called"]:
                    state["overlap"] = True
            import time
            time.sleep(0.02)
            with lock:
                state["pinging"] -= 1
            return 0.0, 5.0, 0.5

        class Probe(StubProbes):
            def host_wifi(self_):
                with lock:
                    state["wifi_called"] = True
                    state["overlap"] = state["overlap"] or state["pinging"] > 0
                return {"signal_dbm": -50, "snr_db": 40}
        snap = run(pinger=pinger, probe=Probe())
        self.assertFalse(state["overlap"], "host_wifi ran while a ping was in flight")
        self.assertEqual(snap["probes"]["host_wifi"]["signal_dbm"], -50)

    def test_isp_dns_is_skipped_when_the_wan_reports_none(self):
        snap = run()
        self.assertEqual([d["label"] for d in snap["probes"]["dns"]], ["router", "public"])

    def test_jitter_is_recorded_when_the_pinger_reports_it(self):
        snap = run(pinger=lambda ip, count=3: (0.0, 5.0, 1.5))
        self.assertEqual([n for n in snap["nodes"] if n["name"] == "Hall"][0]["jitter_ms"], 1.5)

    def test_node_in_mesh_without_an_address_is_offline(self):
        t = world()
        devices = {"devices": [dict(d) for d in t["192.168.1.1"]["devicelist/GetDevices3"]["devices"]]}
        for d in devices["devices"]:
            if d["deviceID"] == "uuid-hall":
                d["connections"] = []
        t["192.168.1.1"]["devicelist/GetDevices3"] = devices
        t["192.168.1.1"]["nodes/diagnostics/GetBackhaulInfo"] = {"backhaulDevices": [
            {"deviceUUID": "uuid-hall", "parentIPAddress": "192.168.1.1", "connectionType": "Wired",
             "speedMbps": "1024", "timestamp": "2026-10-20T14:45:16Z"}]}
        hall = [n for n in run(t)["nodes"] if n["name"] == "Hall"][0]
        self.assertTrue(hall["in_mesh"])
        self.assertIs(hall["reachable"], False)

    def test_backhaul_address_wins_over_a_stale_device_entry(self):
        t = world()
        devices = {"devices": [dict(d) for d in t["192.168.1.1"]["devicelist/GetDevices3"]["devices"]]}
        for d in devices["devices"]:
            if d["deviceID"] == "uuid-bed":
                d["connections"] = [{"macAddress": "x", "ipAddress": "10.208.1.1", "parentDeviceID": "uuid-main"}]
        t["192.168.1.1"]["devicelist/GetDevices3"] = devices
        bed = [n for n in run(t)["nodes"] if n["name"] == "Bedroom"][0]
        self.assertEqual(bed["ip"], "192.168.1.130")

    def test_client_placement_follows_the_device_list_not_old_signal_data(self):
        t = world()
        t["192.168.1.1"]["nodes/networkconnections/GetNodesWirelessNetworkConnections"] = {
            "nodeWirelessConnections": [{"deviceID": "uuid-main", "connections": [
                {"macAddress": "AA:00:00:00:00:02", "negotiatedMbps": 100,
                 "timestamp": "2020-01-01T00:00:00Z", "wireless": {"band": "2.4GHz", "signalDecibels": -70}}]}]}
        c = [c for c in run(t)["clients"] if c["mac"] == "AA:00:00:00:00:02"][0]
        self.assertEqual(c["node"], "Hall", "placement comes from the live device list")
        self.assertEqual(c["dbm"], -70)


if __name__ == "__main__":
    unittest.main()
