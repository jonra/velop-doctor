from dataclasses import replace

from ethtester.results import (
    DhcpResult,
    DnsResult,
    GatewayResult,
    InternetResult,
    LinkResult,
    LldpResult,
    Snapshot,
)


def ok_snapshot(**overrides) -> Snapshot:
    """A snapshot where every check passed; override fields to break one."""
    base = Snapshot(
        link=LinkResult(up=True, speed_mbps=1000, duplex="full", carrier_changes=2),
        flaps_last_minute=0,
        seconds_since_link=30.0,
        seconds_to_ip=2.1,
        lldp=LldpResult(switch_name="SW-Office", port="gi0/14", age_s=25),
        dhcp=DhcpResult(
            ip="192.168.1.57",
            prefix=24,
            gateway="192.168.1.1",
            dhcp_server="192.168.1.1",
            dns_servers=("192.168.1.1",),
        ),
        gateway=GatewayResult(reachable=True, rtt_ms=3.2, loss_pct=0.0),
        dns=DnsResult(ok=True, server="192.168.1.1"),
        internet=InternetResult(ok=True, public_ip="84.1.2.3"),
    )
    return replace(base, **overrides)
