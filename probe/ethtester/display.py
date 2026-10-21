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
    """release() runs before every re-init: epd.init() opens SPI again without closing it."""

    def __init__(self, epd, release=None):
        self.epd = epd
        self.release = release
        self._initialised = False
        self._init()
        self.epd.Clear(0xFF)

    def _init(self) -> None:
        if self._initialised and self.release is not None:
            self.release()
        self.epd.init()
        self._initialised = True

    def show(self, image, full: bool) -> None:
        buf = self.epd.getbuffer(image)
        if full:
            self._init()
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
    return WaveshareBackend(driver.EPD(), release=driver.epdconfig.module_exit)
