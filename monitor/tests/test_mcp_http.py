import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from velop_doctor import config
from velop_doctor.mcp_http import McpHttp, serve_http
from velop_doctor.mcp_server import McpServer, Tool, ToolResult, object_schema

TOKEN = "test-token-123"


def echo_server():
    return McpServer("t", "1", [
        Tool("echo", "Echo", "Echoes.", object_schema({"text": {"type": "string"}}, required=["text"]),
             lambda a: ToolResult(a["text"], {"echo": a["text"]})),
    ])


class HttpCase(unittest.TestCase):
    host = "127.0.0.1"
    allow_origins = ()

    @classmethod
    def setUpClass(cls):
        cls.app, cls.httpd = serve_http(echo_server(), TOKEN, cls.host, 0, allow_origins=cls.allow_origins)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/mcp"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method="POST", body=None, token=TOKEN, session=None, headers=None, raw=None, path=""):
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        if session:
            h["Mcp-Session-Id"] = session
        h.update(headers or {})
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.url + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                text = resp.read()
                return resp.status, (json.loads(text) if text else None), dict(resp.headers)
        except urllib.error.HTTPError as e:
            text = e.read()
            e.close()
            return e.code, (json.loads(text) if text else None), dict(e.headers)

    def initialize(self, version="2025-06-18"):
        status, body, headers = self.request(body={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version}})
        self.assertEqual(status, 200)
        return headers["Mcp-Session-Id"], body

    def call(self, session, **args):
        return self.request(body={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "echo", "arguments": args}}, session=session)


