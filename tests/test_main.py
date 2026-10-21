from pathlib import Path

import pytest
from PIL import Image

from ethtester.config import Config, load_config
from ethtester.display import Display, PngBackend
from ethtester.main import CONFIG_ERROR_S, FAST_INTERVAL, run, start
from ethtester.render import render_message
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


class FailingStateLog:
    def record(self, snapshot, verdict, now):
        raise OSError("disk full")


def test_run_survives_statelog_failure(tmp_path: Path):
    cfg = Config(display="png", png_path=str(tmp_path / "s.png"), interval=5)
    collector = ScriptedCollector(Snapshot(link=LinkResult(up=False)))
    delays = []
    run(cfg, collector, Display(PngBackend(Path(cfg.png_path))),
        FailingStateLog(), sleep=delays.append,
        wall=lambda: 1_700_000_000, ticks=1)

    assert delays == [FAST_INTERVAL]
    assert Image.open(tmp_path / "s.png").size == (250, 122)


def test_run_pets_watchdog_every_tick_even_when_tick_fails(tmp_path: Path):
    cfg = Config(display="png", png_path=str(tmp_path / "s.png"), interval=5)
    collector = ScriptedCollector(Snapshot(link=LinkResult(up=False)),
                                  RuntimeError("boom"),
                                  ok_snapshot())
    sent = []
    run(cfg, collector, Display(PngBackend(Path(cfg.png_path))),
        StateLogger(tmp_path / "state.log"), sleep=lambda s: None,
        wall=lambda: 1_700_000_000, ticks=3, notify=sent.append)

    assert sent == ["WATCHDOG=1"] * 3


class Shown:
    def __init__(self):
        self.images = []

    def show(self, image, full):
        self.images.append(image.tobytes())


def test_start_shows_config_error_and_falls_back(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text("interval = \n")
    shown, waits, made = Shown(), [], []

    def factory(cfg):
        made.append(cfg)
        return shown

    cfg, display = start(path, sleep=waits.append, backend_factory=factory)

    assert cfg == Config()
    assert made == [Config()]
    assert waits == [CONFIG_ERROR_S] and CONFIG_ERROR_S == 15
    with pytest.raises(ValueError) as exc:
        load_config(path)
    reason = str(exc.value).splitlines()[0]
    assert reason.startswith("invalid TOML")
    assert shown.images[0] == render_message("CONFIG ERROR", reason).tobytes()


def test_start_without_error_does_not_wait(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(f'display = "png"\npng_path = "{tmp_path / "s.png"}"\n')
    waits = []
    cfg, display = start(path, sleep=waits.append)
    assert waits == []
    assert isinstance(display.backend, PngBackend)
    assert Image.open(tmp_path / "s.png").size == (250, 122)


def test_start_falls_back_to_png_when_display_fails(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text(f'display = "V4"\npng_path = "{tmp_path / "s.png"}"\n')

    def broken(cfg):
        raise RuntimeError("no SPI")

    cfg, display = start(path, sleep=lambda s: None, backend_factory=broken)
    assert isinstance(display.backend, PngBackend)
    assert display.backend.path == tmp_path / "s.png"
    assert (tmp_path / "s.png").exists()


def test_start_survives_failing_startup_screen(tmp_path: Path):
    path = tmp_path / "c.toml"
    path.write_text('display = "png"\n')

    class Broken:
        def show(self, image, full):
            raise OSError("panel gone")

    cfg, display = start(path, sleep=lambda s: None, backend_factory=lambda cfg: Broken())
    assert display.backend.__class__ is Broken
