# Vendored Waveshare e-Paper driver

- Upstream: https://github.com/waveshareteam/e-Paper, path `RaspberryPi_JetsonNano/python/lib/waveshare_epd`
- Files: `__init__.py`, `epdconfig.py`, `epd2in13_V3.py`, `epd2in13_V4.py`, fetched from `master` on 2026-10-21.

## Local modification

`ReadBusy()` in `epd2in13_V3.py` and `epd2in13_V4.py` looped forever while the BUSY pin stayed high.
It now waits through `ethtester.busywait.wait_until` with a deadline of `BUSY_TIMEOUT_S = 10` seconds
and raises `TimeoutError("e-paper busy timeout")` when it passes. The changed lines are marked
with `# ethtester:` comments. `epdconfig.py` and `__init__.py` are unchanged.
