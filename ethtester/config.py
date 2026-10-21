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


# (low, strict): strict means the value must be above low, otherwise at least low.
LIMITS = {"interval": (0, True), "dhcp_timeout": (0, True),
          "ping_count": (1, False), "expected_speed": (1, False)}


def _coerce(name: str, kind: type, value):
    try:
        if isinstance(value, (bool, dict, list)):
            raise TypeError
        if kind is int:
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            return int(value)
        if kind is float:
            return float(value)
        if not isinstance(value, (str, int, float)):
            raise TypeError
        return str(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be {kind.__name__}, got {value!r}") from None


def _check_range(cfg: Config) -> None:
    for name, (low, strict) in LIMITS.items():
        value = getattr(cfg, name)
        ok = value > low if strict else value >= low  # also rejects NaN
        if not ok:
            raise ValueError(f"{name} must be {'>' if strict else '>='} {low}, got {value!r}")


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML: {exc}") from None
    types = {f.name: f.type for f in fields(Config)}
    for key in data.keys() - types.keys():
        log.warning("ignoring unknown config key %r", key)
    cfg = Config(**{k: _coerce(k, types[k], v) for k, v in data.items() if k in types})
    if cfg.display not in DISPLAYS:
        raise ValueError(f"display must be one of {DISPLAYS}, got {cfg.display!r}")
    _check_range(cfg)
    return cfg
