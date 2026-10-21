from ethtester.collector import Checks, Collector
from ethtester.config import Config
from ethtester.results import (
    DhcpResult,
    DnsResult,
    GatewayResult,
    InternetResult,
    LinkResult,
    LldpResult,
)

LEASE = DhcpResult(ip="192.168.1.57", prefix=24, gateway="192.168.1.1",
                   dns_servers=("192.168.1.1",))


class Fake:
    """Scriptable checks that record which ones were called."""

    def __init__(self):
        self.calls = []
        self.link_result = LinkResult(up=True, speed_mbps=1000, duplex="full", carrier_changes=2)
        self.lldp_result = LldpResult("SW", "1", age_s=3)
        self.dhcp_result = LEASE
        self.gateway_result = GatewayResult(True, 2.0, 0.0)
        self.dns_result = DnsResult(True, "192.168.1.1")
        self.internet_result = InternetResult(True, "84.1.2.3")
        self.reactivate_result = None

    def checks(self) -> Checks:
        def make(name):
            def fn(*args):
                self.calls.append((name, args))
                result = getattr(self, f"{name}_result")
                if isinstance(result, Exception):
                    raise result
                return result
            return fn
        return Checks(**{n: make(n) for n in ("link", "lldp", "dhcp", "gateway", "dns", "internet",
                                                   "reactivate")})

    def names(self):
        return [name for name, _ in self.calls]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(fake=None, clock=None):
    fake = fake or Fake()
    clock = clock or Clock()
    return fake, clock, Collector(Config(), fake.checks(), clock)


def test_link_down_skips_everything():
    fake, _, c = make()
    fake.link_result = LinkResult(up=False)
    s = c.collect()
    assert fake.names() == ["link"]
    assert s.dhcp is None and s.gateway is None


def test_no_ip_skips_reachability():
    fake, clock, c = make()
    fake.dhcp_result = DhcpResult()
    clock.now = 4.0
    s = c.collect()
    assert fake.names() == ["link", "lldp", "dhcp"]
    assert s.seconds_since_link == 0.0
    assert s.gateway is None


def test_full_pipeline():
    fake, _, c = make()
    s = c.collect()
    assert fake.names() == ["link", "lldp", "dhcp", "gateway", "dns", "internet"]
    assert ("gateway", ("eth0", "192.168.1.1", 3)) in fake.calls
    assert ("dns", ("eth0", ("192.168.1.1",), "example.com")) in fake.calls
    assert s.internet.public_ip == "84.1.2.3"


def test_gateway_failure_skips_dns():
    fake, _, c = make()
    fake.gateway_result = GatewayResult(False)
    s = c.collect()
    assert "dns" not in fake.names()
    assert s.dns is None


def test_dns_falls_back_to_gateway():
    fake, _, c = make()
    fake.dhcp_result = DhcpResult(ip="10.0.0.5", prefix=8, gateway="10.0.0.1")
    c.collect()
    assert ("dns", ("eth0", ("10.0.0.1",), "example.com")) in fake.calls


def test_check_exception_uses_fallback():
    fake, _, c = make()
    fake.dhcp_result = RuntimeError("nmcli broke")
    s = c.collect()
    assert s.dhcp == DhcpResult()
    assert "gateway" not in fake.names()


def test_stale_lldp_ignored_after_replug():
    fake, clock, c = make()
    fake.link_result = LinkResult(up=False)
    c.collect()
    fake.link_result = LinkResult(up=True, speed_mbps=1000)
    fake.lldp_result = LldpResult("OLD", "9", age_s=300)
    clock.now = 10.0
    assert c.collect().lldp is None


def test_lldp_kept_when_service_starts_on_live_link():
    fake, _, c = make()
    fake.lldp_result = LldpResult("SW", "1", age_s=300)
    assert c.collect().lldp.switch_name == "SW"


def test_lldp_given_up_after_60s():
    fake, clock, c = make()
    fake.lldp_result = None
    assert not c.collect().lldp_given_up
    clock.now = 61.0
    assert c.collect().lldp_given_up


def test_seconds_to_ip_and_flaps():
    fake, clock, c = make()
    fake.link_result = LinkResult(up=False, carrier_changes=3)
    c.collect()
    clock.now = 1.0
    fake.link_result = LinkResult(up=True, speed_mbps=1000, carrier_changes=4)
    fake.dhcp_result = DhcpResult()
    c.collect()
    clock.now = 3.5
    fake.dhcp_result = LEASE
    s = c.collect()
    assert s.seconds_to_ip == 2.5
    assert s.flaps_last_minute == 0


def test_observed_replug_reactivates_once():
    fake, clock, c = make()
    fake.link_result = LinkResult(up=False, carrier_changes=3)
    c.collect()
    clock.now = 1.0
    fake.link_result = LinkResult(up=True, speed_mbps=1000, carrier_changes=4)
    s = c.collect()
    assert fake.names() == ["link", "link", "reactivate", "lldp"]
    assert ("reactivate", ("eth0",)) in fake.calls
    assert s.dhcp == DhcpResult()
    clock.now = 2.0
    c.collect()
    assert fake.names().count("reactivate") == 1


def test_replug_between_ticks_resets_session():
    fake, clock, c = make()
    c.collect()
    assert "reactivate" not in fake.names()
    clock.now = 10.0
    fake.link_result = LinkResult(up=True, speed_mbps=1000, carrier_changes=4)
    s = c.collect()
    assert fake.names().count("reactivate") == 1
    assert s.seconds_since_link == 0.0
    assert s.dhcp == DhcpResult()
    assert s.gateway is None
    clock.now = 12.0
    s = c.collect()
    assert s.dhcp == LEASE
    assert s.seconds_since_link == 2.0
    assert s.seconds_to_ip == 2.0


def test_live_link_at_start_does_not_reactivate():
    fake, clock, c = make()
    c.collect()
    clock.now = 5.0
    c.collect()
    assert "reactivate" not in fake.names()


def test_reactivate_failure_is_contained():
    fake, clock, c = make()
    fake.link_result = LinkResult(up=False)
    c.collect()
    fake.link_result = LinkResult(up=True, speed_mbps=1000)
    fake.reactivate_result = RuntimeError("nmcli broke")
    clock.now = 1.0
    assert c.collect().link.up


def test_link_check_exception_skips_flaps_and_session():
    fake, clock, c = make()
    fake.link_result = RuntimeError("sysfs broke")
    s = c.collect()
    assert s.link == LinkResult(up=False)
    clock.now = 1.0
    fake.link_result = LinkResult(up=True, speed_mbps=1000, carrier_changes=10)
    c.collect()
    clock.now = 2.0
    s = c.collect()
    assert s.flaps_last_minute == 0
    assert "reactivate" not in fake.names()


def test_link_check_exception_keeps_plug_session():
    fake, clock, c = make()
    c.collect()
    clock.now = 1.0
    fake.link_result = RuntimeError("sysfs broke")
    c.collect()
    clock.now = 2.0
    fake.link_result = LinkResult(up=True, speed_mbps=1000, carrier_changes=2)
    s = c.collect()
    assert s.seconds_since_link == 2.0
    assert "reactivate" not in fake.names()
