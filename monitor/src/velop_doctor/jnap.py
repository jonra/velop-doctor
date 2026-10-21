"""Minimal client for the Linksys JNAP local API (the one the Linksys app uses)."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

BASE = "http://linksys.com/jnap/"


class JnapError(Exception):
    """The node answered, but with an error result."""

    def __init__(self, result, action=""):
        super().__init__(f"{action}: {result}" if action else str(result))
        self.result = result
        self.action = action


class AuthError(JnapError):
    """The node rejected the admin credentials."""


class Unreachable(JnapError):
    """The node did not answer (timeout, refused, not JSON)."""


class JnapClient:
    def __init__(self, host, username="admin", password=None, timeout=6.0):
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout

    def _auth_header(self):
        raw = f"{self.username}:{self.password or ''}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def call(self, action, payload=None, *, auth=True):
        """Call one JNAP action and return its `output` dict.

        Never puts the server's error text in exceptions: a failed login echoes
        the submitted credentials back in it.
        """
        name = action if action.startswith("http") else BASE + action
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-JNAP-Action": name,
        }
        if auth:
            headers["X-JNAP-Authorization"] = self._auth_header()
        req = urllib.request.Request(
            f"http://{self.host}/JNAP/",
            data=json.dumps(payload or {}).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AuthError("_ErrorUnauthorized", action) from None
            raise Unreachable(f"http {e.code}", action) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Unreachable(type(e).__name__, action) from None

        result = data.get("result")
        if result == "OK":
            return data.get("output") or {}
        if result == "_ErrorUnauthorized":
            raise AuthError(result, action)
        raise JnapError(result or "unknown", action)
