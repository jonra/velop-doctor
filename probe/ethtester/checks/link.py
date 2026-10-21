"""Link state from the kernel's sysfs interface files."""

import re
from dataclasses import replace
from pathlib import Path

from ..results import LinkResult
from .command import CheckError, run_command

SYSFS = Path("/sys/class/net")


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def parse_link(carrier: str | None, speed: str | None, duplex: str | None,
               carrier_changes: str | None) -> LinkResult:
    up = carrier == "1"
    speed_mbps = None
    if up and speed and speed.lstrip("-").isdigit() and int(speed) > 0:
        speed_mbps = int(speed)
    return LinkResult(
        up=up,
        speed_mbps=speed_mbps,
        duplex=duplex if up and duplex in ("full", "half") else None,
        carrier_changes=int(carrier_changes) if carrier_changes and carrier_changes.isdigit() else 0,
    )


_SPEED_IN_MODE = re.compile(r"(\d+)base", re.IGNORECASE)  # 100baseT/Full -> 100
_FIELD_START = re.compile(r"\s*[A-Za-z][^:]*:")  # e.g. "Supported pause frame use:"


def parse_ethtool_max_speed(output: str) -> int | None:
    """Highest speed (Mbps) in the "Supported link modes" block of `ethtool <interface>`.

    Only that block counts: "Advertised" and "Link partner" modes describe what was
    offered, not what the adapter can do.
    """
    speeds: list[int] = []
    in_block = False
    for line in output.splitlines():
        marker = "Supported link modes:"
        if marker in line:
            in_block = True
            line = line.split(marker, 1)[1]
        elif not in_block:
            continue
        elif _FIELD_START.match(line):  # the next field ends the block
            break
        speeds += [int(m) for m in _SPEED_IN_MODE.findall(line)]
    return max(speeds) if speeds else None


def adapter_max_speed(interface: str, run=run_command) -> int | None:
    try:
        return parse_ethtool_max_speed(run(["ethtool", interface], timeout=3.0))
    except CheckError:  # ethtool missing, or the driver won't say
        return None


def check_link(interface: str, root: Path = SYSFS, run=run_command) -> LinkResult:
    base = root / interface
    link = parse_link(
        _read(base / "carrier"),
        _read(base / "speed"),
        _read(base / "duplex"),
        _read(base / "carrier_changes"),
    )
    if link.up and link.speed_mbps:
        link = replace(link, max_speed_mbps=adapter_max_speed(interface, run))
    return link
