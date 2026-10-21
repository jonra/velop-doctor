import unittest

from velop_doctor import diagnostics as d

SERVERS = {"router": "192.168.1.1", "isp": "84.116.46.23", "public": "1.1.1.1"}


class HostAndCountTests(unittest.TestCase):
    def test_valid_hosts(self):
        for host in ("1.1.1.1", "192.168.1.1", "example.com", "a-b.example.co.uk", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(d.valid_host(host), host)

    def test_rejected_hosts(self):
        bad = ["", "   ", None, "-c 1000", "--help", "-f", "a b", "a;b", "$(reboot)", "`id`", "a|b",
               "x&y", "::1", "fe80::1", "example..com", ".example.com", "a" * 64 + ".com", "h" * 300,
               "http://x", "x/y", "999.1.1.1/8"]
        for host in bad:
            with self.subTest(host=host), self.assertRaises(d.DiagnosticError):
                d.valid_host(host)

    def test_count_bounds_and_types(self):
        self.assertEqual((d.valid_count(None), d.valid_count(1), d.valid_count(20)), (5, 1, 20))
        for bad in (0, 21, -1, 2.5, "5", True):
            with self.subTest(count=bad), self.assertRaises(d.DiagnosticError):
                d.valid_count(bad)


class PingTests(unittest.TestCase):
    def test_ping_from_both_places(self):
        out = d.run_ping({"host": "1.1.1.1", "count": 3},
                         pinger=lambda host, count: (0.0, 12.5, 1.0),
                         router_ping=lambda host, count: {"target": host, "loss": 10.0, "rtt_ms": 9.0})
        self.assertEqual((out["host"], out["count"]), ("1.1.1.1", 3))
        self.assertEqual(out["computer"], {"loss_pct": 0.0, "avg_ms": 12.5, "jitter_ms": 1.0})
        self.assertEqual(out["router"], {"loss_pct": 10.0, "avg_ms": 9.0})

    def test_ping_only_where_asked(self):
        calls = []
        out = d.run_ping({"host": "8.8.8.8", "from": "computer"},
                         pinger=lambda host, count: calls.append("pc") or (0.0, 5.0, 0.5),
                         router_ping=lambda host, count: calls.append("router"))
        self.assertEqual(calls, ["pc"])
        self.assertNotIn("router", out)

    def test_router_that_cannot_ping_is_explained_not_fatal(self):
        out = d.run_ping({"host": "8.8.8.8"}, pinger=lambda h, c: (0.0, 5.0, 0.5),
                         router_ping=lambda host, count: None)
        self.assertIsNone(out["router"])
        self.assertIn("router", out["router_note"].lower())

    def test_ping_from_router_without_a_router_is_an_error(self):
        with self.assertRaises(d.DiagnosticError):
            d.run_ping({"host": "8.8.8.8", "from": "router"}, router_ping=None)

    def test_bad_arguments_are_rejected_before_anything_runs(self):
        ran = []
        for params in ({"host": "1.1.1.1", "from": "mars"}, {"host": "-c 99999"}, {}):
            with self.subTest(params=params), self.assertRaises(d.DiagnosticError):
                d.run_ping(params, pinger=lambda *a: ran.append(a), router_ping=lambda *a: ran.append(a))
        self.assertEqual(ran, [])


class DnsTests(unittest.TestCase):
    def test_aliases_and_literals(self):
        seen = []
        probe = lambda server, name: seen.append((server, name)) or {"ok": True, "ms": 21.0}
        self.assertEqual(d.run_dns({"name": "example.com"}, SERVERS, dns_probe=probe)["server"], "192.168.1.1")
        self.assertEqual(d.run_dns({"name": "example.com", "server": "isp"}, SERVERS, dns_probe=probe)["server"],
                         "84.116.46.23")
        r = d.run_dns({"name": "example.com", "server": "9.9.9.9"}, SERVERS, dns_probe=probe)
        self.assertEqual(r, {"name": "example.com", "server": "9.9.9.9", "resolved": True, "ms": 21.0})
        self.assertEqual(seen[-1], ("9.9.9.9", "example.com"))

    def test_rejects_unknown_alias_bad_server_and_bad_name(self):
        probe = lambda *a: {"ok": True, "ms": 1}
        cases = (({"name": "x.com", "server": "cloud"}, SERVERS), ({"name": "x.com", "server": "a;b"}, SERVERS),
                 ({"name": "x.com", "server": "isp"}, {}), ({"name": "-x"}, SERVERS))
        for params, servers in cases:
            with self.subTest(params=params), self.assertRaises(d.DiagnosticError):
                d.run_dns(params, servers, dns_probe=probe)

    def test_dispatch_and_known_servers(self):
        with self.assertRaises(d.DiagnosticError):
            d.diagnose("rm", {}, None)
        snap = {"probes": {"dns": [{"label": "router", "server": "192.168.1.1", "ok": True, "ms": 5}]}}
        self.assertEqual(d.known_dns_servers(snap), {"router": "192.168.1.1"})
        self.assertEqual(d.known_dns_servers(None), {})


if __name__ == "__main__":
    unittest.main()
