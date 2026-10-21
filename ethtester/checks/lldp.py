"""Switch name and port from lldpd's neighbour table."""

import json
import re

from ..results import LldpResult
from .command import run_command

AGE_RE = re.compile(r"(\d+) days?, (\d+):(\d+):(\d+)")


def _age_seconds(age: str | None) -> int | None:
    m = AGE_RE.match(age or "")
    if not m:
        return None
    days, hours, minutes, seconds = (int(g) for g in m.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def parse_lldp(raw: str, interface: str) -> LldpResult | None:
    ifaces = json.loads(raw).get("lldp", {})
    ifaces = ifaces.get("interface", {}) if isinstance(ifaces, dict) else {}
    if isinstance(ifaces, list):
        merged: dict = {}
        for item in ifaces:
            merged.update(item)
        ifaces = merged
    entry = ifaces.get(interface)
    if not entry:
        return None

    chassis = entry.get("chassis", {})
    if isinstance(chassis.get("id"), dict) and "value" in chassis["id"]:
        name = chassis["id"]["value"]
    else:
        name = next(iter(chassis), None)

    port = entry.get("port", {})
    port_id = port.get("id", {})
    if port_id.get("type") == "ifname":
        port_name = port_id.get("value")
    else:
        port_name = port.get("descr") or port_id.get("value")

    return LldpResult(switch_name=name, port=port_name, age_s=_age_seconds(entry.get("age")))


def check_lldp(interface: str, run=run_command) -> LldpResult | None:
    return parse_lldp(run(["lldpcli", "-f", "json", "show", "neighbors", "ports", interface]), interface)
