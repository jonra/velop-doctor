"""Service entry point: collect → evaluate → log → render → display, forever."""

import argparse
import logging
import time
from pathlib import Path

from . import __version__, watchdog
from .collector import Collector
from .config import DEFAULT_CONFIG_PATH, Config, load_config
from .display import Display, PngBackend, make_backend
from .render import build_rows, render, render_message
from .statelog import StateLogger
from .verdict import evaluate

log = logging.getLogger("ethtester")

FAST_INTERVAL = 0.5
FAST_CODES = ("NO_LINK", "WAIT_DHCP")
CONFIG_ERROR_S = 15


def run(cfg: Config, collector, display: Display, statelog: StateLogger,
        sleep=time.sleep, wall=time.time, ticks: int | None = None,
        notify=watchdog.notify) -> None:
    done = 0
    while ticks is None or done < ticks:
        done += 1
        delay = cfg.interval
        try:
            snapshot = collector.collect()
            verdict = evaluate(snapshot, cfg.expected_speed, cfg.dhcp_timeout)
            delay = FAST_INTERVAL if verdict.code in FAST_CODES else cfg.interval
            now = wall()
            try:
                statelog.record(snapshot, verdict, now)
            except Exception:
                log.exception("state log failed")
            clock_text = time.strftime("%H:%M", time.localtime(now))
            display.update(render(build_rows(snapshot, verdict), verdict, clock_text), verdict.code)
        except Exception:
            log.exception("tick failed")
        notify("WATCHDOG=1")
        sleep(delay)


def start(cfg_path: Path, sleep=time.sleep, backend_factory=make_backend) -> tuple[Config, Display]:
    """Loads the config and opens the display, falling back so the loop always gets to run."""
    error = None
    try:
        cfg = load_config(cfg_path)
    except (ValueError, OSError) as exc:
        log.error("config %s unusable, using defaults: %s", cfg_path, exc)
        cfg, error = Config(), str(exc).splitlines()[0]
    log.info("ethtester %s starting with %s", __version__, cfg)

    try:
        backend = backend_factory(cfg)
    except Exception:
        log.exception("display init failed, writing PNG to %s instead", cfg.png_path)
        backend = PngBackend(Path(cfg.png_path))
    display = Display(backend)

    if error is None:
        title, subtitle, code = "ethtester", f"waiting for link  v{__version__}", "STARTUP"
    else:
        title, subtitle, code = "CONFIG ERROR", error, "CONFIG_ERROR"
    try:
        display.update(render_message(title, subtitle), code)
    except Exception:
        log.exception("startup screen failed")
    if error is not None:
        sleep(CONFIG_ERROR_S)
    return cfg, display


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ethtester")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    cfg, display = start(args.config)
    run(cfg, Collector(cfg), display, StateLogger(Path(cfg.log_path)))
