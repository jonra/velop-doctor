"""Settings loaded from ethtester.toml on the SD card's boot partition."""

import logging
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("/boot/firmware/ethtester.toml")
DISPLAYS = ("V3", "V4", "png")


@dataclass(frozen=True)
class Config:
    interface: str = "eth0"
    display: str = "V4"
    expected_speed: int = 1000
    interval: float = 5
    dhcp_timeout: float = 20
    ping_count: int = 3
    dns_test_name: str = "example.com"
    internet_url: str = "https://1.1.1.1/cdn-cgi/trace"
    png_path: str = "/tmp/ethtester.png"
    log_path: str = "/var/log/ethtester/ethtester.log"


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    names = {f.name for f in fields(Config)}
    for key in data.keys() - names:
        log.warning("ignoring unknown config key %r", key)
    cfg = Config(**{k: v for k, v in data.items() if k in names})
    if cfg.display not in DISPLAYS:
        raise ValueError(f"display must be one of {DISPLAYS}, got {cfg.display!r}")
    return cfg
