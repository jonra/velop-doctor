from ethtester.results import (
    DhcpResult,
    DnsResult,
    GatewayResult,
    InternetResult,
    LinkResult,
    Snapshot,
)
from ethtester.verdict import evaluate
from tests.factories import ok_snapshot


def code(s: Snapshot) -> str:
    return evaluate(s, expected_speed=1000, dhcp_timeout=20).code


def test_all_good():
    v = evaluate(ok_snapshot(), 1000, 20)
    assert (v.code, v.text) == ("OK", "OK")


def test_no_link():
    v = evaluate(Snapshot(link=LinkResult(up=False)), 1000, 20)
    assert (v.code, v.text) == ("NO_LINK", "NO LINK: cable/plug/port")


def test_slow_link():
    s = ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full"))
    v = evaluate(s, 1000, 20)
    assert (v.code, v.text) == ("SLOW_LINK", "CABLE? (100M)")


def test_slow_link_ok_when_expected_is_100():
    s = ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full"))
    assert evaluate(s, 100, 20).code == "OK"


def test_unknown_speed_is_not_slow():
    assert code(ok_snapshot(link=LinkResult(up=True, speed_mbps=None))) == "OK"


def test_flapping_threshold():
    assert code(ok_snapshot(flaps_last_minute=2)) == "OK"
    v = evaluate(ok_snapshot(flaps_last_minute=3), 1000, 20)
    assert (v.code, v.text) == ("FLAPPING", "FLAPPING: loose plug?")


def test_waiting_for_dhcp_then_no_dhcp():
    waiting = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=5.0,
                          gateway=None, dns=None, internet=None)
    v = evaluate(waiting, 1000, 20)
    assert (v.code, v.text) == ("WAIT_DHCP", "WAITING FOR DHCP…")
    late = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=25.0,
                       gateway=None, dns=None, internet=None)
    v = evaluate(late, 1000, 20)
    assert (v.code, v.text) == ("NO_DHCP", "NO DHCP: router/VLAN")


def test_gateway_failures():
    unreachable = ok_snapshot(gateway=GatewayResult(reachable=False), dns=None, internet=None)
    v = evaluate(unreachable, 1000, 20)
    assert (v.code, v.text) == ("GATEWAY", "GATEWAY: router/VLAN")
    lossy = ok_snapshot(gateway=GatewayResult(reachable=True, rtt_ms=5, loss_pct=33.3))
    assert code(lossy) == "GATEWAY"
    borderline = ok_snapshot(gateway=GatewayResult(reachable=True, rtt_ms=5, loss_pct=20.0))
    assert code(borderline) == "OK"


def test_dns_failure():
    v = evaluate(ok_snapshot(dns=DnsResult(ok=False), internet=None), 1000, 20)
    assert (v.code, v.text) == ("DNS", "DNS")


def test_internet_failure():
    v = evaluate(ok_snapshot(internet=InternetResult(ok=False)), 1000, 20)
    assert (v.code, v.text) == ("NO_INTERNET", "NO INTERNET: uplink")


def test_first_rule_wins():
    s = ok_snapshot(link=LinkResult(up=True, speed_mbps=100), flaps_last_minute=5,
                    dhcp=DhcpResult(), seconds_since_link=60.0)
    assert code(s) == "SLOW_LINK"