class AuthTests(HttpCase):
    def test_no_token_or_wrong_token_is_401_and_says_how_to_authenticate(self):
        for token in (None, "wrong", TOKEN + "x", TOKEN[:-1]):
            with self.subTest(token=token):
                status, _, headers = self.request(body={"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=token)
                self.assertEqual(status, 401)
                self.assertIn("Bearer", headers["WWW-Authenticate"])

    def test_other_auth_schemes_are_refused(self):
        status, _, _ = self.request(body={}, token=None, headers={"Authorization": f"Basic {TOKEN}"})
        self.assertEqual(status, 401)

    def test_every_method_is_gated(self):
        for method in ("GET", "DELETE", "POST"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method=method, body={} if method == "POST" else None, token=None)[0], 401)

    def test_unauthenticated_requests_learn_nothing_about_sessions(self):
        session, _ = self.initialize()
        self.assertEqual(self.request(method="DELETE", token=None, session=session)[0], 401)
        self.assertEqual(self.request(method="DELETE", session=session)[0], 204)

    def test_only_the_mcp_path_exists(self):
        self.assertEqual(self.request(path="/other", body={})[0], 404)


class BrowserProtectionTests(HttpCase):
    def test_foreign_origin_is_refused_even_with_the_token(self):
        status, _, _ = self.request(body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_local_origin_is_fine(self):
        status, _, _ = self.request(body={"jsonrpc": "2.0", "id": 1, "method": "ping"}, session=self.initialize()[0],
                                    headers={"Origin": "http://localhost:3000"})
        self.assertEqual(status, 200)

    def test_rebinding_host_header_is_refused_on_a_loopback_listener(self):
        status, _, _ = self.request(body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    headers={"Host": "evil.example"})
        self.assertEqual(status, 403)


class ProtocolTests(HttpCase):
    def test_initialize_issues_a_session_and_negotiates(self):
        session, body = self.initialize("2025-03-26")
        self.assertEqual(len(session), 32)
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(body["result"]["serverInfo"]["name"], "t")

    def test_requests_need_a_known_session(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(body=ping)[0], 400)
        self.assertEqual(self.request(body=ping, session="0" * 32)[0], 404)
        self.assertEqual(self.request(body=ping, session=self.initialize()[0])[0], 200)

    def test_tool_call_over_http(self):
        session, _ = self.initialize()
        status, body, headers = self.call(session, text="hi")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(body["result"]["content"][0]["text"], "hi")
        self.assertEqual(body["result"]["structuredContent"], {"echo": "hi"})

    def test_notifications_get_202_and_no_body(self):
        session, _ = self.initialize()
        status, body, _ = self.request(body={"jsonrpc": "2.0", "method": "notifications/initialized"}, session=session)
        self.assertEqual((status, body), (202, None))

    def test_each_session_keeps_its_own_protocol_version(self):
        new, _ = self.initialize("2025-06-18")
        old, _ = self.initialize("2024-11-05")
        self.assertIn("structuredContent", self.call(new, text="a")[1]["result"])
        self.assertNotIn("structuredContent", self.call(old, text="a")[1]["result"])
        self.assertIn("structuredContent", self.call(new, text="a")[1]["result"], "the older session must not leak into the newer one")

    def test_delete_ends_the_session(self):
        session, _ = self.initialize()
        self.assertEqual(self.request(method="DELETE", session=session)[0], 204)
        self.assertEqual(self.call(session, text="x")[0], 404)
        self.assertEqual(self.request(method="DELETE", session=session)[0], 404)

    def test_get_has_no_event_stream(self):
        status, _, headers = self.request(method="GET")
        self.assertEqual(status, 405)
        self.assertIn("POST", headers["Allow"])

    def test_bad_input(self):
        self.assertEqual(self.request(raw=b"{not json")[0], 400)
        self.assertEqual(self.request(raw=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request(raw=b"x" * 1_000_001)[0], 413)
        status, _, _ = self.request(body={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    session=self.initialize()[0], headers={"MCP-Protocol-Version": "1999-01-01"})
        self.assertEqual(status, 400)

    def test_parse_error_is_a_json_rpc_error(self):
        status, body, _ = self.request(raw=b"{not json")
        self.assertEqual((status, body["error"]["code"]), (400, -32700))

    def test_tool_errors_still_come_back_as_results(self):
        session, _ = self.initialize()
        status, body, _ = self.call(session)  # missing required argument
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])


class ExposedListenerTests(HttpCase):
    """Listening beyond loopback relies on the token and Origin, not on the Host header."""
    host = "0.0.0.0"

    def test_any_host_header_is_accepted_but_the_token_is_still_required(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(body=ping, headers={"Host": "my-mac.local"}, session=self.initialize()[0])[0], 200)
        self.assertEqual(self.request(body=ping, headers={"Host": "my-mac.local"}, token=None)[0], 401)
        self.assertFalse(self.app.loopback)

    def test_foreign_origin_is_still_refused(self):
        self.assertEqual(self.request(body={}, headers={"Origin": "https://evil.example"})[0], 403)


class NoAuthTests(HttpCase):
    """The owner may choose to run without a token on localhost; browsers are still kept out."""

    @classmethod
    def setUpClass(cls):
        cls.app, cls.httpd = serve_http(echo_server(), None, "127.0.0.1", 0, require_auth=False)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/mcp"

    def initialize(self, version="2025-06-18"):
        status, body, headers = self.request(token=None, body={
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": version}})
        self.assertEqual(status, 200)
        return headers["Mcp-Session-Id"], body

    def test_no_token_needed_and_a_stray_one_is_ignored(self):
        session, _ = self.initialize()
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(token=None, body=ping, session=session)[0], 200)
        self.assertEqual(self.request(token="whatever", body=ping, session=session)[0], 200)

    def test_browsers_and_rebinding_are_still_refused(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(token=None, body=ping, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request(token=None, body=ping, headers={"Host": "evil.example"})[0], 403)

    def test_sessions_and_everything_else_still_apply(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(token=None, body=ping)[0], 400)
        self.assertEqual(self.request(token=None, method="GET")[0], 405)

    def test_it_is_refused_on_anything_but_loopback(self):
        for host in ("0.0.0.0", "192.168.1.50"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                McpHttp(echo_server(), None, host=host, require_auth=False)

    def test_the_command_line_refuses_the_dangerous_combination_before_binding(self):
        from velop_doctor import cli
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main(["mcp", "--http", "--no-auth", "--host", "0.0.0.0", "--listen-port", "0"])
        self.assertEqual(code, 2)
        self.assertIn("Refusing to run without a token", err.getvalue())


class ExtraOriginTests(HttpCase):
    allow_origins = ("https://my-tool.example",)

    def test_an_explicitly_allowed_origin_passes(self):
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        status, _, _ = self.request(body=ping, session=self.initialize()[0], headers={"Origin": "https://my-tool.example"})
        self.assertEqual(status, 200)


class TokenFileTests(unittest.TestCase):
    def setUp(self):
        os.environ["VELOP_DOCTOR_HOME"] = tempfile.mkdtemp()

    def tearDown(self):
        os.environ.pop("VELOP_DOCTOR_HOME", None)

    def test_created_once_private_and_stable(self):
        first = config.get_mcp_token()
        self.assertGreaterEqual(len(first), 40)
        self.assertEqual(stat.S_IMODE(os.stat(config.mcp_token_path()).st_mode), 0o600)
        self.assertEqual(config.get_mcp_token(), first)

    def test_rotation_replaces_it(self):
        first = config.get_mcp_token()
        second = config.get_mcp_token(rotate=True)
        self.assertNotEqual(first, second)
        self.assertEqual(config.get_mcp_token(), second)

    def test_a_server_cannot_be_built_without_a_token(self):
        with self.assertRaises(ValueError):
            McpHttp(echo_server(), "")


if __name__ == "__main__":
    unittest.main()
