"""Configuration and password storage.

The admin password is never written to the config file. Lookup order:
  1. VELOP_PASSWORD environment variable
  2. macOS Keychain (service "velop-doctor")
  3. a 0600 file in the config directory (Linux and other platforms)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

KEYCHAIN_SERVICE = "velop-doctor"


def home_dir():
    return Path(os.environ.get("VELOP_DOCTOR_HOME") or Path.home() / ".velop-doctor")


@dataclass
class Config:
    primary: str = ""                 # IP of the main (router) node
    username: str = "admin"
    interval: int = 60                # seconds between polls
    port: int = 8765
    ping_targets: list = field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    weak_dbm: int = -80               # clients at or below this are "weak"
    weak_seconds: int = 300           # ...for at least this long
    idle_node_seconds: int = 900      # a node with 0 clients this long is flagged
    stale_client_seconds: int = 600   # wireless entries older than this are ignored
    retention_days: int = 7

    @property
    def db_path(self):
        return home_dir() / "velop.db"


def config_path():
    return home_dir() / "config.json"


def load():
    cfg = Config()
    try:
        data = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return cfg
    known = {f.name for f in fields(Config)}
    for key, value in data.items():
        if key in known:
            setattr(cfg, key, value)
    return cfg


def save(cfg):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2) + "\n")


# --- password storage -------------------------------------------------------

def _account(cfg):
    return f"{cfg.username}@{cfg.primary}"


def _secret_file():
    return home_dir() / "secret"


def get_password(cfg):
    env = os.environ.get("VELOP_PASSWORD")
    if env:
        return env
    if sys.platform == "darwin":
        p = subprocess.run(
            ["security", "find-generic-password", "-a", _account(cfg),
             "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout.rstrip("\n")
    try:
        return _secret_file().read_text().rstrip("\n")
    except OSError:
        return None


def _security_quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_password(cfg, password):
    """Store the password; returns a short description of where it went."""
    if sys.platform == "darwin":
        # `security -i` reads the command from stdin, so the password never
        # appears in the process list.
        cmd = (f"add-generic-password -U -a {_security_quote(_account(cfg))} "
               f"-s {KEYCHAIN_SERVICE} -w {_security_quote(password)}\n")
        p = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True)
        if p.returncode == 0:
            return "macOS Keychain"
    path = _secret_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(password + "\n")
    return f"file {path} (mode 600)"
