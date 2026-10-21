import http.server
import json
import threading
import unittest
import urllib.error
import urllib.request

from velop_doctor import actions
from velop_doctor.backend import DemoBackend
from velop_doctor.config import Config
from velop_doctor.demo import make_snapshot
from velop_doctor.jnap import AuthError, JnapClient, JnapError, Unreachable
from velop_doctor.poller import Poller
from velop_doctor.server import App, serve
from velop_doctor.store import Store

from fakes import FakeClient, cfg, world


class FakeNode(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        action = self.headers.get("X-JNAP-Action", "")
        auth = self.headers.get("X-JNAP-Authorization")
        if action.endswith("GetDeviceInfo"):
            body = {"result": "OK", "output": {"serialNumber": "S1"}}
        elif auth is None or auth != "Basic YWRtaW46Z29vZA==":  # admin:good
            body = {"result": "_ErrorUnauthorized", "error": "Invalid authorization credentials 'SECRET'"}
        elif action.endswith("Boom"):
            body = {"result": "_ErrorUnknownAction"}
        else:
            body = {"result": "OK", "output": {"x": 1}}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class JnapClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeNode)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.host = f"127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_ok_unauth_and_auth(self):
        self.assertEqual(JnapClient(self.host).call("core/GetDeviceInfo", auth=False), {"serialNumber": "S1"})
        self.assertEqual(JnapClient(self.host, "admin", "good").call("any/Thing"), {"x": 1})

    def test_wrong_password_raises_without_leaking_server_text(self):
        with self.assertRaises(AuthError) as cm:
            JnapClient(self.host, "admin", "bad").call("any/Thing")
        self.assertNotIn("SECRET", str(cm.exception))
        self.assertNotIn("bad", str(cm.exception))

    def test_other_errors_and_unreachable(self):
        with self.assertRaises(JnapError):
            JnapClient(self.host, "admin", "good").call("any/Boom")
        with self.assertRaises(Unreachable):
            JnapClient("127.0.0.1:1", timeout=1).call("core/GetDeviceInfo", auth=False)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()
        self.snap = make_snapshot(self.cfg, "problems")
        self.snap["nodes"][0]["ip"] = "192.168.1.1"

    def test_steering_preview_noop_when_already_set(self):
        self.snap["steering"] = True
        self.assertTrue(actions.preview("steering_on", {}, self.snap)["noop"])
        self.assertFalse(actions.preview("steering_off", {}, self.snap)["noop"])

    def test_steering_apply_reads_back_and_changes_only_that_flag(self):
        state = {"isClientSteeringEnabled": False, "other": 7}
        calls = []

        def setter(payload):
            state.update(payload)
            return {}
        table = {"192.168.1.1": {actions.STEERING_GET: lambda p: dict(state), actions.STEERING_SET: setter}}
        result = actions.execute("steering_on", {}, self.snap,
                                 lambda h: FakeClient(table, h, calls), self.cfg)
        self.assertTrue(result["ok"])
        self.assertEqual(state, {"isClientSteeringEnabled": True, "other": 7})

    def test_failed_write_is_reported_honestly(self):
        table = {"192.168.1.1": {actions.STEERING_GET: {"isClientSteeringEnabled": False},
                                 actions.STEERING_SET: {}}}
        result = actions.execute("steering_on", {}, self.snap, lambda h: FakeClient(table, h), self.cfg)
        self.assertFalse(result["ok"])

    def test_reboot_only_known_nodes_and_warns_for_main(self):
        with self.assertRaises(actions.ActionError):
            actions.preview("reboot_node", {"ip": "10.0.0.99"}, self.snap)
        main = [n for n in self.snap["nodes"] if n["role"] == "primary"][0]
        pv = actions.preview("reboot_node", {"ip": main["ip"]}, self.snap)
        self.assertEqual(pv["risk"], "high")
        self.assertIn("MAIN", pv["effects"][0])
        hall = actions.preview("reboot_node", {"ip": "192.168.1.96"}, self.snap)
        self.assertEqual(hall["risk"], "medium")

    def test_reboot_tolerates_dropped_connection(self):
        table = {"192.168.1.96": {"core/Reboot": Unreachable("reset")}}
        r = actions.execute("reboot_node", {"ip": "192.168.1.96"}, self.snap,
                            lambda h: FakeClient(table, h), self.cfg)
        self.assertTrue(r["ok"])


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = Config(primary="192.168.1.1", interval=3600)
        cls.backend, cls.store = DemoBackend(c), Store(":memory:")
        cls.poller = Poller(cls.backend, cls.store, c)
        snap = cls.poller.poll()
        # critical findings only become active after their min_seconds (60 s)
        from velop_doctor import analyzer
        cls.store.update_conditions(snap["ts"] + 120, analyzer.evaluate(snap, c))
        cls.app = App(c, cls.backend, cls.store, cls.poller)
        cls.httpd = serve(cls.app, "127.0.0.1", 0)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, path, body=None, headers=None):
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        r = urllib.request.Request(self.base + path, data=data, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_state_has_findings_and_health(self):
        status, raw = self.req("/api/state")
        data = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(data["health"], "critical")
        self.assertTrue(data["demo"])
        self.assertGreater(len(data["snapshot"]["nodes"]), 2)

    def test_index_embeds_token_and_nothing_else_is_templated(self):
        status, raw = self.req("/")
        self.assertEqual(status, 200)
        self.assertIn(self.app.token, raw.decode())
        self.assertNotIn("__TOKEN__", raw.decode())

    def test_action_needs_token(self):
        status, _ = self.req("/api/actions", {"action": "steering_on"})
        self.assertEqual(status, 403)

    def test_rebinding_host_is_rejected(self):
        status, _ = self.req("/api/state", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_foreign_origin_is_rejected(self):
        status, _ = self.req("/api/actions", {"action": "refresh"},
                             {"X-Velop-Token": self.app.token, "Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_preview_then_confirm(self):
        hdr = {"X-Velop-Token": self.app.token}
        status, raw = self.req("/api/actions", {"action": "steering_on"}, hdr)
        self.assertIn("preview", json.loads(raw))
        self.assertFalse(self.backend.steering)
        status, raw = self.req("/api/actions", {"action": "steering_on", "confirm": True}, hdr)
        self.assertTrue(json.loads(raw)["ok"])
        self.assertTrue(self.backend.steering)
        self.backend.steering = False

    def test_unknown_node_is_a_400(self):
        status, _ = self.req("/api/actions", {"action": "reboot_node", "params": {"ip": "8.8.8.8"}},
                             {"X-Velop-Token": self.app.token})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
