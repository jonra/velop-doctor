"""Plain data returned by the checks. None in a Snapshot field means the check was skipped."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LinkResult:
    up: bool
    speed_mbps: int | None = None
    duplex: str | None = None
    carrier_changes: int = 0


@dataclass(frozen=True)
class LldpResult:
    switch_name: str | None = None
    port: str | None = None
    age_s: int | None = None


@dataclass(frozen=True)
class DhcpResult:
    ip: str | None = None
    prefix: int | None = None
    gateway: str | None = None
    dhcp_server: str | None = None
    dns_servers: tuple[str, ...] = ()


@dataclass(frozen=True)
class GatewayResult:
    reachable: bool
    rtt_ms: float | None = None
    loss_pct: float = 100.0


@dataclass(frozen=True)
class DnsResult:
    ok: bool
    server: str | None = None


@dataclass(frozen=True)
class InternetResult:
    ok: bool
    public_ip: str | None = None


@dataclass(frozen=True)
class Snapshot:
    link: LinkResult
    flaps_last_minute: int = 0
    seconds_since_link: float | None = None
    seconds_to_ip: float | None = None
    lldp: LldpResult | None = None
    lldp_given_up: bool = False
    dhcp: DhcpResult | None = None
    gateway: GatewayResult | None = None
    dns: DnsResult | None = None
    internet: InternetResult | None = None
