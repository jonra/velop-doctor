import struct

from ethtester.checks.command import CheckError
from ethtester.checks.dns import build_query, check_dns, parse_response
from ethtester.checks.gateway import check_gateway, parse_ping
from ethtester.checks.internet import check_internet, parse_trace
from ethtester.results import DnsResult, GatewayResult, InternetResult

PING_OK = """PING 192.168.1.1 (192.168.1.1) from 192.168.1.57 eth0: 56(84) bytes of data.
64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=2.91 ms

--- 192.168.1.1 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 404ms
rtt min/avg/max/mdev = 2.512/3.204/4.011/0.612 ms
"""

PING_DEAD = """--- 192.168.1.1 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2050ms
"""

PING_LOSSY = """3 packets transmitted, 2 received, 33.3333% packet loss, time 405ms
rtt min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms
"""


def test_parse_ping():
    assert parse_ping(PING_OK) == GatewayResult(True, 3.204, 0.0)
    assert parse_ping(PING_DEAD) == GatewayResult(False, None, 100.0)
    assert parse_ping(PING_LOSSY) == GatewayResult(True, 2.0, 33.3333)
    assert parse_ping("") == GatewayResult(False, None, 100.0)


def test_check_gateway_command():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return PING_OK

    assert check_gateway("eth0", "192.168.1.1", 3, run=fake_run).reachable
    cmd, kwargs = calls[0]
    assert cmd == ["ping", "-I", "eth0", "-n", "-c", "3", "-W", "1", "-i", "0.2", "192.168.1.1"]
    assert kwargs["check"] is False


def test_build_query():
    expected = (bytes.fromhex("123401000001000000000000")
                + b"\x07example\x03com\x00" + b"\x00\x01\x00\x01")
    assert build_query("example.com", 0x1234) == expected


def response(qid: int, flags: int, answers: int) -> bytes:
    return struct.pack(">HHHHHH", qid, flags, 1, answers, 0, 0)


def test_parse_response():
    assert parse_response(response(7, 0x8180, 1), 7)
    assert not parse_response(response(7, 0x8183, 0), 7)   # NXDOMAIN
    assert not parse_response(response(8, 0x8180, 1), 7)   # wrong id
    assert not parse_response(response(7, 0x0100, 1), 7)   # not a response
    assert not parse_response(b"\x00\x07", 7)               # truncated


def test_check_dns():
    seen = []

    def ok_query(server, name, interface):
        seen.append((server, name, interface))
        return True

    def timeout_query(server, name, interface):
        raise TimeoutError()

    assert check_dns("eth0", ("192.168.1.1", "1.1.1.1"), "example.com", query=ok_query) \
        == DnsResult(True, "192.168.1.1")
    assert seen == [("192.168.1.1", "example.com", "eth0")]
    assert check_dns("eth0", ("9.9.9.9",), "example.com", query=timeout_query) \
        == DnsResult(False, "9.9.9.9")
    assert check_dns("eth0", (), "example.com", query=ok_query) == DnsResult(False, None)


def test_parse_trace():
    assert parse_trace("fl=1\nh=1.1.1.1\nip=84.1.2.3\nts=1\n") == "84.1.2.3"
    assert parse_trace("<html>hello</html>") is None


def test_check_internet():
    def ok_run(cmd, **kwargs):
        assert cmd[:3] == ["curl", "--interface", "eth0"]
        return "ip=84.1.2.3\n"

    def failing_run(cmd, **kwargs):
        raise CheckError("curl exited 7")

    url = "https://1.1.1.1/cdn-cgi/trace"
    assert check_internet("eth0", url, run=ok_run) == InternetResult(True, "84.1.2.3")
    assert check_internet("eth0", url, run=failing_run) == InternetResult(False, None)
