"""MCP over Streamable HTTP (spec 2025-06-18), standard library only.

One endpoint (default /mcp). Every request needs `Authorization: Bearer <token>` unless the owner
explicitly chooses `require_auth=False` (the CLI's `--no-auth`, refused on any non-loopback address).
It listens on 127.0.0.1 unless told otherwise. Origin (and Host on loopback) are checked either way,
so a web page cannot drive it even without a token.

What is implemented, deliberately small:
- POST: a JSON-RPC request gets a JSON reply; a notification or reply gets 202 with no body.
- sessions: `initialize` returns an `Mcp-Session-Id`; later requests must carry it (400 if missing,
  404 if unknown, so a client re-initialises after a restart); DELETE ends a session.
- GET answers 405: there is no server-initiated stream, which the spec permits.
- Origin is validated (browsers send it, and a web page must not be able to drive this), and on a
  loopback listener so is Host (DNS rebinding).
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .mcp_server import PARSE_ERROR, SUPPORTED_VERSIONS

MAX_BODY = 1_000_000
DRAIN_LIMIT = 8 * MAX_BODY  # how much of an oversized body is read and thrown away so the 413 arrives
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "[::1]"}


def _hostname(value):
    """'localhost:8780' -> 'localhost'; '[::1]:8780' -> '[::1]'."""
    if value.startswith("["):
        return value.split("]", 1)[0] + "]"
    return value.split(":", 1)[0]


class McpHttp:
    def __init__(self, server, token, *, host="127.0.0.1", path="/mcp", allow_origins=(), require_auth=True):
        self.loopback = host in ("127.0.0.1", "localhost", "::1")
        if require_auth and not token:
            raise ValueError("an access token is required")
        if not require_auth and not self.loopback:
            raise ValueError("running without a token is only allowed on a loopback address")
        self.server, self.token, self.path = server, token or "", path
        self.require_auth = require_auth
        self.allowed_hosts = LOOPBACK_NAMES | ({host} if self.loopback is False and host != "0.0.0.0" else set())
        self.allowed_origins = set(allow_origins)
        self.sessions = {}  # session id -> negotiated protocol version
        self._lock = threading.Lock()

    # --- checks -------------------------------------------------------------------------
    def host_ok(self, host_header):
        return not self.loopback or _hostname(host_header or "") in self.allowed_hosts

    def origin_ok(self, origin):
        if not origin:
            return True  # not a browser
        if origin in self.allowed_origins:
            return True
        return _hostname(origin.split("://", 1)[-1]) in LOOPBACK_NAMES

    def token_ok(self, header):
        if not self.require_auth:
            return True
        scheme, _, supplied = (header or "").partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(supplied.strip().encode(), self.token.encode())

    # --- sessions -----------------------------------------------------------------------
    def new_session(self, protocol):
        sid = secrets.token_hex(16)
        with self._lock:
            self.sessions[sid] = protocol
        return sid

    def session_protocol(self, sid):
        with self._lock:
            return self.sessions.get(sid)

    def end_session(self, sid):
        with self._lock:
            return self.sessions.pop(sid, None) is not None


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "velop-doctor-mcp"
        timeout = 30  # a stalled client must not hold a thread forever

        def _drain(self, length):
            """Discard (a bounded amount of) a rejected body; closing with it unread resets the client."""
            remaining = min(length, DRAIN_LIMIT)
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            self.close_connection = True

        def log_message(self, *args):
            pass

        def _send(self, status, payload=None, headers=None):
            body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            if payload is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _gate(self):
            """Host, Origin, then token, in that order. Returns True if the request may proceed."""
            if urlparse(self.path).path != app.path:
                self._send(404, {"error": "not found"})
                return False
            if not app.host_ok(self.headers.get("Host")):
                self._send(403, {"error": "host not allowed"})
                return False
            if not app.origin_ok(self.headers.get("Origin")):
                self._send(403, {"error": "origin not allowed"})
                return False
            if not app.token_ok(self.headers.get("Authorization")):
                self._send(401, {"error": "unauthorized"}, {"WWW-Authenticate": 'Bearer realm="velop-doctor"'})
                return False
            version = self.headers.get("MCP-Protocol-Version")
            if version and version not in SUPPORTED_VERSIONS:
                self._send(400, {"error": f"unsupported MCP-Protocol-Version {version[:20]}"})
                return False
            return True

        def do_POST(self):
            if not self._gate():
                return
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(415, {"error": "Content-Type must be application/json"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._send(400, {"error": "bad Content-Length"})
            if length > MAX_BODY:
                self._drain(length)
                return self._send(413, {"error": "request too large"}, {"Connection": "close"})
            try:
                message = json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": PARSE_ERROR, "message": "parse error"}})

            is_initialize = isinstance(message, dict) and message.get("method") == "initialize"
            protocol = None
            if not is_initialize:
                sid = self.headers.get("Mcp-Session-Id")
                if not sid:
                    return self._send(400, {"error": "missing Mcp-Session-Id: send initialize first"})
                protocol = app.session_protocol(sid)
                if protocol is None:
                    return self._send(404, {"error": "unknown session: send initialize again"})

            reply = app.server.handle(message, protocol=protocol)
            if reply is None:
                return self._send(202)  # notifications and replies get no body
            headers = {}
            if is_initialize and isinstance(reply, dict) and "result" in reply:
                headers["Mcp-Session-Id"] = app.new_session(reply["result"]["protocolVersion"])
            self._send(200, reply, headers)

        def do_GET(self):
            if self._gate():
                self._send(405, {"error": "this server has no event stream; use POST"}, {"Allow": "POST, DELETE"})

        def do_DELETE(self):
            if not self._gate():
                return
            sid = self.headers.get("Mcp-Session-Id")
            if sid and app.end_session(sid):
                return self._send(204)
            self._send(404, {"error": "unknown session"})

    return Handler


def serve_http(server, token, host, port, **options):  # options: allow_origins, require_auth
    app = McpHttp(server, token, host=host, **options)
    return app, ThreadingHTTPServer((host, port), make_handler(app))
