"""Background loop: collect, analyse, store."""
from __future__ import annotations

import threading
import time

from . import analyzer


class Poller(threading.Thread):
    def __init__(self, backend, store, cfg):
        super().__init__(daemon=True, name="velop-poller")
        self.backend, self.store, self.cfg = backend, store, cfg
        self.last_error = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_prune = 0

    @property
    def gap_seconds(self):
        return 3 * self.cfg.interval + 30

    def poll(self):
        try:
            snap = self.backend.collect()
        except Exception as e:  # keep the loop alive whatever a node does
            self.last_error = f"{type(e).__name__}: {e}"
            return None
        self.last_error = None
        prev = self.store.latest_snapshot()
        if prev and snap["ts"] - prev["ts"] > self.gap_seconds:
            self.store.add_event(
                snap["ts"], "info", "gap",
                f"No data for {(snap['ts'] - prev['ts']) // 60} min (computer asleep or app stopped)")
        self.store.add_snapshot(snap)
        for event in analyzer.diff_events(prev, snap):
            self.store.add_event(snap["ts"], *event)
        findings = analyzer.evaluate(snap, self.cfg)
        findings += analyzer.flapping_findings(snap, self.store.count_events, snap["ts"])
        self.store.update_conditions(snap["ts"], findings, gap_seconds=self.gap_seconds)
        if snap["ts"] - self._last_prune > 3600:
            self.store.prune(snap["ts"], self.cfg.retention_days)
            self._last_prune = snap["ts"]
        return snap

    def poll_now(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def run(self):
        while not self._stop.is_set():
            started = time.time()
            self.poll()
            self._wake.wait(max(1, self.cfg.interval - (time.time() - started)))
            self._wake.clear()
