"""Runs the checks in order and builds a Snapshot; later checks are skipped once one fails."""

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .checks.dhcp import check_dhcp, reactivate
from .checks.dns import check_dns
from .checks.gateway import check_gateway
from .checks.internet import check_internet
from .checks.link import check_link
from .checks.lldp import check_lldp
from .config import Config
from .results import DhcpResult, DnsResult, GatewayResult, InternetResult, LinkResult, Snapshot
from .tracking import FlapTracker, PlugSession
from .verdict import MAX_LOSS_PCT

log = logging.getLogger(__name__)

LLDP_GIVE_UP_S = 60
LLDP_AGE_SLACK_S = 5


@dataclass
class Checks:
    link: Callable = check_link
    lldp: Callable = check_lldp
    dhcp: Callable = check_dhcp
    gateway: Callable = check_gateway
    dns: Callable = check_dns
    internet: Callable = check_internet
    reactivate: Callable = reactivate


def _safe(fn, fallback, *args):
    try:
        return fn(*args)
    except Exception:
        log.exception("check %s failed", getattr(fn, "__name__", fn))
        return fallback


class Collector:
    def __init__(self, cfg: Config, checks: Checks | None = None, clock=time.monotonic):
        self.cfg = cfg
        self.checks = checks or Checks()
        self.clock = clock
        self.session = PlugSession()
        self.flaps = FlapTracker()

    def collect(self) -> Snapshot:
        cfg, checks, now = self.cfg, self.checks, self.clock()

        try:
            link = checks.link(cfg.interface)
        except Exception:
            # Don't feed a made-up reading into the flap window or the plug session.
            log.exception("check link failed")
            return Snapshot(link=LinkResult(up=False))
        flaps = self.flaps.update(now, link.carrier_changes)
        new_plug = self.session.on_link(now, link.up, link.carrier_changes)
        if not link.up:
            return Snapshot(link=link, flaps_last_minute=flaps)

        if new_plug:
            _safe(checks.reactivate, None, cfg.interface)
        since = self.session.seconds_since_link(now)
        lldp = _safe(checks.lldp, None, cfg.interface)
        if (lldp is not None and self.session.fresh and lldp.age_s is not None
                and lldp.age_s > since + LLDP_AGE_SLACK_S):
            lldp = None

        if new_plug:
            dhcp = DhcpResult()  # any lease now is the previous port's; wait for the new one
        else:
            dhcp = _safe(checks.dhcp, DhcpResult(), cfg.interface)
            self.session.on_ip(now, dhcp.ip is not None)
        base = dict(
            link=link,
            flaps_last_minute=flaps,
            seconds_since_link=since,
            seconds_to_ip=self.session.seconds_to_ip,
            lldp=lldp,
            lldp_given_up=lldp is None and since > LLDP_GIVE_UP_S,
            dhcp=dhcp,
        )
        if dhcp.ip is None:
            return Snapshot(**base)

        gateway = GatewayResult(reachable=False)
        if dhcp.gateway:
            gateway = _safe(checks.gateway, gateway, cfg.interface, dhcp.gateway, cfg.ping_count)
        if not gateway.reachable or gateway.loss_pct > MAX_LOSS_PCT:
            return Snapshot(**base, gateway=gateway)

        servers = dhcp.dns_servers or ((dhcp.gateway,) if dhcp.gateway else ())
        dns = _safe(checks.dns, DnsResult(ok=False), cfg.interface, servers, cfg.dns_test_name)
        if not dns.ok:
            return Snapshot(**base, gateway=gateway, dns=dns)

        internet = _safe(checks.internet, InternetResult(ok=False), cfg.interface, cfg.internet_url)
        return Snapshot(**base, gateway=gateway, dns=dns, internet=internet)
