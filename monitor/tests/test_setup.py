import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from velop_doctor import config
from velop_doctor.backend import DemoBackend
from velop_doctor.config import Config
from velop_doctor.jnap import AuthError, Unreachable
from velop_doctor.server import App, serve
from velop_doctor.store import Store


class FakeVelop:
    def __init__(self, host, user, password, answers):
        self.host, self.user, self.password, self.answers = host, user, password, answers

    def call(self, action, payload=None, *, auth=True):
        if self.host not in self.answers:
            raise Unreachable("down", action)
        if auth and self.password != self.answers[self.host]:
            raise AuthError("_ErrorUnauthorized", action)
        return {"serialNumber": "S"} if "GetDeviceInfo" in action else {"devices": []}


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["VELOP_DOCTOR_HOME"] = self.home
        self.stored = []
        self._orig = config.set_password
        config.set_password = lambda cfg, pw: self.stored.append(pw) or "test store"
        self.cfg = Config()
        self.app = App(self.cfg, None, Store(":memory:"), None, guess="192.168.1.1",
                       client=lambda h, u, p: FakeVelop(h, u, p, {"192.168.1.1": "right"}),
                       make_backend=lambda cfg, pw: DemoBackend(cfg))
        self.httpd = serve(self.app, "127.0.0.1", 0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        config.set_password = self._orig
        os.environ.pop("VELOP_DOCTOR_HOME", None)
        self.httpd.shutdown()
        if self.app.poller:
            self.app.poller.stop()

    def post(self, path, body, token=True):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Velop-Token"] = self.app.token
        r = urllib.request.Request(self.base + path, json.dumps(body).encode(), h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_unconfigured_state_asks_for_setup_and_suggests_the_gateway(self):
        with urllib.request.urlopen(self.base + "/api/state") as r:
            state = json.loads(r.read())
        self.assertTrue(state["setup_required"])
        self.assertEqual(state["primary"], "192.168.1.1")
        self.assertIsNone(state["snapshot"])

    def test_actions_are_refused_before_setup(self):
        status, body = self.post("/api/actions", {"action": "steering_on"})
        self.assertEqual(status, 400)

    def test_setup_needs_the_token(self):
        status, _ = self.post("/api/setup", {"primary": "192.168.1.1", "password": "right"}, token=False)
        self.assertEqual(status, 403)

    def test_rejects_non_ip_and_public_addresses_without_contacting_them(self):
        for bad in ("router.local", "8.8.8.8", ""):
            status, body = self.post("/api/setup", {"primary": bad, "password": "right"})
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.stored, [])

    def test_wrong_password_saves_nothing(self):
        status, body = self.post("/api/setup", {"primary": "192.168.1.1", "password": "wrong"})
        self.assertEqual(status, 400)
        self.assertIn("rejected", body["message"])
        self.assertEqual(self.stored, [])
        self.assertFalse(self.app.configured)
        self.assertFalse(os.path.exists(config.config_path()))

    def test_no_node_at_that_address(self):
        status, body = self.post("/api/setup", {"primary": "192.168.1.77", "password": "right"})
        self.assertEqual(status, 400)
        self.assertIn("No Linksys node", body["message"])

    def test_success_stores_password_writes_config_and_starts_monitoring(self):
        status, body = self.post("/api/setup", {"primary": "192.168.1.1", "username": "admin", "password": "right"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.stored, ["right"])
        self.assertTrue(self.app.configured)
        with open(config.config_path()) as f:
            saved = json.load(f)
        self.assertEqual(saved["primary"], "192.168.1.1")
        self.assertNotIn("right", json.dumps(saved), "the password must never be in the config file")
        self.assertNotIn("right", json.dumps(body))
        self.assertFalse(self.app.state().get("setup_required"))


if __name__ == "__main__":
    unittest.main()
