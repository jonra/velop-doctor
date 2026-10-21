"""Local web server: JSON API plus the single-page dashboard (standard library only)."""
from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .actions import ActionError

INDEX = Path(__file__).parent / "web" / "index.html"


class App:
    def __init__(self, cfg, backend, store, poller, host="127.0.0.1"):
        self.cfg, self.backend, self.store, self.poller = cfg, backend, store, poller
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts = {"localhost", "127.0.0.1", "[::1]", host}
        self.action_lock = threading.Lock()

    def state(self):
        snap = self.store.latest_snapshot()
        findings = self.store.active_findings()
        severities = {f["severity"] for f in findings}
        health = "critical" if "critical" in severities else \
            "warning" if "warning" in severities else "ok"
        return {"version": __version__, "demo": self.backend.demo, "now": int(time.time()),
                "interval": self.cfg.interval, "snapshot": snap, "findings": findings,
                "health": health if snap else "unknown", "last_error": self.poller.last_error}

    def handle_action(self, body):
        action = body.get("action")
        params = body.get("params") or {}
        if action == "refresh":
            self.poller.poll_now()
            return 200, {"ok": True, "message": "Refreshing…"}
        snap = self.store.latest_snapshot()
        with self.action_lock:
            try:
                if not body.get("confirm"):
                    return 200, {"preview": self.backend.preview(action, params, snap)}
                result = self.backend.execute(action, params, snap)
            except ActionError as e:
                return 400, {"ok": False, "message": str(e)}
        self.poller.poll_now()
        return 200, result


def _hostname(value):
    """'localhost:8765' -> 'localhost'; '[::1]:8765' -> '[::1]'."""
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.split(":", 1)[0]


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "velop-doctor"

        def log_message(self, *args):
            pass

        def _send(self, status, payload, ctype="application/json"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            """Reject unexpected Host headers (blocks DNS-rebinding attacks)."""
            return _hostname(self.headers.get("Host") or "") in app.allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            url = urlparse(self.path)
            if url.path == "/":
                html = INDEX.read_text().replace("__TOKEN__", app.token)
                return self._send(200, html.encode(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                return self._send(200, app.state())
            if url.path == "/api/events":
                limit = int(parse_qs(url.query).get("limit", ["100"])[0])
                return self._send(200, {"events": app.store.events(min(limit, 500))})
            if url.path == "/api/history":
                minutes = int(parse_qs(url.query).get("minutes", ["60"])[0])
                since = int(time.time()) - min(minutes, 7 * 24 * 60) * 60
                return self._send(200, {"since": since, "series": app.store.history(since)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            origin = self.headers.get("Origin")
            if origin and _hostname(origin.split("://", 1)[-1]) not in app.allowed_hosts:
                return self._send(403, {"error": "bad origin"})
            if self.headers.get("X-Velop-Token") != app.token:
                return self._send(403, {"error": "missing or wrong token"})
            if urlparse(self.path).path != "/api/actions":
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid json"})
            status, payload = app.handle_action(body)
            self._send(status, payload)

    return Handler


def serve(app, host, port):
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    return httpd
