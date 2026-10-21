"""Turns a snapshot into one verdict; the first matching rule wins."""

from dataclasses import dataclass

from .results import LinkResult, Snapshot

GIGABIT = 1000
MAX_FLAPS = 2
MAX_LOSS_PCT = 20.0


@dataclass(frozen=True)
class Verdict:
    code: str
    text: str


def required_speed(link: LinkResult, expected_speed: int) -> int:
    """The speed this link should reach: what the user expects, capped by what the adapter can do.

    A 10/100 USB dongle never reaches 1000M on any cable, so that is not the cable's fault.
    """
    if link.max_speed_mbps:
        return min(expected_speed, link.max_speed_mbps)
    return expected_speed


def adapter_limited(link: LinkResult) -> bool:
    """True when the link runs at the adapter's ceiling and that ceiling is below gigabit."""
    return (link.up and link.speed_mbps is not None and link.max_speed_mbps is not None
            and link.speed_mbps >= link.max_speed_mbps and link.max_speed_mbps < GIGABIT)


def evaluate(s: Snapshot, expected_speed: int, dhcp_timeout: float) -> Verdict:
    if not s.link.up:
        return Verdict("NO_LINK", "NO LINK: cable/plug/port")
    if s.link.speed_mbps is not None and s.link.speed_mbps < required_speed(s.link, expected_speed):
        return Verdict("SLOW_LINK", f"CABLE? ({s.link.speed_mbps}M)")
    if s.flaps_last_minute > MAX_FLAPS:
        return Verdict("FLAPPING", "FLAPPING: loose plug?")
    if s.dhcp is None or s.dhcp.ip is None:
        if s.seconds_since_link is not None and s.seconds_since_link < dhcp_timeout:
            return Verdict("WAIT_DHCP", "WAITING FOR DHCP…")
        return Verdict("NO_DHCP", "NO DHCP: router/VLAN")
    if s.gateway is None or not s.gateway.reachable or s.gateway.loss_pct > MAX_LOSS_PCT:
        return Verdict("GATEWAY", "GATEWAY: router/VLAN")
    if s.dns is None or not s.dns.ok:
        return Verdict("DNS", "DNS")
    if s.internet is None or not s.internet.ok:
        return Verdict("NO_INTERNET", "NO INTERNET: uplink")
    if adapter_limited(s.link):
        return Verdict("OK", f"OK (adapter {s.link.speed_mbps}M)")
    return Verdict("OK", "OK")
