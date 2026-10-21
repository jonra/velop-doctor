# Ethernet Tester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python service for a Pi Zero with a USB Ethernet dongle and a Waveshare 2.13" e-ink HAT. It shows link, switch port, DHCP, gateway, DNS and internet status, plus a verdict that points to plug/cable, port, or router.

**Architecture:** A systemd service runs a loop. Each tick it collects a `Snapshot` from small check modules (sysfs, `lldpcli`, `nmcli`, `ping`, a raw DNS query and `curl`, all bound to `eth0`). A pure `evaluate()` function turns the snapshot into a `Verdict`. A pure renderer draws rows plus the verdict into a 250×122 one-bit Pillow image. A `Display` sends the image to the Waveshare driver (or to a PNG file during development) using partial or full refresh. State changes are logged as JSON lines.

**Tech Stack:** Python 3.11+ (standard library: `tomllib`, `subprocess`, `socket`, `dataclasses`), Pillow, pytest, Waveshare `epd2in13_V3`/`epd2in13_V4` drivers (vendored), lldpd, NetworkManager, systemd. Target OS: Raspberry Pi OS Lite (Bookworm).

**Spec:** `docs/superpowers/specs/2026-10-21-ethernet-tester-design.md`

## Global Constraints

- Python: code must run on Python 3.11 (Bookworm's version). Use no syntax or library features newer than 3.11.
- Pillow: on the Pi it is Debian's `python3-pil` 9.4. Don't use `ImageFont.load_default(size=...)` (added in 10.1).
- Screen: 250×122 pixels, mode `"1"`, white background (255), black ink (0).
- Default interface: `eth0`. Every network check must be bound to the interface (`ping -I`, `curl --interface`, `SO_BINDTODEVICE`), so Wi-Fi can never satisfy a check.
- Verdict texts, copied exactly: `NO LINK: cable/plug/port`, `CABLE? (<speed>M)`, `FLAPPING: loose plug?`, `WAITING FOR DHCP…`, `NO DHCP: router/VLAN`, `GATEWAY: router/VLAN`, `DNS`, `NO INTERNET: uplink`, `OK`.
- Thresholds: flapping means more than 2 flaps in 60 s; gateway failure means loss above 20%; `dhcp_timeout` defaults to 20 s; LLDP is given up after 60 s.
- Refresh: a full refresh happens at startup, when the verdict code changes, and after every 20 partial refreshes.
- Config file: `/boot/firmware/ethtester.toml`. Log file: `/var/log/ethtester/ethtester.log`, rotated at 1 MB with 5 backups.
- A failing check must never crash the loop.
- Development on the Mac: `python3 -m venv .venv && .venv/bin/pip install pillow pytest`. Run tests with `.venv/bin/pytest`.

## File Structure

```
ethtester/
  __init__.py            version
  __main__.py            `python -m ethtester` entry
  config.py              Config dataclass + TOML loader
  results.py             result dataclasses + Snapshot
  verdict.py             Verdict + evaluate()
  tracking.py            FlapTracker, PlugSession (time-based state)
  collector.py           Checks bundle + Collector (ordered pipeline)
  render.py              Row, build_rows(), render(), render_message()
  display.py             RefreshPolicy, Display, PngBackend, WaveshareBackend, make_backend()
  statelog.py            StateLogger (JSON lines on change)
  main.py                run() loop + main()
  checks/
    __init__.py
    command.py           run_command(), CheckError
    link.py              sysfs link state
    lldp.py              lldpcli JSON parser
    dhcp.py              nmcli parser
    gateway.py           ping parser
    dns.py               raw DNS query bound to interface
    internet.py          curl trace parser
  vendor/waveshare_epd/  vendored Waveshare driver files
deploy/
  ethtester.service
  ethtester.toml         default config
  install.sh
tests/
  __init__.py
  factories.py           ok_snapshot()
  test_*.py
README.md
pyproject.toml
.gitignore
```

---

### Task 1: Project scaffold and config

**Files:**
- Create: `.gitignore`, `pyproject.toml`, `ethtester/__init__.py`, `ethtester/checks/__init__.py`, `tests/__init__.py`, `ethtester/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `ethtester.__version__: str`; `ethtester.config.Config` (frozen dataclass with fields `interface: str = "eth0"`, `display: str = "V4"`, `expected_speed: int = 1000`, `interval: float = 5`, `dhcp_timeout: float = 20`, `ping_count: int = 3`, `dns_test_name: str = "example.com"`, `internet_url: str = "https://1.1.1.1/cdn-cgi/trace"`, `png_path: str = "/tmp/ethtester.png"`, `log_path: str = "/var/log/ethtester/ethtester.log"`); `load_config(path: Path) -> Config`; `DEFAULT_CONFIG_PATH: Path`.

- [ ] **Step 1: Initialise the repo and dev environment**

```bash
cd ethernet-tester
git init
python3 -m venv .venv
.venv/bin/pip install pillow pytest
mkdir -p ethtester/checks tests
```

- [ ] **Step 2: Create scaffold files**

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
tests/output/
```

`pyproject.toml`:
```toml
[project]
name = "ethtester"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pillow"]

[project.optional-dependencies]
dev = ["pytest"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

`ethtester/__init__.py`:
```python
"""Pi Zero Ethernet tester with an e-ink status display."""

__version__ = "0.1.0"
```

`ethtester/checks/__init__.py` and `tests/__init__.py`: empty files.

- [ ] **Step 3: Write the failing test**

`tests/test_config.py`:
```python
from pathlib import Path

import pytest

from ethtester.config import Config, load_config


def test_missing_file_gives_defaults(tmp_path: Path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_values_override_defaults(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('display = "V3"\nexpected_speed = 100\ninterval = 2\n')
    cfg = load_config(path)
    assert cfg.display == "V3"
    assert cfg.expected_speed == 100
    assert cfg.interval == 2
    assert cfg.interface == "eth0"


def test_unknown_keys_are_ignored(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('colour = "red"\n')
    assert load_config(path) == Config()


def test_invalid_display_rejected(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('display = "V9"\n')
    with pytest.raises(ValueError, match="display"):
        load_config(path)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ethtester.config'`

- [ ] **Step 5: Implement**

`ethtester/config.py`:
```python
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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml ethtester tests docs
git commit -m "feat: project scaffold and config loader"
```

---

### Task 2: Result types and verdict rules

**Files:**
- Create: `ethtester/results.py`, `ethtester/verdict.py`, `tests/factories.py`
- Test: `tests/test_verdict.py`

**Interfaces:**
- Produces (in `ethtester.results`, all `@dataclass(frozen=True)`):
  - `LinkResult(up: bool, speed_mbps: int | None = None, duplex: str | None = None, carrier_changes: int = 0)`
  - `LldpResult(switch_name: str | None = None, port: str | None = None, age_s: int | None = None)`
  - `DhcpResult(ip: str | None = None, prefix: int | None = None, gateway: str | None = None, dhcp_server: str | None = None, dns_servers: tuple[str, ...] = ())`
  - `GatewayResult(reachable: bool, rtt_ms: float | None = None, loss_pct: float = 100.0)`
  - `DnsResult(ok: bool, server: str | None = None)`
  - `InternetResult(ok: bool, public_ip: str | None = None)`
  - `Snapshot(link, flaps_last_minute=0, seconds_since_link=None, seconds_to_ip=None, lldp=None, lldp_given_up=False, dhcp=None, gateway=None, dns=None, internet=None)`. A field left as `None` means that check was skipped.
- Produces (in `ethtester.verdict`): `Verdict(code: str, text: str)`; `evaluate(s: Snapshot, expected_speed: int, dhcp_timeout: float) -> Verdict`. The codes are `NO_LINK`, `SLOW_LINK`, `FLAPPING`, `WAIT_DHCP`, `NO_DHCP`, `GATEWAY`, `DNS`, `NO_INTERNET` and `OK`.
- Produces (in `tests.factories`): `ok_snapshot(**overrides) -> Snapshot`.

- [ ] **Step 1: Create the result types**

`ethtester/results.py`:
```python
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
```

`tests/factories.py`:
```python
from dataclasses import replace

from ethtester.results import (
    DhcpResult,
    DnsResult,
    GatewayResult,
    InternetResult,
    LinkResult,
    LldpResult,
    Snapshot,
)


def ok_snapshot(**overrides) -> Snapshot:
    """A snapshot where every check passed; override fields to break one."""
    base = Snapshot(
        link=LinkResult(up=True, speed_mbps=1000, duplex="full", carrier_changes=2),
        flaps_last_minute=0,
        seconds_since_link=30.0,
        seconds_to_ip=2.1,
        lldp=LldpResult(switch_name="SW-Office", port="gi0/14", age_s=25),
        dhcp=DhcpResult(
            ip="192.168.1.57",
            prefix=24,
            gateway="192.168.1.1",
            dhcp_server="192.168.1.1",
            dns_servers=("192.168.1.1",),
        ),
        gateway=GatewayResult(reachable=True, rtt_ms=3.2, loss_pct=0.0),
        dns=DnsResult(ok=True, server="192.168.1.1"),
        internet=InternetResult(ok=True, public_ip="84.1.2.3"),
    )
    return replace(base, **overrides)
```

- [ ] **Step 2: Write the failing test**

`tests/test_verdict.py`:
```python
from ethtester.results import (
    DhcpResult,
    DnsResult,
    GatewayResult,
    InternetResult,
    LinkResult,
    Snapshot,
)
from ethtester.verdict import evaluate
from tests.factories import ok_snapshot


def code(s: Snapshot) -> str:
    return evaluate(s, expected_speed=1000, dhcp_timeout=20).code


def test_all_good():
    v = evaluate(ok_snapshot(), 1000, 20)
    assert (v.code, v.text) == ("OK", "OK")


def test_no_link():
    v = evaluate(Snapshot(link=LinkResult(up=False)), 1000, 20)
    assert (v.code, v.text) == ("NO_LINK", "NO LINK: cable/plug/port")


def test_slow_link():
    s = ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full"))
    v = evaluate(s, 1000, 20)
    assert (v.code, v.text) == ("SLOW_LINK", "CABLE? (100M)")


def test_slow_link_ok_when_expected_is_100():
    s = ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full"))
    assert evaluate(s, 100, 20).code == "OK"


def test_unknown_speed_is_not_slow():
    assert code(ok_snapshot(link=LinkResult(up=True, speed_mbps=None))) == "OK"


def test_flapping_threshold():
    assert code(ok_snapshot(flaps_last_minute=2)) == "OK"
    v = evaluate(ok_snapshot(flaps_last_minute=3), 1000, 20)
    assert (v.code, v.text) == ("FLAPPING", "FLAPPING: loose plug?")


def test_waiting_for_dhcp_then_no_dhcp():
    waiting = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=5.0,
                          gateway=None, dns=None, internet=None)
    v = evaluate(waiting, 1000, 20)
    assert (v.code, v.text) == ("WAIT_DHCP", "WAITING FOR DHCP…")
    late = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=25.0,
                       gateway=None, dns=None, internet=None)
    v = evaluate(late, 1000, 20)
    assert (v.code, v.text) == ("NO_DHCP", "NO DHCP: router/VLAN")


def test_gateway_failures():
    unreachable = ok_snapshot(gateway=GatewayResult(reachable=False), dns=None, internet=None)
    v = evaluate(unreachable, 1000, 20)
    assert (v.code, v.text) == ("GATEWAY", "GATEWAY: router/VLAN")
    lossy = ok_snapshot(gateway=GatewayResult(reachable=True, rtt_ms=5, loss_pct=33.3))
    assert code(lossy) == "GATEWAY"
    borderline = ok_snapshot(gateway=GatewayResult(reachable=True, rtt_ms=5, loss_pct=20.0))
    assert code(borderline) == "OK"


def test_dns_failure():
    v = evaluate(ok_snapshot(dns=DnsResult(ok=False), internet=None), 1000, 20)
    assert (v.code, v.text) == ("DNS", "DNS")


def test_internet_failure():
    v = evaluate(ok_snapshot(internet=InternetResult(ok=False)), 1000, 20)
    assert (v.code, v.text) == ("NO_INTERNET", "NO INTERNET: uplink")


def test_first_rule_wins():
    s = ok_snapshot(link=LinkResult(up=True, speed_mbps=100), flaps_last_minute=5,
                    dhcp=DhcpResult(), seconds_since_link=60.0)
    assert code(s) == "SLOW_LINK"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_verdict.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ethtester.verdict'`

- [ ] **Step 4: Implement**

`ethtester/verdict.py`:
```python
"""Turns a snapshot into one verdict; the first matching rule wins."""

from dataclasses import dataclass

from .results import Snapshot

MAX_FLAPS = 2
MAX_LOSS_PCT = 20.0


@dataclass(frozen=True)
class Verdict:
    code: str
    text: str


def evaluate(s: Snapshot, expected_speed: int, dhcp_timeout: float) -> Verdict:
    if not s.link.up:
        return Verdict("NO_LINK", "NO LINK: cable/plug/port")
    if s.link.speed_mbps is not None and s.link.speed_mbps < expected_speed:
        return Verdict("SLOW_LINK", f"CABLE? ({s.link.speed_mbps}M)")
    if s.flaps_last_minute > MAX_FLAPS:
        return Verdict("FLAPPING", "FLAPPING: loose plug?")
    if s.dhcp is None or s.dhcp.ip is None:
        if s.seconds_since_link is not None and s.seconds_since_link < dhcp_timeout:
            return Verdict("WAIT_DHCP", "WAITING FOR DHCP…")
        return Verdict("NO_DHCP", "NO DHCP: router/VLAN")
    if s.gateway is None or not s.gateway.reachable or s.gateway.loss_pct > MAX_LOSS_PCT:
        return Verdict("GATEWAY", "GATEWAY: router/VLAN")
    if s.dns is None or not s.dns.ok:
        return Verdict("DNS", "DNS")
    if s.internet is None or not s.internet.ok:
        return Verdict("NO_INTERNET", "NO INTERNET: uplink")
    return Verdict("OK", "OK")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_verdict.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add ethtester/results.py ethtester/verdict.py tests/factories.py tests/test_verdict.py
git commit -m "feat: result types and verdict rules"
```

---

### Task 3: Link check and per-plug tracking

**Files:**
- Create: `ethtester/checks/link.py`, `ethtester/tracking.py`
- Test: `tests/test_link.py`, `tests/test_tracking.py`

**Interfaces:**
- Consumes: `LinkResult` (Task 2).
- Produces: `check_link(interface: str, root: Path = Path("/sys/class/net")) -> LinkResult`; `parse_link(carrier, speed, duplex, carrier_changes: str | None) -> LinkResult`; `FlapTracker(window: float = 60.0).update(now: float, carrier_changes: int) -> int`; `PlugSession` with `on_link(now: float, up: bool)`, `on_ip(now: float, has_ip: bool)`, `seconds_since_link(now: float) -> float | None`, property `seconds_to_ip -> float | None` and attribute `fresh: bool`. `fresh` is True when this link-up followed an observed link-down, i.e. a real plug-in rather than the service starting on an already-connected cable.

Background: Linux's `carrier_changes` counter goes up by 2 for each down→up cycle. Reading `carrier` raises `OSError` (EINVAL) when the interface is administratively down, and the directory disappears when the dongle is unplugged. Both cases count as "link down". `speed` reads `-1` when the speed is unknown.

- [ ] **Step 1: Write the failing tests**

`tests/test_link.py`:
```python
from pathlib import Path

from ethtester.checks.link import check_link, parse_link
from ethtester.results import LinkResult


def fake_sysfs(root: Path, **files: str) -> None:
    base = root / "eth0"
    base.mkdir(parents=True)
    for name, value in files.items():
        (base / name).write_text(value + "\n")


def test_link_up_gigabit(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="1", speed="1000", duplex="full", carrier_changes="4")
    assert check_link("eth0", tmp_path) == LinkResult(True, 1000, "full", 4)


def test_link_down(tmp_path: Path):
    fake_sysfs(tmp_path, carrier="0", speed="-1", duplex="unknown", carrier_changes="5")
    assert check_link("eth0", tmp_path) == LinkResult(False, None, None, 5)


def test_dongle_missing(tmp_path: Path):
    assert check_link("eth0", tmp_path) == LinkResult(False)


def test_unknown_speed():
    assert parse_link("1", "-1", "full", "2") == LinkResult(True, None, "full", 2)


def test_unreadable_carrier():
    assert parse_link(None, None, None, None) == LinkResult(False)
```

`tests/test_tracking.py`:
```python
from ethtester.tracking import FlapTracker, PlugSession


def test_flap_tracker_counts_cycles_in_window():
    t = FlapTracker(window=60)
    assert t.update(0, 2) == 0
    assert t.update(10, 4) == 1
    assert t.update(20, 6) == 2
    assert t.update(30, 8) == 3
    assert t.update(100, 8) == 0


def test_session_measures_time_to_ip_after_replug():
    s = PlugSession()
    s.on_link(0, False)
    s.on_link(1, True)
    s.on_ip(1, False)
    assert s.fresh
    assert s.seconds_since_link(2) == 1
    s.on_ip(3.5, True)
    assert s.seconds_to_ip == 2.5
    s.on_ip(9, True)
    assert s.seconds_to_ip == 2.5


def test_session_already_up_at_start_is_not_fresh():
    s = PlugSession()
    s.on_link(0, True)
    s.on_ip(0, True)
    assert not s.fresh
    assert s.seconds_to_ip is None


def test_session_resets_on_unplug():
    s = PlugSession()
    s.on_link(0, False)
    s.on_link(1, True)
    s.on_ip(2, True)
    s.on_link(5, False)
    assert s.seconds_since_link(5) is None
    assert s.seconds_to_ip is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_link.py tests/test_tracking.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`ethtester/checks/link.py`:
```python
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
```

`ethtester/tracking.py`:
```python
"""State that depends on time: link flaps and per-plug timing."""

from collections import deque


class FlapTracker:
    """Counts down→up cycles within a sliding window using carrier_changes."""

    def __init__(self, window: float = 60.0):
        self.window = window
        self.samples: deque[tuple[float, int]] = deque()

    def update(self, now: float, carrier_changes: int) -> int:
        self.samples.append((now, carrier_changes))
        while now - self.samples[0][0] > self.window:
            self.samples.popleft()
        return max(0, carrier_changes - self.samples[0][1]) // 2


class PlugSession:
    """Timing for the current plug: when the link came up and when an IP appeared."""

    def __init__(self):
        self.link_up_at: float | None = None
        self.ip_at: float | None = None
        self.fresh = False
        self._saw_down = False

    def on_link(self, now: float, up: bool) -> None:
        if not up:
            self.link_up_at = None
            self.ip_at = None
            self._saw_down = True
        elif self.link_up_at is None:
            self.link_up_at = now
            self.fresh = self._saw_down

    def on_ip(self, now: float, has_ip: bool) -> None:
        if not has_ip:
            self.ip_at = None
        elif self.ip_at is None:
            self.ip_at = now

    def seconds_since_link(self, now: float) -> float | None:
        return None if self.link_up_at is None else now - self.link_up_at

    @property
    def seconds_to_ip(self) -> float | None:
        if not self.fresh or self.link_up_at is None or self.ip_at is None:
            return None
        return self.ip_at - self.link_up_at
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_link.py tests/test_tracking.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add ethtester/checks/link.py ethtester/tracking.py tests/test_link.py tests/test_tracking.py
git commit -m "feat: link check, flap tracking and per-plug timing"
```

---

### Task 4: Command runner and LLDP check

**Files:**
- Create: `ethtester/checks/command.py`, `ethtester/checks/lldp.py`
- Test: `tests/test_lldp.py`

**Interfaces:**
- Consumes: `LldpResult` (Task 2).
- Produces: `CheckError(Exception)`; `run_command(cmd: list[str], timeout: float = 10.0, check: bool = True) -> str` (returns stdout; raises `CheckError` on OSError, timeout, or a non-zero exit when `check=True`); `parse_lldp(raw: str, interface: str) -> LldpResult | None`; `check_lldp(interface: str, run=run_command) -> LldpResult | None`.

Background: `lldpcli -f json show neighbors ports eth0` prints `{"lldp": {}}` when there is no neighbour. Otherwise `lldp.interface` is either a dict keyed by interface name or a list of such dicts. A chassis with a system name is keyed by that name (`{"SW-Office": {...}}`); one without a name has `{"id": {"type": "mac", "value": ...}}`. `age` looks like `"0 day, 00:01:23"`.

- [ ] **Step 1: Write the failing test**

`tests/test_lldp.py`:
```python
import json

import pytest

from ethtester.checks.command import CheckError, run_command
from ethtester.checks.lldp import check_lldp, parse_lldp
from ethtester.results import LldpResult

NAMED = json.dumps({"lldp": {"interface": {"eth0": {
    "via": "LLDP", "rid": "1", "age": "0 day, 00:01:23",
    "chassis": {"SW-Office": {"id": {"type": "mac", "value": "aa:bb:cc:dd:ee:ff"},
                              "descr": "Managed switch"}},
    "port": {"id": {"type": "ifname", "value": "gi0/14"}, "descr": "Port 14"},
}}}})

UNNAMED_LIST = json.dumps({"lldp": {"interface": [{"eth0": {
    "via": "LLDP", "age": "1 day, 02:00:05",
    "chassis": {"id": {"type": "mac", "value": "aa:bb:cc:dd:ee:ff"}},
    "port": {"id": {"type": "mac", "value": "11:22:33:44:55:66"}, "descr": "Port 7"},
}}]}})


def test_named_switch():
    assert parse_lldp(NAMED, "eth0") == LldpResult("SW-Office", "gi0/14", 83)


def test_unnamed_switch_in_list_form():
    assert parse_lldp(UNNAMED_LIST, "eth0") == LldpResult("aa:bb:cc:dd:ee:ff", "Port 7", 93605)


def test_no_neighbour():
    assert parse_lldp('{"lldp": {}}', "eth0") is None
    assert parse_lldp(NAMED, "eth1") is None


def test_check_lldp_uses_runner():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return NAMED

    assert check_lldp("eth0", run=fake_run).port == "gi0/14"
    assert calls == [["lldpcli", "-f", "json", "show", "neighbors", "ports", "eth0"]]


def test_run_command_errors():
    assert run_command(["echo", "hi"]) == "hi\n"
    with pytest.raises(CheckError):
        run_command(["false"])
    assert run_command(["false"], check=False) == ""
    with pytest.raises(CheckError):
        run_command(["definitely-not-a-command-xyz"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_lldp.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`ethtester/checks/command.py`:
```python
"""Runs external tools and turns their failures into CheckError."""

import subprocess


class CheckError(Exception):
    pass


def run_command(cmd: list[str], timeout: float = 10.0, check: bool = True) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f"{cmd[0]}: {exc}") from exc
    if check and proc.returncode != 0:
        raise CheckError(f"{cmd[0]} exited {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout
```

`ethtester/checks/lldp.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_lldp.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add ethtester/checks/command.py ethtester/checks/lldp.py tests/test_lldp.py
git commit -m "feat: command runner and LLDP neighbour check"
```

---

### Task 5: DHCP check (NetworkManager)

**Files:**
- Create: `ethtester/checks/dhcp.py`
- Test: `tests/test_dhcp.py`

**Interfaces:**
- Consumes: `DhcpResult` (Task 2), `run_command` (Task 4).
- Produces: `parse_nmcli(raw: str) -> DhcpResult`; `check_dhcp(interface: str, run=run_command) -> DhcpResult`.

Background: `nmcli -t -f IP4,DHCP4 device show eth0` prints `KEY:value` lines. Link-local addresses (`169.254.x.x`) don't count as a DHCP lease.

- [ ] **Step 1: Write the failing test**

`tests/test_dhcp.py`:
```python
from ethtester.checks.dhcp import check_dhcp, parse_nmcli
from ethtester.results import DhcpResult

LEASE = """IP4.ADDRESS[1]:192.168.1.57/24
IP4.GATEWAY:192.168.1.1
IP4.ROUTE[1]:dst = 192.168.1.0/24, nh = 0.0.0.0, mt = 100
IP4.DNS[1]:192.168.1.1
IP4.DNS[2]:1.1.1.1
IP4.DOMAIN[1]:lan
DHCP4.OPTION[1]:broadcast_address = 192.168.1.255
DHCP4.OPTION[2]:dhcp_server_identifier = 192.168.1.1
DHCP4.OPTION[3]:ip_address = 192.168.1.57
"""

NO_LEASE = """IP4.GATEWAY:--
"""


def test_lease():
    assert parse_nmcli(LEASE) == DhcpResult(
        ip="192.168.1.57", prefix=24, gateway="192.168.1.1",
        dhcp_server="192.168.1.1", dns_servers=("192.168.1.1", "1.1.1.1"),
    )


def test_no_lease():
    assert parse_nmcli(NO_LEASE) == DhcpResult()
    assert parse_nmcli("") == DhcpResult()


def test_link_local_is_not_a_lease():
    assert parse_nmcli("IP4.ADDRESS[1]:169.254.10.20/16\n").ip is None


def test_check_dhcp_command():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return LEASE

    assert check_dhcp("eth0", run=fake_run).ip == "192.168.1.57"
    assert calls == [["nmcli", "-t", "-f", "IP4,DHCP4", "device", "show", "eth0"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_dhcp.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`ethtester/checks/dhcp.py`:
```python
"""IPv4 lease details from NetworkManager."""

from ..results import DhcpResult
from .command import run_command


def parse_nmcli(raw: str) -> DhcpResult:
    ip = prefix = gateway = server = None
    dns: list[str] = []
    for line in raw.splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if key.startswith("IP4.ADDRESS") and ip is None and "/" in value:
            address, _, bits = value.partition("/")
            if not address.startswith("169.254."):
                ip, prefix = address, int(bits)
        elif key == "IP4.GATEWAY" and value and value != "--":
            gateway = value
        elif key.startswith("IP4.DNS") and value:
            dns.append(value)
        elif key.startswith("DHCP4.OPTION"):
            name, _, option = value.partition(" = ")
            if name.strip() == "dhcp_server_identifier":
                server = option.strip()
    return DhcpResult(ip=ip, prefix=prefix, gateway=gateway, dhcp_server=server,
                      dns_servers=tuple(dns))


def check_dhcp(interface: str, run=run_command) -> DhcpResult:
    return parse_nmcli(run(["nmcli", "-t", "-f", "IP4,DHCP4", "device", "show", interface]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_dhcp.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add ethtester/checks/dhcp.py tests/test_dhcp.py
git commit -m "feat: DHCP lease check via nmcli"
```

---

### Task 6: Gateway, DNS and internet checks

**Files:**
- Create: `ethtester/checks/gateway.py`, `ethtester/checks/dns.py`, `ethtester/checks/internet.py`
- Test: `tests/test_reachability.py`

**Interfaces:**
- Consumes: `GatewayResult`, `DnsResult`, `InternetResult` (Task 2); `run_command`, `CheckError` (Task 4).
- Produces:
  - `parse_ping(raw: str) -> GatewayResult`; `check_gateway(interface: str, gateway: str, count: int, run=run_command) -> GatewayResult`
  - `build_query(name: str, qid: int) -> bytes`; `parse_response(data: bytes, qid: int) -> bool`; `dns_query(server: str, name: str, interface: str, timeout: float = 2.0) -> bool`; `check_dns(interface: str, servers: tuple[str, ...], name: str, query=dns_query) -> DnsResult`
  - `parse_trace(raw: str) -> str | None`; `check_internet(interface: str, url: str, run=run_command) -> InternetResult`

Background: `dig -b` only sets the source address, and Linux can still route the packet out over Wi-Fi. So DNS uses a small hand-built UDP query on a socket bound with `SO_BINDTODEVICE` (root only; the service runs as root). `ping -I eth0` and `curl --interface eth0` bind to the device themselves.

- [ ] **Step 1: Write the failing test**

`tests/test_reachability.py`:
```python
import struct

from ethtester.checks.command import CheckError
from ethtester.checks.dns import build_query, check_dns, parse_response
from ethtester.checks.gateway import check_gateway, parse_ping
from ethtester.checks.internet import check_internet, parse_trace
from ethtester.results import DnsResult, GatewayResult, InternetResult

PING_OK = """PING 192.168.1.1 (192.168.1.1) from 192.168.1.57 eth0: 56(84) bytes of data.
64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=2.91 ms

--- 192.168.1.1 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 404ms
rtt min/avg/max/mdev = 2.512/3.204/4.011/0.612 ms
"""

PING_DEAD = """--- 192.168.1.1 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2050ms
"""

PING_LOSSY = """3 packets transmitted, 2 received, 33.3333% packet loss, time 405ms
rtt min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms
"""


def test_parse_ping():
    assert parse_ping(PING_OK) == GatewayResult(True, 3.204, 0.0)
    assert parse_ping(PING_DEAD) == GatewayResult(False, None, 100.0)
    assert parse_ping(PING_LOSSY) == GatewayResult(True, 2.0, 33.3333)
    assert parse_ping("") == GatewayResult(False, None, 100.0)


def test_check_gateway_command():
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return PING_OK

    assert check_gateway("eth0", "192.168.1.1", 3, run=fake_run).reachable
    cmd, kwargs = calls[0]
    assert cmd == ["ping", "-I", "eth0", "-n", "-c", "3", "-W", "1", "-i", "0.2", "192.168.1.1"]
    assert kwargs["check"] is False


def test_build_query():
    expected = (bytes.fromhex("123401000001000000000000")
                + b"\x07example\x03com\x00" + b"\x00\x01\x00\x01")
    assert build_query("example.com", 0x1234) == expected


def response(qid: int, flags: int, answers: int) -> bytes:
    return struct.pack(">HHHHHH", qid, flags, 1, answers, 0, 0)


def test_parse_response():
    assert parse_response(response(7, 0x8180, 1), 7)
    assert not parse_response(response(7, 0x8183, 0), 7)   # NXDOMAIN
    assert not parse_response(response(8, 0x8180, 1), 7)   # wrong id
    assert not parse_response(response(7, 0x0100, 1), 7)   # not a response
    assert not parse_response(b"\x00\x07", 7)               # truncated


def test_check_dns():
    seen = []

    def ok_query(server, name, interface):
        seen.append((server, name, interface))
        return True

    def timeout_query(server, name, interface):
        raise TimeoutError()

    assert check_dns("eth0", ("192.168.1.1", "1.1.1.1"), "example.com", query=ok_query) \
        == DnsResult(True, "192.168.1.1")
    assert seen == [("192.168.1.1", "example.com", "eth0")]
    assert check_dns("eth0", ("9.9.9.9",), "example.com", query=timeout_query) \
        == DnsResult(False, "9.9.9.9")
    assert check_dns("eth0", (), "example.com", query=ok_query) == DnsResult(False, None)


def test_parse_trace():
    assert parse_trace("fl=1\nh=1.1.1.1\nip=84.1.2.3\nts=1\n") == "84.1.2.3"
    assert parse_trace("<html>hello</html>") is None


def test_check_internet():
    def ok_run(cmd, **kwargs):
        assert cmd[:3] == ["curl", "--interface", "eth0"]
        return "ip=84.1.2.3\n"

    def failing_run(cmd, **kwargs):
        raise CheckError("curl exited 7")

    url = "https://1.1.1.1/cdn-cgi/trace"
    assert check_internet("eth0", url, run=ok_run) == InternetResult(True, "84.1.2.3")
    assert check_internet("eth0", url, run=failing_run) == InternetResult(False, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_reachability.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`ethtester/checks/gateway.py`:
```python
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
```

`ethtester/checks/dns.py`:
```python
"""A single DNS A-record lookup sent only through the tested interface."""

import random
import socket
import struct

from ..results import DnsResult

SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)


def build_query(name: str, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    labels = name.strip(".").split(".")
    qname = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\x00"
    return header + qname + struct.pack(">HH", 1, 1)


def parse_response(data: bytes, qid: int) -> bool:
    if len(data) < 12:
        return False
    rid, flags, _questions, answers = struct.unpack(">HHHH", data[:8])
    is_response = bool(flags & 0x8000)
    rcode = flags & 0x000F
    return rid == qid and is_response and rcode == 0 and answers > 0


def dns_query(server: str, name: str, interface: str, timeout: float = 2.0) -> bool:
    qid = random.randrange(65536)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, interface.encode())
        sock.settimeout(timeout)
        sock.sendto(build_query(name, qid), (server, 53))
        data, _ = sock.recvfrom(4096)
    return parse_response(data, qid)


def check_dns(interface: str, servers: tuple[str, ...], name: str, query=dns_query) -> DnsResult:
    if not servers:
        return DnsResult(ok=False, server=None)
    server = servers[0]
    try:
        ok = query(server, name, interface)
    except OSError:
        ok = False
    return DnsResult(ok=ok, server=server)
```

`ethtester/checks/internet.py`:
```python
"""HTTPS request through the tested interface; also reports the public IP."""

from ..results import InternetResult
from .command import CheckError, run_command


def parse_trace(raw: str) -> str | None:
    for line in raw.splitlines():
        if line.startswith("ip="):
            return line[3:].strip()
    return None


def check_internet(interface: str, url: str, run=run_command) -> InternetResult:
    try:
        out = run(["curl", "--interface", interface, "-s", "-f", "-m", "5", url], timeout=10)
    except CheckError:
        return InternetResult(ok=False, public_ip=None)
    return InternetResult(ok=True, public_ip=parse_trace(out))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_reachability.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add ethtester/checks/gateway.py ethtester/checks/dns.py ethtester/checks/internet.py tests/test_reachability.py
git commit -m "feat: gateway, DNS and internet checks bound to the interface"
```

---

### Task 7: Collector (ordered check pipeline)

**Files:**
- Create: `ethtester/collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: every check function (Tasks 3–6), `FlapTracker` and `PlugSession` (Task 3), `Config` (Task 1), the result types (Task 2).
- Produces: `Checks` (dataclass of callables: `link(interface)`, `lldp(interface)`, `dhcp(interface)`, `gateway(interface, gateway, count)`, `dns(interface, servers, name)`, `internet(interface, url)`, defaulting to the real checks); `Collector(cfg: Config, checks: Checks | None = None, clock=time.monotonic)` with `collect() -> Snapshot`.

Rules:
- If the link is down, return just the link result and skip everything else.
- If there's no IP, skip gateway, DNS and internet.
- If the gateway fails, skip DNS and internet. If DNS fails, skip internet.
- If a check raises, log it and use a failing fallback result.
- Stale LLDP: when the session is `fresh`, ignore a neighbour whose `age_s` is more than 5 s older than the current link (it came from the previous plug).
- `lldp_given_up` is True when there's no neighbour and the link has been up for more than 60 s.
- DNS servers are the ones DHCP handed out, or the gateway if DHCP gave none.
- Flap history (`FlapTracker`) is not reset on link-down: it is a 60 s sliding window, and resetting it would hide flapping.

- [ ] **Step 1: Write the failing test**

`tests/test_collector.py`:
```python
from ethtester.collector import Checks, Collector
from ethtester.config import Config
from ethtester.results import (
    DhcpResult,
    DnsResult,
    GatewayResult,
    InternetResult,
    LinkResult,
    LldpResult,
)

LEASE = DhcpResult(ip="192.168.1.57", prefix=24, gateway="192.168.1.1",
                   dns_servers=("192.168.1.1",))


class Fake:
    """Scriptable checks that record which ones were called."""

    def __init__(self):
        self.calls = []
        self.link_result = LinkResult(up=True, speed_mbps=1000, duplex="full", carrier_changes=2)
        self.lldp_result = LldpResult("SW", "1", age_s=3)
        self.dhcp_result = LEASE
        self.gateway_result = GatewayResult(True, 2.0, 0.0)
        self.dns_result = DnsResult(True, "192.168.1.1")
        self.internet_result = InternetResult(True, "84.1.2.3")

    def checks(self) -> Checks:
        def make(name):
            def fn(*args):
                self.calls.append((name, args))
                result = getattr(self, f"{name}_result")
                if isinstance(result, Exception):
                    raise result
                return result
            return fn
        return Checks(**{n: make(n) for n in ("link", "lldp", "dhcp", "gateway", "dns", "internet")})

    def names(self):
        return [name for name, _ in self.calls]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(fake=None, clock=None):
    fake = fake or Fake()
    clock = clock or Clock()
    return fake, clock, Collector(Config(), fake.checks(), clock)


def test_link_down_skips_everything():
    fake, _, c = make()
    fake.link_result = LinkResult(up=False)
    s = c.collect()
    assert fake.names() == ["link"]
    assert s.dhcp is None and s.gateway is None


def test_no_ip_skips_reachability():
    fake, clock, c = make()
    fake.dhcp_result = DhcpResult()
    clock.now = 4.0
    s = c.collect()
    assert fake.names() == ["link", "lldp", "dhcp"]
    assert s.seconds_since_link == 0.0
    assert s.gateway is None


def test_full_pipeline():
    fake, _, c = make()
    s = c.collect()
    assert fake.names() == ["link", "lldp", "dhcp", "gateway", "dns", "internet"]
    assert ("gateway", ("eth0", "192.168.1.1", 3)) in fake.calls
    assert ("dns", ("eth0", ("192.168.1.1",), "example.com")) in fake.calls
    assert s.internet.public_ip == "84.1.2.3"


def test_gateway_failure_skips_dns():
    fake, _, c = make()
    fake.gateway_result = GatewayResult(False)
    s = c.collect()
    assert "dns" not in fake.names()
    assert s.dns is None


def test_dns_falls_back_to_gateway():
    fake, _, c = make()
    fake.dhcp_result = DhcpResult(ip="10.0.0.5", prefix=8, gateway="10.0.0.1")
    c.collect()
    assert ("dns", ("eth0", ("10.0.0.1",), "example.com")) in fake.calls


def test_check_exception_uses_fallback():
    fake, _, c = make()
    fake.dhcp_result = RuntimeError("nmcli broke")
    s = c.collect()
    assert s.dhcp == DhcpResult()
    assert "gateway" not in fake.names()


def test_stale_lldp_ignored_after_replug():
    fake, clock, c = make()
    fake.link_result = LinkResult(up=False)
    c.collect()
    fake.link_result = LinkResult(up=True, speed_mbps=1000)
    fake.lldp_result = LldpResult("OLD", "9", age_s=300)
    clock.now = 10.0
    assert c.collect().lldp is None


def test_lldp_kept_when_service_starts_on_live_link():
    fake, _, c = make()
    fake.lldp_result = LldpResult("SW", "1", age_s=300)
    assert c.collect().lldp.switch_name == "SW"


def test_lldp_given_up_after_60s():
    fake, clock, c = make()
    fake.lldp_result = None
    assert not c.collect().lldp_given_up
    clock.now = 61.0
    assert c.collect().lldp_given_up


def test_seconds_to_ip_and_flaps():
    fake, clock, c = make()
    fake.link_result = LinkResult(up=False, carrier_changes=3)
    c.collect()
    clock.now = 1.0
    fake.link_result = LinkResult(up=True, speed_mbps=1000, carrier_changes=4)
    fake.dhcp_result = DhcpResult()
    c.collect()
    clock.now = 3.5
    fake.dhcp_result = LEASE
    s = c.collect()
    assert s.seconds_to_ip == 2.5
    assert s.flaps_last_minute == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_collector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ethtester.collector'`

- [ ] **Step 3: Implement**

`ethtester/collector.py`:
```python
"""Runs the checks in order and builds a Snapshot; later checks are skipped once one fails."""

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .checks.dhcp import check_dhcp
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

        link = _safe(checks.link, LinkResult(up=False), cfg.interface)
        flaps = self.flaps.update(now, link.carrier_changes)
        self.session.on_link(now, link.up)
        if not link.up:
            return Snapshot(link=link, flaps_last_minute=flaps)

        since = self.session.seconds_since_link(now)
        lldp = _safe(checks.lldp, None, cfg.interface)
        if (lldp is not None and self.session.fresh and lldp.age_s is not None
                and lldp.age_s > since + LLDP_AGE_SLACK_S):
            lldp = None

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_collector.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add ethtester/collector.py tests/test_collector.py
git commit -m "feat: ordered check pipeline producing snapshots"
```

---

### Task 8: Screen rendering

**Files:**
- Create: `ethtester/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Snapshot` (Task 2), `Verdict` (Task 2).
- Produces: `WIDTH = 250`, `HEIGHT = 122`; status constants `OK, FAIL, SKIP, WAIT = "ok", "fail", "skip", "wait"`; `Row(label: str, status: str, text: str)`; `build_rows(s: Snapshot, v: Verdict) -> list[Row]` (always 6 rows: LINK, PORT, IP, GW, DNS, NET); `render(rows: list[Row], v: Verdict, clock_text: str) -> PIL.Image.Image`; `render_message(title: str, subtitle: str) -> PIL.Image.Image`.

Fonts: DejaVu Sans Mono on the Pi (`fonts-dejavu-core`), and Menlo on the Mac (which has the same metrics). If neither is found, it falls back to Pillow's built-in bitmap font. Status icons are drawn with lines rather than glyphs, so they don't depend on the font.

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:
```python
from pathlib import Path

from PIL import ImageDraw, ImageOps

from ethtester.render import (
    FAIL, HEIGHT, OK, ROW_FONT, SKIP, WAIT, WIDTH, Row, _fit, build_rows, render, render_message,
)
from ethtester.results import DhcpResult, GatewayResult, LinkResult, Snapshot
from ethtester.verdict import Verdict, evaluate
from tests.factories import ok_snapshot

OUT = Path(__file__).parent / "output"


def rows_for(s: Snapshot) -> list[Row]:
    return build_rows(s, evaluate(s, 1000, 20))


def test_rows_all_ok():
    assert rows_for(ok_snapshot()) == [
        Row("LINK", OK, "1000M full  flaps:0"),
        Row("PORT", OK, "SW-Office / gi0/14"),
        Row("IP", OK, "192.168.1.57/24  2.1s"),
        Row("GW", OK, "192.168.1.1  3ms 0%"),
        Row("DNS", OK, "192.168.1.1"),
        Row("NET", OK, "84.1.2.3"),
    ]


def test_rows_link_down():
    rows = rows_for(Snapshot(link=LinkResult(up=False)))
    assert rows[0] == Row("LINK", FAIL, "down")
    assert all(r.status == SKIP for r in rows[1:])


def test_rows_waiting_and_no_dhcp():
    waiting = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=3.0, lldp=None,
                          gateway=None, dns=None, internet=None)
    rows = rows_for(waiting)
    assert rows[1] == Row("PORT", WAIT, "…")
    assert rows[2] == Row("IP", WAIT, "waiting…")
    assert [r.status for r in rows[3:]] == [SKIP, SKIP, SKIP]
    failed = ok_snapshot(dhcp=DhcpResult(), seconds_since_link=30.0, lldp=None,
                         lldp_given_up=True, gateway=None, dns=None, internet=None)
    rows = rows_for(failed)
    assert rows[1] == Row("PORT", SKIP, "no LLDP")
    assert rows[2] == Row("IP", FAIL, "no address")


def test_rows_slow_link_and_gateway_fail():
    rows = rows_for(ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full")))
    assert rows[0] == Row("LINK", FAIL, "100M full  flaps:0")
    rows = rows_for(ok_snapshot(gateway=GatewayResult(False), dns=None, internet=None))
    assert rows[3] == Row("GW", FAIL, "192.168.1.1  100% loss")


def test_fit_truncates_to_width():
    draw = ImageDraw.Draw(render_message("x", "y"))
    text = _fit(draw, "a-very-long-switch-name-that-goes-on / GigabitEthernet1/0/48", ROW_FONT, 200)
    assert text.endswith("…")
    assert draw.textlength(text, font=ROW_FONT) <= 200


def is_drawn(image) -> bool:
    return ImageOps.invert(image.convert("L")).getbbox() is not None


def test_render_samples():
    OUT.mkdir(exist_ok=True)
    samples = {
        "ok": ok_snapshot(),
        "no_link": Snapshot(link=LinkResult(up=False)),
        "cable_100m": ok_snapshot(link=LinkResult(up=True, speed_mbps=100, duplex="full")),
        "no_dhcp": ok_snapshot(dhcp=DhcpResult(), seconds_since_link=30.0, lldp=None,
                               lldp_given_up=True, gateway=None, dns=None, internet=None),
    }
    for name, snap in samples.items():
        image = render(rows_for(snap), evaluate(snap, 1000, 20), "10:42")
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "1"
        assert is_drawn(image)
        image.save(OUT / f"{name}.png")


def test_render_message():
    image = render_message("ethtester", "waiting for link  v0.1.0")
    assert image.size == (WIDTH, HEIGHT) and is_drawn(image)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ethtester.render'`

- [ ] **Step 3: Implement**

`ethtester/render.py`:
```python
"""Turns a snapshot and verdict into the 250×122 one-bit screen image."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .results import Snapshot
from .verdict import MAX_LOSS_PCT, Verdict

WIDTH, HEIGHT = 250, 122
OK, FAIL, SKIP, WAIT = "ok", "fail", "skip", "wait"

MONO = [("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 0),
        ("/System/Library/Fonts/Menlo.ttc", 0)]
MONO_BOLD = [("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 0),
             ("/System/Library/Fonts/Menlo.ttc", 1)]

ROW_HEIGHT = 13
LABEL_X, ICON_X, TEXT_X = 2, 34, 46
DIVIDER_Y = 81
HEADLINE_Y = 84
FOOTER_Y = 107


def load_font(candidates: list[tuple[str, int]], size: int):
    for path, index in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size, index=index)
    return ImageFont.load_default()


ROW_FONT = load_font(MONO, 11)
HEAD_FONT = load_font(MONO_BOLD, 18)


@dataclass(frozen=True)
class Row:
    label: str
    status: str
    text: str


def build_rows(s: Snapshot, v: Verdict) -> list[Row]:
    return [_link_row(s, v), _port_row(s), _ip_row(s, v), _gateway_row(s), _dns_row(s), _net_row(s)]


def _link_row(s: Snapshot, v: Verdict) -> Row:
    if not s.link.up:
        return Row("LINK", FAIL, "down")
    speed = f"{s.link.speed_mbps}M" if s.link.speed_mbps else "?M"
    text = f"{speed} {s.link.duplex or '?'}  flaps:{s.flaps_last_minute}"
    return Row("LINK", FAIL if v.code in ("SLOW_LINK", "FLAPPING") else OK, text)


def _port_row(s: Snapshot) -> Row:
    if not s.link.up:
        return Row("PORT", SKIP, "")
    if s.lldp and s.lldp.switch_name:
        port = f" / {s.lldp.port}" if s.lldp.port else ""
        return Row("PORT", OK, f"{s.lldp.switch_name}{port}")
    if s.lldp_given_up:
        return Row("PORT", SKIP, "no LLDP")
    return Row("PORT", WAIT, "…")


def _ip_row(s: Snapshot, v: Verdict) -> Row:
    if not s.link.up:
        return Row("IP", SKIP, "")
    d = s.dhcp
    if d is None or d.ip is None:
        if v.code == "NO_DHCP":
            return Row("IP", FAIL, "no address")
        return Row("IP", WAIT, "waiting…")
    text = f"{d.ip}/{d.prefix}" if d.prefix is not None else d.ip
    if s.seconds_to_ip is not None:
        text += f"  {s.seconds_to_ip:.1f}s"
    return Row("IP", OK, text)


def _gateway_row(s: Snapshot) -> Row:
    g = s.gateway
    if g is None:
        return Row("GW", SKIP, "")
    addr = (s.dhcp.gateway if s.dhcp else None) or "none"
    if g.reachable and g.loss_pct <= MAX_LOSS_PCT:
        rtt = f"{g.rtt_ms:.0f}ms " if g.rtt_ms is not None else ""
        return Row("GW", OK, f"{addr}  {rtt}{g.loss_pct:.0f}%")
    return Row("GW", FAIL, f"{addr}  {g.loss_pct:.0f}% loss")


def _dns_row(s: Snapshot) -> Row:
    if s.dns is None:
        return Row("DNS", SKIP, "")
    if s.dns.ok:
        return Row("DNS", OK, s.dns.server or "")
    return Row("DNS", FAIL, s.dns.server or "no server")


def _net_row(s: Snapshot) -> Row:
    if s.internet is None:
        return Row("NET", SKIP, "")
    if s.internet.ok:
        return Row("NET", OK, s.internet.public_ip or "reachable")
    return Row("NET", FAIL, "unreachable")


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def _draw_icon(draw: ImageDraw.ImageDraw, x: int, y: int, status: str) -> None:
    if status == OK:
        draw.line([(x, y + 5), (x + 3, y + 8), (x + 8, y + 1)], fill=0, width=2)
    elif status == FAIL:
        draw.line([(x + 1, y + 1), (x + 8, y + 8)], fill=0, width=2)
        draw.line([(x + 8, y + 1), (x + 1, y + 8)], fill=0, width=2)
    elif status == WAIT:
        for dx in (1, 4, 7):
            draw.rectangle([x + dx, y + 7, x + dx + 1, y + 8], fill=0)
    else:
        draw.line([(x + 1, y + 5), (x + 7, y + 5)], fill=0)


def render(rows: list[Row], v: Verdict, clock_text: str) -> Image.Image:
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    for i, row in enumerate(rows):
        y = 2 + i * ROW_HEIGHT
        draw.text((LABEL_X, y), row.label, font=ROW_FONT, fill=0)
        _draw_icon(draw, ICON_X, y + 2, row.status)
        draw.text((TEXT_X, y), _fit(draw, row.text, ROW_FONT, WIDTH - TEXT_X - 2), font=ROW_FONT, fill=0)
    draw.line([(0, DIVIDER_Y), (WIDTH, DIVIDER_Y)], fill=0)

    headline, _, hint = v.text.partition(": ")
    draw.text((2, HEADLINE_Y), _fit(draw, headline, HEAD_FONT, WIDTH - 4), font=HEAD_FONT, fill=0)
    clock_width = draw.textlength(clock_text, font=ROW_FONT)
    draw.text((WIDTH - 2 - clock_width, FOOTER_Y), clock_text, font=ROW_FONT, fill=0)
    if hint:
        draw.text((2, FOOTER_Y), _fit(draw, hint, ROW_FONT, WIDTH - clock_width - 10), font=ROW_FONT, fill=0)
    return image


def render_message(title: str, subtitle: str) -> Image.Image:
    image = Image.new("1", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    draw.text((4, 40), _fit(draw, title, HEAD_FONT, WIDTH - 8), font=HEAD_FONT, fill=0)
    draw.text((4, 70), _fit(draw, subtitle, ROW_FONT, WIDTH - 8), font=ROW_FONT, fill=0)
    return image
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_render.py -v`
Expected: 7 passed

- [ ] **Step 5: Review the sample images**

Open `tests/output/ok.png`, `no_link.png`, `cable_100m.png` and `no_dhcp.png`. Check that:
- no text overlaps;
- the verdict headline fits on one line;
- the hint and clock share the bottom line without touching;
- the icons line up with the row text.

If anything is off, adjust `ROW_HEIGHT`, `HEADLINE_Y`, `FOOTER_Y` or the font sizes, then re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add ethtester/render.py tests/test_render.py
git commit -m "feat: render status rows and verdict to a 250x122 image"
```

---

### Task 9: Display backends, refresh policy and vendored driver

**Files:**
- Create: `ethtester/display.py`, `ethtester/vendor/__init__.py`, `ethtester/vendor/waveshare_epd/` (downloaded)
- Test: `tests/test_display.py`

**Interfaces:**
- Consumes: `Config` (Task 1), `PIL.Image.Image` from `render()` (Task 8).
- Produces: `RefreshPolicy(max_partials: int = 20).decide(verdict_code: str) -> bool` (True means full refresh); `Display(backend, policy: RefreshPolicy | None = None).update(image, verdict_code: str) -> bool` (returns False when the image hasn't changed); `PngBackend(path: Path)`; `WaveshareBackend(epd)`; `make_backend(cfg: Config)`. A backend is any object with `show(image, full: bool) -> None`.

Waveshare API (the same in V3 and V4): `EPD()`, `init()`, `Clear(0xFF)`, `getbuffer(image)` (takes a 250×122 landscape image), `displayPartBaseImage(buf)` (full refresh that also stores the base image for partial updates), and `displayPartial(buf)`. The driver modules import `spidev`/`gpiozero` when loaded, so they must only be imported inside `make_backend`, never at module load.

- [ ] **Step 1: Vendor the Waveshare driver files**

```bash
mkdir -p ethtester/vendor/waveshare_epd
touch ethtester/vendor/__init__.py
BASE=https://raw.githubusercontent.com/waveshareteam/e-Paper/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd
for f in __init__.py epdconfig.py epd2in13_V3.py epd2in13_V4.py; do
  curl -fsSL "$BASE/$f" -o "ethtester/vendor/waveshare_epd/$f"
done
grep -n "def displayPartBaseImage\|def displayPartial\|def getbuffer\|def Clear\|def init" ethtester/vendor/waveshare_epd/epd2in13_V3.py ethtester/vendor/waveshare_epd/epd2in13_V4.py
```

Expected: both files define `init`, `Clear`, `getbuffer`, `displayPartBaseImage` and `displayPartial`. If either file is missing one of these, stop and report back. Don't guess an alternative API.

- [ ] **Step 2: Write the failing test**

`tests/test_display.py`:
```python
from pathlib import Path

from PIL import Image

from ethtester.config import Config
from ethtester.display import Display, PngBackend, RefreshPolicy, WaveshareBackend, make_backend


def test_policy_full_on_start_change_and_every_n():
    p = RefreshPolicy(max_partials=3)
    assert p.decide("OK") is True
    assert [p.decide("OK") for _ in range(3)] == [False, False, False]
    assert p.decide("OK") is True
    assert p.decide("OK") is False
    assert p.decide("DNS") is True


class Recorder:
    def __init__(self):
        self.shown = []

    def show(self, image, full):
        self.shown.append(full)


def img(color: int) -> Image.Image:
    return Image.new("1", (250, 122), color)


def test_display_skips_identical_images():
    rec = Recorder()
    d = Display(rec)
    assert d.update(img(255), "OK") is True
    assert d.update(img(255), "OK") is False
    assert d.update(img(0), "OK") is True
    assert rec.shown == [True, False]


def test_png_backend(tmp_path: Path):
    path = tmp_path / "screen.png"
    PngBackend(path).show(img(0), full=True)
    assert Image.open(path).size == (250, 122)


class FakeEPD:
    def __init__(self):
        self.calls = []

    def init(self):
        self.calls.append("init")

    def Clear(self, color):
        self.calls.append(f"Clear({color:#x})")

    def getbuffer(self, image):
        return b"buf"

    def displayPartBaseImage(self, buf):
        self.calls.append("base")

    def displayPartial(self, buf):
        self.calls.append("partial")


def test_waveshare_backend_sequence():
    epd = FakeEPD()
    backend = WaveshareBackend(epd)
    backend.show(img(255), full=True)
    backend.show(img(255), full=False)
    assert epd.calls == ["init", "Clear(0xff)", "init", "base", "partial"]


def test_make_backend_png(tmp_path: Path):
    backend = make_backend(Config(display="png", png_path=str(tmp_path / "s.png")))
    assert isinstance(backend, PngBackend)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_display.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ethtester.display'`

- [ ] **Step 4: Implement**

`ethtester/display.py`:
```python
"""Sends rendered images to the e-ink panel (or a PNG) with partial/full refresh."""

from pathlib import Path

from .config import Config


class RefreshPolicy:
    """Full refresh at start, on verdict change, and after max_partials partial refreshes."""

    def __init__(self, max_partials: int = 20):
        self.max_partials = max_partials
        self.partials = 0
        self.last_code: str | None = None

    def decide(self, verdict_code: str) -> bool:
        full = (self.last_code is None or verdict_code != self.last_code
                or self.partials >= self.max_partials)
        self.partials = 0 if full else self.partials + 1
        self.last_code = verdict_code
        return full


class Display:
    def __init__(self, backend, policy: RefreshPolicy | None = None):
        self.backend = backend
        self.policy = policy or RefreshPolicy()
        self._last: bytes | None = None

    def update(self, image, verdict_code: str) -> bool:
        data = image.tobytes()
        if data == self._last:
            return False
        self.backend.show(image, self.policy.decide(verdict_code))
        self._last = data
        return True


class PngBackend:
    def __init__(self, path: Path):
        self.path = Path(path)

    def show(self, image, full: bool) -> None:
        image.save(self.path)


class WaveshareBackend:
    def __init__(self, epd):
        self.epd = epd
        self.epd.init()
        self.epd.Clear(0xFF)

    def show(self, image, full: bool) -> None:
        buf = self.epd.getbuffer(image)
        if full:
            self.epd.init()
            self.epd.displayPartBaseImage(buf)
        else:
            self.epd.displayPartial(buf)


def make_backend(cfg: Config):
    if cfg.display == "png":
        return PngBackend(Path(cfg.png_path))
    if cfg.display == "V3":
        from .vendor.waveshare_epd import epd2in13_V3 as driver
    else:
        from .vendor.waveshare_epd import epd2in13_V4 as driver
    return WaveshareBackend(driver.EPD())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_display.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add ethtester/display.py ethtester/vendor tests/test_display.py
git commit -m "feat: display refresh policy, PNG and Waveshare backends"
```

---

### Task 10: State change log

**Files:**
- Create: `ethtester/statelog.py`
- Test: `tests/test_statelog.py`

**Interfaces:**
- Consumes: `Snapshot`, `Verdict` (Task 2).
- Produces: `fingerprint(s: Snapshot, v: Verdict) -> tuple`; `StateLogger(path: Path)` with `record(s: Snapshot, v: Verdict, wall_time: float) -> bool` (writes one JSON line when the fingerprint changes and returns True; otherwise returns False).

- [ ] **Step 1: Write the failing test**

`tests/test_statelog.py`:
```python
import json
from dataclasses import replace
from pathlib import Path

from ethtester.results import GatewayResult, LinkResult
from ethtester.statelog import StateLogger
from ethtester.verdict import evaluate
from tests.factories import ok_snapshot


def test_logs_only_on_meaningful_change(tmp_path: Path):
    path = tmp_path / "logs" / "ethtester.log"
    logger = StateLogger(path)
    s = ok_snapshot()
    assert logger.record(s, evaluate(s, 1000, 20), 1_700_000_000)
    jittery = replace(s, gateway=GatewayResult(True, 9.9, 0.0))
    assert not logger.record(jittery, evaluate(jittery, 1000, 20), 1_700_000_005)
    slow = replace(s, link=LinkResult(up=True, speed_mbps=100, duplex="full"))
    assert logger.record(slow, evaluate(slow, 1000, 20), 1_700_000_010)

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["verdict"] for line in lines] == ["OK", "SLOW_LINK"]
    assert lines[1]["text"] == "CABLE? (100M)"
    assert lines[1]["snapshot"]["link"]["speed_mbps"] == 100
    assert "ts" in lines[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_statelog.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`ethtester/statelog.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_statelog.py -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add ethtester/statelog.py tests/test_statelog.py
git commit -m "feat: JSON-lines state change log with rotation"
```

---

### Task 11: Main loop and entry point

**Files:**
- Create: `ethtester/main.py`, `ethtester/__main__.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `Config`/`load_config`/`DEFAULT_CONFIG_PATH` (Task 1), `evaluate` (Task 2), `Collector` (Task 7), `build_rows`/`render`/`render_message` (Task 8), `Display`/`make_backend` (Task 9), `StateLogger` (Task 10).
- Produces: `FAST_INTERVAL = 0.5`; `run(cfg, collector, display, statelog, sleep=time.sleep, wall=time.time, ticks: int | None = None) -> None`; `main(argv: list[str] | None = None) -> None`.

The loop polls every 0.5 s while the verdict is `NO_LINK` or `WAIT_DHCP`, so it reacts quickly to plug swaps and measures time-to-IP accurately. Otherwise it polls every `cfg.interval`. Any exception in a tick is logged and the loop continues.

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:
```python
from pathlib import Path

from PIL import Image

from ethtester.config import Config
from ethtester.display import Display, PngBackend
from ethtester.main import FAST_INTERVAL, run
from ethtester.results import LinkResult, Snapshot
from ethtester.statelog import StateLogger
from tests.factories import ok_snapshot


class ScriptedCollector:
    def __init__(self, *items):
        self.items = list(items)

    def collect(self):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_run_loop(tmp_path: Path):
    cfg = Config(display="png", png_path=str(tmp_path / "s.png"), interval=5)
    collector = ScriptedCollector(Snapshot(link=LinkResult(up=False)),
                                  RuntimeError("boom"),
                                  ok_snapshot())
    delays = []
    run(cfg, collector, Display(PngBackend(Path(cfg.png_path))),
        StateLogger(tmp_path / "state.log"), sleep=delays.append,
        wall=lambda: 1_700_000_000, ticks=3)

    assert delays == [FAST_INTERVAL, 5, 5]
    assert Image.open(tmp_path / "s.png").size == (250, 122)
    assert len((tmp_path / "state.log").read_text().splitlines()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ethtester.main'`

- [ ] **Step 3: Implement**

`ethtester/main.py`:
```python
"""Service entry point: collect → evaluate → log → render → display, forever."""

import argparse
import logging
import time
from pathlib import Path

from . import __version__
from .collector import Collector
from .config import DEFAULT_CONFIG_PATH, Config, load_config
from .display import Display, make_backend
from .render import build_rows, render, render_message
from .statelog import StateLogger
from .verdict import evaluate

log = logging.getLogger("ethtester")

FAST_INTERVAL = 0.5
FAST_CODES = ("NO_LINK", "WAIT_DHCP")


def run(cfg: Config, collector, display: Display, statelog: StateLogger,
        sleep=time.sleep, wall=time.time, ticks: int | None = None) -> None:
    done = 0
    while ticks is None or done < ticks:
        done += 1
        delay = cfg.interval
        try:
            snapshot = collector.collect()
            verdict = evaluate(snapshot, cfg.expected_speed, cfg.dhcp_timeout)
            now = wall()
            statelog.record(snapshot, verdict, now)
            clock_text = time.strftime("%H:%M", time.localtime(now))
            display.update(render(build_rows(snapshot, verdict), verdict, clock_text), verdict.code)
            if verdict.code in FAST_CODES:
                delay = FAST_INTERVAL
        except Exception:
            log.exception("tick failed")
        sleep(delay)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ethtester")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config(args.config)
    log.info("ethtester %s starting with %s", __version__, cfg)
    display = Display(make_backend(cfg))
    display.update(render_message("ethtester", f"waiting for link  v{__version__}"), "STARTUP")
    run(cfg, Collector(cfg), display, StateLogger(Path(cfg.log_path)))
```

`ethtester/__main__.py`:
```python
from .main import main

main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: 1 passed

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: all tests pass (64 tests)

- [ ] **Step 6: Commit**

```bash
git add ethtester/main.py ethtester/__main__.py tests/test_main.py
git commit -m "feat: main loop with fast polling during plug swaps"
```

---

### Task 12: Deployment files and README

**Files:**
- Create: `deploy/ethtester.toml`, `deploy/ethtester.service`, `deploy/install.sh`, `README.md`

**Interfaces:**
- Consumes: `python3 -m ethtester --config <path>` (Task 11); the config keys (Task 1).

NetworkManager gives up on DHCP after a while by default. The installer therefore adds a dedicated `ethtester` connection profile with an unlimited DHCP timeout and unlimited autoconnect retries, so a port that only answers DHCP late is still detected.

- [ ] **Step 1: Write `deploy/ethtester.toml`**

```toml
# ethtester settings. Edit from any computer by opening the SD card's boot partition.
interface      = "eth0"
display        = "V4"         # "V3" or "V4": check the sticker on the back of the HAT
expected_speed = 1000         # Mbps; use 100 for a 10/100 USB dongle
interval       = 5            # seconds between checks once connected
dhcp_timeout   = 20           # seconds before "NO DHCP"
ping_count     = 3
dns_test_name  = "example.com"
internet_url   = "https://1.1.1.1/cdn-cgi/trace"
```

- [ ] **Step 2: Write `deploy/ethtester.service`**

```ini
[Unit]
Description=Ethernet tester with e-ink display
After=NetworkManager.service lldpd.service
Wants=lldpd.service

[Service]
WorkingDirectory=/opt/ethtester
ExecStart=/usr/bin/python3 -m ethtester --config /boot/firmware/ethtester.toml
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Write `deploy/install.sh`**

```bash
#!/usr/bin/env bash
# Installs ethtester on Raspberry Pi OS Lite (Bookworm). Usage: sudo deploy/install.sh [interface]
set -euo pipefail

IFACE="${1:-eth0}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo." >&2
  exit 1
fi

apt-get update
apt-get install -y python3-pil python3-spidev python3-gpiozero python3-lgpio lldpd curl fonts-dejavu-core

raspi-config nonint do_spi 0
systemctl enable --now lldpd

if ! nmcli -t -f NAME connection show | grep -qx ethtester; then
  nmcli connection add type ethernet ifname "$IFACE" con-name ethtester \
    ipv4.method auto ipv4.dhcp-timeout 2147483647 ipv6.method ignore \
    connection.autoconnect-retries 0 connection.autoconnect-priority 10
fi

mkdir -p /opt/ethtester /var/log/ethtester
rm -rf /opt/ethtester/ethtester
cp -r "$SRC/ethtester" /opt/ethtester/
find /opt/ethtester -name __pycache__ -prune -exec rm -rf {} +

if [ ! -f /boot/firmware/ethtester.toml ]; then
  cp "$SRC/deploy/ethtester.toml" /boot/firmware/ethtester.toml
fi

cp "$SRC/deploy/ethtester.service" /etc/systemd/system/ethtester.service
systemctl daemon-reload
systemctl enable ethtester

echo "Installed. Check the display version in /boot/firmware/ethtester.toml, then reboot."
```

Run: `chmod +x deploy/install.sh && bash -n deploy/install.sh`
Expected: no output (the syntax is valid)

- [ ] **Step 4: Write `README.md`**

````markdown
# ethtester

A Pi Zero with a USB Ethernet dongle and a Waveshare 2.13" e-ink HAT. Plug it into a wall socket or switch port, and the screen shows how far the connection gets and what is most likely at fault.

| Screen says | Likely cause |
|---|---|
| `NO LINK` | Cable, wall plug, or dead switch port |
| `CABLE? (100M)` | Damaged cable/termination (gigabit fell back to 100) |
| `FLAPPING` | Loose plug or worn connector |
| `NO DHCP` | Router/DHCP, or port on the wrong VLAN |
| `GATEWAY` | Router or VLAN configuration |
| `DNS` | DNS server |
| `NO INTERNET` | Router's uplink |

## Setup

1. Flash **Raspberry Pi OS Lite (64-bit, Bookworm)** with Raspberry Pi Imager (use 32-bit on an original Pi Zero / Zero W). Enable SSH, set a user and, on Wi-Fi models, add your Wi-Fi so you can SSH in while the Ethernet port is under test.
2. Boot the Pi and copy this folder over:
   `scp -r ethernet-tester <user>@<pi-ip>:`
3. On the Pi, run `sudo ethernet-tester/deploy/install.sh`
4. In `/boot/firmware/ethtester.toml`, check that `display` matches the sticker on the back of the HAT (default `"V4"`). If your dongle is a 10/100 model, also set `expected_speed = 100`.
5. Reboot.

Logs: `journalctl -u ethtester` for errors, and `/var/log/ethtester/ethtester.log` for the state history.

Limitations: it can't measure PoE (use an inline PoE tester). LLDP needs a managed switch.

## Development (Mac)

```
python3 -m venv .venv && .venv/bin/pip install pillow pytest
.venv/bin/pytest
```

Sample screens are written to `tests/output/`.
````

- [ ] **Step 5: Commit**

```bash
git add deploy README.md
git commit -m "feat: systemd unit, installer, default config and README"
```

---

### Task 13: On-device verification

Manual test on the real hardware. Nothing is committed unless a fix is needed. Any fix goes back through the relevant task's test cycle first.

- [ ] **Step 1: Install** by following the README Setup section. Confirm `systemctl status ethtester` shows the service as `active (running)`, and that the startup screen appears.
- [ ] **Step 2: No cable.** Unplug the dongle's cable. Expected: `NO LINK` within about 1 s, with all rows below LINK showing a dash.
- [ ] **Step 3: Good port.** Plug into a known-good port. Expected: `WAITING FOR DHCP…`, then `OK`, with the correct IP, a time-to-IP, the gateway ping and the public IP. On a managed switch, PORT shows the switch name and port within 30 s.
- [ ] **Step 4: Slow link.** Use a cable with one pair cut, or force the switch port to 100M. Expected: `CABLE? (100M)` (gigabit dongle only).
- [ ] **Step 5: No DHCP.** Use a port on a VLAN with no DHCP server, or a switch with no router. Expected: `NO DHCP` after 20 s.
- [ ] **Step 6: Flapping.** Unplug and replug quickly three or more times within a minute. Expected: `FLAPPING`.
- [ ] **Step 7: Wi-Fi isolation (required for the Zero 2 W).** With Wi-Fi connected and the Ethernet cable unplugged, confirm the screen shows `NO LINK` and nothing below it passes.
- [ ] **Step 8: Log.** Check that `/var/log/ethtester/ethtester.log` has one line per state change from the steps above.
