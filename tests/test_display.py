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
    backend = WaveshareBackend(epd, release=lambda: epd.calls.append("release"))
    backend.show(img(255), full=True)
    backend.show(img(255), full=False)
    backend.show(img(0), full=True)
    assert epd.calls == ["init", "Clear(0xff)", "release", "init", "base", "partial",
                         "release", "init", "base"]


def test_waveshare_backend_every_later_init_follows_one_release():
    epd = FakeEPD()
    backend = WaveshareBackend(epd, release=lambda: epd.calls.append("release"))
    for full in (True, True, False, True):
        backend.show(img(255), full=full)
    inits = [i for i, call in enumerate(epd.calls) if call == "init"]
    assert epd.calls[:inits[0]].count("release") == 0
    for before, after in zip(inits, inits[1:]):
        assert epd.calls[before:after].count("release") == 1
        assert epd.calls[after - 1] == "release"


def test_make_backend_png(tmp_path: Path):
    backend = make_backend(Config(display="png", png_path=str(tmp_path / "s.png")))
    assert isinstance(backend, PngBackend)
