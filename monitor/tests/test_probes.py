import socket
import struct
import threading
import unittest

from velop_doctor import probes

MACOS_SAMPLE = """
        Current Network Information:
          <redacted>:
            PHY Mode: 802.11ac
            Channel: 100 (5GHz, 80MHz)
            Country Code: NL
            Network Type: Infrastructure
            Security: WPA2 Personal
            Signal / Noise: -53 dBm / -94 dBm
            Transmit Rate: 702
            MCS Index: 7
        Other Local Wi-Fi Networks:
          <redacted>:
            Channel: 7 (2GHz, 40MHz)
            Signal / Noise: -40 dBm / -90 dBm
"""


class FakeDns(threading.Thread):
    """UDP server answering every query: ok, empty (no answer) or garbage."""

    def __init__(self, mode):
        super().__init__(daemon=True)
        self.mode = mode
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]

    def run(self):
        data, addr = self.sock.recvfrom(512)
        if self.mode == "silent":
            return
        qid = data[:2]
        if self.mode == "ok":
            reply = qid + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0) + data[12:] + b"\xc0\x0c" + b"\x00" * 10
        elif self.mode == "nxdomain":
            reply = qid + struct.pack(">HHHHH", 0x8183, 1, 0, 0, 0) + data[12:]
        else:
            reply = b"junk"
        self.sock.sendto(reply, addr)


class DnsProbeTests(unittest.TestCase):
    def probe(self, mode, timeout=1.0):
        srv = FakeDns(mode)
        srv.start()
        orig = socket.socket
        # point the probe at the fake server's port instead of 53
        real_sendto = socket.socket.sendto

        class Redirect(orig):
            def sendto(self, data, addr):
                return real_sendto(self, data, (addr[0], srv.port))
        socket.socket = Redirect
        try:
            return probes.dns_probe("127.0.0.1", timeout=timeout)
        finally:
            socket.socket = orig
            srv.sock.close()

    def test_answer_is_ok_and_timed(self):
        r = self.probe("ok")
        self.assertTrue(r["ok"])
        self.assertLess(r["ms"], 500)

    def test_nxdomain_or_garbage_or_silence_is_a_failure(self):
        for mode in ("nxdomain", "junk", "silent"):
            self.assertEqual(self.probe(mode, timeout=0.5), {"ok": False, "ms": None}, mode)


class TcpAndPingTests(unittest.TestCase):
    def test_tcp_connect_time_and_failure(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        self.assertIsNotNone(probes.tcp_probe("127.0.0.1", port, timeout=1))
        srv.close()
        self.assertIsNone(probes.tcp_probe("127.0.0.1", port, timeout=0.5))

    def test_ping_localhost_reports_no_loss_and_jitter(self):
        loss, avg, jitter = probes.ping_host("127.0.0.1", count=3)
        self.assertEqual(loss, 0.0)
        self.assertIsNotNone(avg)
        self.assertIsNotNone(jitter)


class HostWifiParseTests(unittest.TestCase):
    def test_macos_output(self):
        w = probes.parse_macos_airport(MACOS_SAMPLE)
        self.assertEqual((w["signal_dbm"], w["noise_dbm"], w["snr_db"]), (-53, -94, 41))
        self.assertEqual((w["channel"], w["band"], w["rate_mbps"], w["phy"]), (100, "5GHz", 702, "802.11ac"))

    def test_not_on_wifi(self):
        self.assertIsNone(probes.parse_macos_airport("Status: Off"))


class FakeRouter:
    def __init__(self, status_log, running_polls=1):
        self.log, self.polls, self.calls = status_log, running_polls, []

    def call(self, action, payload=None, **k):
        self.calls.append(action)
        if action.endswith("GetPingStatus"):
            self.polls -= 1
            return {"isRunning": self.polls > 0, "pingLog": self.log if self.polls <= 0 else ""}
        return {}


class RouterProbeTests(unittest.TestCase):
    def test_router_ping_parses_loss_and_average(self):
        log = "4 packets transmitted, 3 packets received, 25% packet loss\nround-trip min/avg/max = 7.5/10.5/15.1 ms\n"
        r = probes.router_ping(FakeRouter(log), "1.1.1.1", wait=3)
        self.assertEqual(r, {"target": "1.1.1.1", "loss": 25.0, "rtt_ms": 10.5})

    def test_unsupported_firmware_gives_none(self):
        from velop_doctor.jnap import JnapError

        class Old:
            def call(self, *a, **k):
                raise JnapError("_ErrorUnknownAction")
        self.assertIsNone(probes.router_ping(Old()))

    def test_dhcp_usage(self):
        class R:
            def call(self, action, payload=None, **k):
                if action.endswith("GetLANSettings"):
                    return {"dhcpSettings": {"firstClientIPAddress": "192.168.1.10", "lastClientIPAddress": "192.168.1.254"}}
                return {"leases": [{}] * 39}
        self.assertEqual(probes.dhcp_usage(R()), {"pool": 245, "leases": 39})


if __name__ == "__main__":
    unittest.main()


class HostWifiCacheTests(unittest.TestCase):
    def test_the_scan_runs_at_most_once_per_cache_window(self):
        calls = []
        orig = probes._host_wifi_uncached
        probes._host_wifi_uncached = lambda: calls.append(1) or {"signal_dbm": -50}
        probes._wifi_cache.update(at=0.0, value=None)
        try:
            for _ in range(5):
                self.assertEqual(probes.host_wifi()["signal_dbm"], -50)
            self.assertEqual(len(calls), 1, "every uncached call makes macOS scan and disturbs traffic")
            probes.host_wifi(max_age=0)
            self.assertEqual(len(calls), 2)
        finally:
            probes._host_wifi_uncached = orig
            probes._wifi_cache.update(at=0.0, value=None)
