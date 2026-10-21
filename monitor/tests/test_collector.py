import unittest

from velop_doctor.collector import collect
from velop_doctor.jnap import AuthError

from fakes import FakeClient, cfg, pinger_ok, world


def run(table=None, pinger=pinger_ok, now=1_792_000_000):
    t = table or world()
    return collect(cfg(), "pw", client=lambda h, u, p: FakeClient(t, h), pinger=pinger,
                   now=lambda: now)


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


if __name__ == "__main__":
    unittest.main()
