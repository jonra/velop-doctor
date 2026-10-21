"""Local web server: JSON API plus the single-page dashboard (standard library only)."""
from __future__ import annotations

import ipaddress
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, config
from .actions import ActionError
from .diagnostics import DiagnosticError
from .jnap import AuthError, JnapClient, JnapError

INDEX = Path(__file__).parent / "web" / "index.html"


class App:
    """Holds the running pieces. Starts "unconfigured" when no Velop is set up yet."""

    def __init__(self, cfg, backend, store, poller, host="127.0.0.1", client=JnapClient,
                 guess=None, make_backend=None):
        self.cfg, self.backend, self.store, self.poller = cfg, backend, store, poller
        self.token = secrets.token_urlsafe(24)
        self.allowed_hosts = {"localhost", "127.0.0.1", "[::1]", host}
        self.action_lock = threading.Lock()
        self.diagnose_lock = threading.Lock()
        self.client, self.guess = client, guess
        self.make_backend = make_backend

    @property
    def configured(self):
        return self.backend is not None

    def attach(self, backend):
        """(Re)start polling with `backend`; stops the previous poller if any."""
        from .poller import Poller
        if self.poller:
            self.poller.stop()
        self.backend = backend
        self.poller = Poller(backend, self.store, self.cfg)
        self.poller.start()

    def state(self):
        if not self.configured:
            return {"version": __version__, "demo": False, "setup_required": True,
                    "now": int(time.time()), "primary": self.cfg.primary or self.guess or "",
                    "username": self.cfg.username, "snapshot": None, "findings": [],
                    "health": "unknown", "last_error": None, "interval": self.cfg.interval}
        snap = self.store.latest_snapshot()
        findings = self.store.active_findings()
        severities = {f["severity"] for f in findings}
        health = "critical" if "critical" in severities else \
            "warning" if "warning" in severities else "ok"
        now = int(time.time())
        return {"version": __version__, "demo": self.backend.demo, "now": now,
                "interval": self.cfg.interval, "snapshot": snap, "findings": findings,
                "health": health if snap else "unknown", "last_error": self.poller.last_error,
                "availability_24h": self.store.availability(now - 86400),
                "primary": self.cfg.primary, "username": self.cfg.username}

    def setup(self, body):
        """Validate credentials against the Velop, store them, start monitoring."""
        host = str(body.get("primary") or "").strip()
        user = str(body.get("username") or "admin").strip() or "admin"
        password = body.get("password") or ""
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            return 400, {"ok": False, "message": "Enter the IP address of your main node, for example 192.168.1.1."}
        if not (addr.is_private or addr.is_link_local):
            return 400, {"ok": False, "message": "That is not a local network address. The password is only ever sent to your own network."}
        if not password:
            return 400, {"ok": False, "message": "Enter the admin password."}
        try:
            self.client(host, user, password).call("core/GetDeviceInfo", auth=False)
        except JnapError:
            return 400, {"ok": False, "message": f"No Linksys node answered at {host}."}
        try:
            self.client(host, user, password).call("devicelist/GetDevices3")
        except AuthError:
            return 400, {"ok": False, "message": "The node rejected that password."}
        except JnapError as e:
            return 400, {"ok": False, "message": f"The login test failed ({e.result})."}
        self.cfg.primary, self.cfg.username = host, user
        config.save(self.cfg)
        where = config.set_password(self.cfg, password)
        from .backend import LiveBackend
        self.attach((self.make_backend or LiveBackend)(self.cfg, password))
        return 200, {"ok": True, "message": f"Connected. Password stored in {where}."}

    def handle_diagnose(self, body):
        """Run a validated ping or DNS lookup. One at a time: they can take half a minute."""
        if not self.configured:
            return 400, {"ok": False, "message": "Connect to your Velop first."}
        if not self.diagnose_lock.acquire(blocking=False):
            return 429, {"ok": False, "message": "Another diagnostic is still running; try again shortly."}
        try:
            result = self.backend.diagnose(body.get("tool"), body.get("params") or {},
                                           self.store.latest_snapshot())
            return 200, {"ok": True, "result": result}
        except DiagnosticError as e:
            return 400, {"ok": False, "message": str(e)}
        finally:
            self.diagnose_lock.release()

    def handle_action(self, body):
        if not self.configured:
            return 400, {"ok": False, "message": "Connect to your Velop first."}
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
            if url.path == "/api/token":
                # Same exposure as the page itself (which embeds the token): browsers on other
                # origins cannot read it, and the Host check blocks DNS rebinding.
                return self._send(200, {"token": app.token})
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
            path = urlparse(self.path).path
            if path not in ("/api/actions", "/api/setup", "/api/diagnose"):
                return self._send(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid json"})
            handler = {"/api/setup": app.setup, "/api/diagnose": app.handle_diagnose}.get(path, app.handle_action)
            status, payload = handler(body)
            self._send(status, payload)

    return Handler


def serve(app, host, port):
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    return httpd
