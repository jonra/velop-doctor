"""Ping the gateway through the tested interface."""

import re

from ..results import GatewayResult
from .command import run_command

LOSS_RE = re.compile(r"([\d.]+)% packet loss")
RTT_RE = re.compile(r"= [\d.]+/([\d.]+)/")


def parse_ping(raw: str) -> GatewayResult:
    loss_match = LOSS_RE.search(raw)
    loss = float(loss_match.group(1)) if loss_match else 100.0
    rtt_match = RTT_RE.search(raw)
    rtt = float(rtt_match.group(1)) if rtt_match else None
    return GatewayResult(reachable=loss < 100.0, rtt_ms=rtt, loss_pct=loss)


def check_gateway(interface: str, gateway: str, count: int, run=run_command) -> GatewayResult:
    cmd = ["ping", "-I", interface, "-n", "-c", str(count), "-W", "1", "-i", "0.2", gateway]
    return parse_ping(run(cmd, timeout=count + 5, check=False))
