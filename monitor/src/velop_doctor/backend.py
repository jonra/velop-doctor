"""Backends the server talks to: a real Velop, or synthetic demo data."""
from __future__ import annotations

from . import actions
from .collector import collect
from .demo import make_snapshot
from .jnap import JnapClient


class LiveBackend:
    demo = False

    def __init__(self, cfg, password):
        self.cfg = cfg
        self.password = password

    def _client(self, host):
        return JnapClient(host, self.cfg.username, self.password)

    def collect(self):
        return collect(self.cfg, self.password)

    def preview(self, action, params, snap):
        return actions.preview(action, params, snap)

    def execute(self, action, params, snap):
        return actions.execute(action, params, snap, self._client, self.cfg)


class DemoBackend:
    """Synthetic mesh with deliberate problems, for screenshots and trying the UI."""

    demo = True

    def __init__(self, cfg, scenario="problems"):
        self.cfg = cfg
        self.scenario = scenario
        self.steering = False

    def collect(self):
        return make_snapshot(self.cfg, self.scenario, steering=self.steering)

    def preview(self, action, params, snap):
        return actions.preview(action, params, snap)

    def execute(self, action, params, snap):
        if action in ("steering_on", "steering_off"):
            self.steering = action == "steering_on"
            return {"ok": True, "message": f"(demo) Client steering is now "
                                           f"{'on' if self.steering else 'off'}."}
        actions.preview(action, params, snap)  # validates the target
        return {"ok": True, "message": "(demo) Reboot command sent."}
