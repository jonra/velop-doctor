"""Turns a snapshot into one verdict; the first matching rule wins."""

from dataclasses import dataclass

from .results import Snapshot

MAX_FLAPS = 2
MAX_LOSS_PCT = 20.0


@dataclass(frozen=True)
class Verdict:
    code: str
    text: str


def evaluate(s: Snapshot, expected_speed: int, dhcp_timeout: float) -> Verdict:
    if not s.link.up:
        return Verdict("NO_LINK", "NO LINK: cable/plug/port")
    if s.link.speed_mbps is not None and s.link.speed_mbps < expected_speed:
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
    return Verdict("OK", "OK")
