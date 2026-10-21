"""State that depends on time: link flaps and per-plug timing."""

from collections import deque


class FlapTracker:
    """Counts down→up cycles within a sliding window using carrier_changes."""

    def __init__(self, window: float = 60.0):
        self.window = window
        self.samples: deque[tuple[float, int]] = deque()

    def update(self, now: float, carrier_changes: int) -> int:
        if self.samples and carrier_changes < self.samples[-1][1]:
            self.samples.clear()  # counter reset: the dongle was re-enumerated
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
        self._carrier_changes: int | None = None

    def on_link(self, now: float, up: bool, carrier_changes: int) -> bool:
        """Returns True when this call starts a new plug, seen or inferred from carrier_changes."""
        previous, self._carrier_changes = self._carrier_changes, carrier_changes
        if not up:
            self.link_up_at = None
            self.ip_at = None
            self._saw_down = True
            return False
        if self.link_up_at is None:
            self.link_up_at = now
            self.fresh = self._saw_down
            return self.fresh
        if previous is not None and carrier_changes > previous:
            # Unplugged and replugged between two calls.
            self.link_up_at = now
            self.ip_at = None
            self.fresh = True
            return True
        return False

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
