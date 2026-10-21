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
