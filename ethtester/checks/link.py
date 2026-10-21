"""Link state from the kernel's sysfs interface files."""

from pathlib import Path

from ..results import LinkResult

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


def check_link(interface: str, root: Path = SYSFS) -> LinkResult:
    base = root / interface
    return parse_link(
        _read(base / "carrier"),
        _read(base / "speed"),
        _read(base / "duplex"),
        _read(base / "carrier_changes"),
    )
