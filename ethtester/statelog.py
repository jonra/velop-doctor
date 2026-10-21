"""Appends a JSON line whenever the meaningful state changes (not on ping jitter)."""

import json
import logging
from dataclasses import asdict
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .results import Snapshot
from .verdict import Verdict


def fingerprint(s: Snapshot, v: Verdict) -> tuple:
    return (
        v.code,
        s.link.up,
        s.link.speed_mbps,
        s.link.duplex,
        s.dhcp.ip if s.dhcp else None,
        (s.lldp.switch_name, s.lldp.port) if s.lldp else None,
        s.internet.public_ip if s.internet else None,
    )


class StateLogger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger = logging.getLogger(f"ethtester.state.{path}")
        self.logger.handlers[:] = [handler]
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self._last: tuple | None = None

    def record(self, s: Snapshot, v: Verdict, wall_time: float) -> bool:
        fp = fingerprint(s, v)
        if fp == self._last:
            return False
        self._last = fp
        self.logger.info(json.dumps({
            "ts": datetime.fromtimestamp(wall_time).isoformat(timespec="seconds"),
            "verdict": v.code,
            "text": v.text,
            "snapshot": asdict(s),
        }))
        return True
