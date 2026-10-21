import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request

from velop_doctor import analyzer
from velop_doctor.backend import DemoBackend
from velop_doctor.config import Config
from velop_doctor.demo import make_snapshot
from velop_doctor.mcp_server import McpServer, Tool, ToolError, ToolResult, object_schema, validate_args
from velop_doctor.mcp_tools import Dashboard, ago, build_server, status_text
from velop_doctor.poller import Poller
from velop_doctor.server import App, serve
from velop_doctor.store import Store


# --- protocol -----------------------------------------------------------------------------

def echo_server():
    def boom(args):
        raise RuntimeError("secret internal detail")

    def refuse(args):
        raise ToolError("The node refused.")
    schema = object_schema({"text": {"type": "string", "maxLength": 5},
                            "n": {"type": "integer", "minimum": 1, "maximum": 3},
                            "mode": {"type": "string", "enum": ["a", "b"]},
                            "flag": {"type": "boolean"}}, required=["text"])
    return McpServer("t", "1.2.3", [
        Tool("echo", "Echo", "Echoes.", schema, lambda a: ToolResult(a["text"], {"echo": a["text"]})),
        Tool("listy", "Listy", "Returns a list.", object_schema(), lambda a: [1, 2]),
        Tool("boom", "Boom", "Crashes.", object_schema(), boom),
        Tool("refuse", "Refuse", "Refuses.", object_schema(), refuse),
    ], instructions="hello")


def rpc(server, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def call(server, name, args=None):
    return rpc(server, "tools/call", {"name": name, "arguments": args or {}})


class ProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_the_protocol_version(self):
        s = echo_server()
        r = rpc(s, "initialize", {"protocolVersion": "2025-03-26"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertEqual(r["serverInfo"], {"name": "t", "version": "1.2.3"})
        self.assertEqual(r["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(r["instructions"], "hello")
        self.assertEqual(rpc(s, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], "2025-06-18")

    def test_notifications_are_never_answered_and_ping_works(self):
        s = echo_server()
        self.assertIsNone(rpc(s, "notifications/initialized", id_=None))
        self.assertIsNone(rpc(s, "notifications/cancelled", {"requestId": 1}, id_=None))
        self.assertEqual(rpc(s, "ping")["result"], {})

    def test_unknown_method_and_malformed_request(self):
        s = echo_server()
        self.assertEqual(rpc(s, "resources/list")["error"]["code"], -32601)
        self.assertEqual(s.handle({"id": 4})["error"]["code"], -32600)
        self.assertEqual(s.handle("nonsense")["error"]["code"], -32600)
        self.assertEqual(s.handle({"jsonrpc": "2.0", "id": 9, "method": "x"})["id"], 9)

    def test_batches_are_answered_in_one_array(self):
        out = echo_server().handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                    {"jsonrpc": "2.0", "method": "notifications/initialized"}])
        self.assertEqual([r["id"] for r in out], [1])

    def test_tools_list_is_well_formed(self):
        tools = rpc(echo_server(), "tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["echo", "listy", "boom", "refuse"])
        for t in tools:
            self.assertTrue(t["description"] and t["title"])
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue({"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"} <= set(t["annotations"]))

    def test_call_returns_text_and_structured_content(self):
        s = echo_server()
        rpc(s, "initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(call(s, "echo", {"text": "hi"})["result"],
                         {"content": [{"type": "text", "text": "hi"}], "isError": False,
                          "structuredContent": {"echo": "hi"}})

    def test_older_protocols_get_no_structured_content(self):
        s = echo_server()
        rpc(s, "initialize", {"protocolVersion": "2024-11-05"})
        self.assertNotIn("structuredContent", call(s, "echo", {"text": "hi"})["result"])

    def test_lists_are_text_only_because_structured_content_must_be_an_object(self):
        r = call(echo_server(), "listy")["result"]
        self.assertEqual(json.loads(r["content"][0]["text"]), [1, 2])
        self.assertNotIn("structuredContent", r)

    def test_bad_arguments_are_a_readable_tool_error(self):
        cases = [({}, "missing required argument 'text'"), ({"text": "hi", "extra": 1}, "unknown argument 'extra'"),
                 ({"text": 5}, "must be of type string"), ({"text": "toolong"}, "too long"),
                 ({"text": "x", "n": 0}, "at least 1"), ({"text": "x", "n": 4}, "at most 3"),
                 ({"text": "x", "n": True}, "must be of type integer"),
                 ({"text": "x", "n": 1.5}, "must be of type integer"),
                 ({"text": "x", "mode": "z"}, "must be one of a, b"),
                 ({"text": "x", "flag": "yes"}, "must be of type boolean")]
        for args, fragment in cases:
            with self.subTest(args=args):
                r = call(echo_server(), "echo", args)["result"]
                self.assertTrue(r["isError"])
                self.assertIn(fragment, r["content"][0]["text"])

    def test_arguments_must_be_an_object(self):
        self.assertEqual(validate_args(object_schema(), ["x"]), "arguments must be an object")

    def test_unknown_tool_is_a_protocol_error(self):
        self.assertEqual(call(echo_server(), "nope")["error"]["code"], -32602)

    def test_tool_errors_are_results_and_crashes_do_not_leak(self):
        s = echo_server()
        self.assertEqual(call(s, "refuse")["result"],
                         {"content": [{"type": "text", "text": "The node refused."}], "isError": True})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            crashed = call(s, "boom")["result"]
        self.assertTrue(crashed["isError"])
        self.assertNotIn("secret internal detail", json.dumps(crashed))
        self.assertIn("secret internal detail", err.getvalue(), "logged to stderr, not shown to the model")

    def test_stdio_loop_speaks_only_protocol_on_stdout(self):
        stdin = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "", "{not json",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]) + "\n")
        out = io.StringIO()
        echo_server().serve_stdio(stdin, out)
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([m.get("id") for m in lines], [1, None, 2])
        self.assertEqual(lines[1]["error"]["code"], -32700)
        self.assertTrue(all(m["jsonrpc"] == "2.0" for m in lines))


# --- tools against a real dashboard ---------------------------------------------------------

class TickingDemo(DemoBackend):
    """Demo data whose clock advances 100 s per poll: a re-check yields a newer snapshot, and
    findings that need to persist (60-300 s) are active no matter which test ran first."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.t = 1_800_000_000

    def collect(self):
        self.t += 100
        return make_snapshot(self.cfg, "problems", steering=self.steering, now=self.t)


class DashboardCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = Config(primary="192.168.1.1", interval=3600)
        cls.backend, cls.store = TickingDemo(cfg), Store(":memory:")
        cls.poller = Poller(cls.backend, cls.store, cfg)
        for _ in range(3):
            snap = cls.poller.poll()
        cls.store.update_conditions(snap["ts"] + 120, analyzer.evaluate(snap, cfg))  # critical ones become active
        cls.app = App(cfg, cls.backend, cls.store, cls.poller)
        cls.httpd = serve(cls.app, "127.0.0.1", 0)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        self.backend.steering = False
        self.poller.poll()  # the stored snapshot must agree, or a previous test's state leaks in
        self.server = build_server(Dashboard(self.url), sleep=lambda s: self.poller.poll())

    def run_tool(self, tool, **args):
        r = call(self.server, tool, args)["result"]
        return r["content"][0]["text"], r["isError"], r.get("structuredContent")


class ToolTests(DashboardCase):
    def test_get_status_is_a_readable_report(self):
        text, err, _ = self.run_tool("get_status")
        assert not err
        assert "Health: PROBLEM" in text
        assert "Bedroom is out of sync" in text
        assert "Hall 192.168.1.96" in text and "(main)" in text
        assert text.count("\n") < 40, "a status report should be short"

    def test_list_findings_filters_by_severity(self):
        _, _, everything = self.run_tool("list_findings")
        _, _, serious = self.run_tool("list_findings", min_severity="critical")
        assert {f["severity"] for f in serious["findings"]} == {"critical"}
        assert len(everything["findings"]) > len(serious["findings"])

    def test_list_nodes_reports_sync_and_membership(self):
        _, _, data = self.run_tool("list_nodes")
        status = {n["name"]: n["status"] for n in data["nodes"]}
        assert status["Bedroom"].startswith("OUT OF SYNC")
        assert status["Old Bedroom (stale)"] == "registered but NOT in the mesh"
        assert status["Hall"] == "up"
        hall = [n for n in data["nodes"] if n["name"] == "Hall"][0]
        assert "periodic measurement" in hall["backhaul"]["note"]

    def test_list_devices_filters_and_sorts_weakest_first(self):
        _, _, everyone = self.run_tool("list_devices")
        signals = [d["signal_dbm"] for d in everyone["devices"] if d["signal_dbm"] is not None]
        assert signals == sorted(signals)
        _, _, weak = self.run_tool("list_devices", max_signal_dbm=-80)
        assert weak["matching"] >= 2 and all(d["signal_dbm"] <= -80 for d in weak["devices"])
        _, _, hall = self.run_tool("list_devices", node="hall")
        assert {d["node"] for d in hall["devices"]} == {"Hall"}
        _, _, wired = self.run_tool("list_devices", band="wired")
        assert all(d["connection"] == "wired" for d in wired["devices"])
        _, _, one = self.run_tool("list_devices", limit=1)
        assert one["shown"] == 1 and one["matching"] > 1
        _, _, named = self.run_tool("list_devices", name_contains="NEST")
        assert [d["name"] for d in named["devices"]] == ["Nest cam"]

    def test_get_events_and_history_and_connection_tests(self):
        text, err, data = self.run_tool("get_events", limit=5)
        assert not err and len(data["events"]) <= 5
        _, err, hist = self.run_tool("get_latency_history", minutes=1440, target="node:Hall")
        assert not err and list(hist["series"]) == ["node:Hall"]
        stats = hist["series"]["node:Hall"]
        assert stats["samples"] >= 3 and stats["answered_pct"] == 100.0
        text, err, _ = self.run_tool("get_latency_history", target="nothing-like-this")
        assert err and "No samples" in text
        _, _, tests = self.run_tool("get_connection_tests")
        assert {"dns", "tcp_443", "path_mtu", "internet_from_the_router", "how_to_read"} <= set(tests)

    def test_run_diagnostics_waits_for_a_newer_snapshot(self):
        before = self.store.latest_snapshot()["ts"]
        text, err, _ = self.run_tool("run_diagnostics")
        assert not err and text.startswith("Re-checked just now.")
        assert self.store.latest_snapshot()["ts"] > before

    def test_run_diagnostics_gives_up_with_a_reason(self):
        ticks = iter(range(0, 1000, 20))
        slow = build_server(Dashboard(self.url), sleep=lambda s: None, clock=lambda: next(ticks))
        r = call(slow, "run_diagnostics")["result"]
        assert r["isError"] and "did not finish" in r["content"][0]["text"]

    def test_ping_compares_computer_and_router(self):
        text, err, data = self.run_tool("ping", host="1.1.1.1", count=3)
        assert not err and "from this computer" in text and "from the router" in text
        assert text.endswith("both ends see a healthy path.")
        assert data["host"] == "1.1.1.1"
        text, _, _ = self.run_tool("ping", host="1.1.1.1", **{"from": "computer"})
        assert "from the router" not in text

    def test_ping_rejects_hostile_hosts_through_the_whole_stack(self):
        for host in ("-c 99999", "a;reboot", "$(id)", "::1"):
            text, err, _ = self.run_tool("ping", host=host)
            self.assertTrue(err, host)
            self.assertTrue("valid" in text.lower() or "must be" in text.lower(), (host, text))

    def test_dns_lookup(self):
        text, err, data = self.run_tool("dns_lookup", name="example.com")
        assert not err and "resolved in" in text and data["server"] == "192.168.1.1"
        text, err, _ = self.run_tool("dns_lookup", name="example.com", server="cloud")
        assert err


class ActionTests(DashboardCase):
    def test_steering_preview_changes_nothing(self):
        text, err, _ = self.run_tool("set_client_steering", enabled=True)
        assert not err and text.startswith("PREVIEW (nothing has been changed)")
        assert "confirm=true" in text and self.backend.steering is False

    def test_steering_confirm_applies_and_noop_is_reported(self):
        text, err, _ = self.run_tool("set_client_steering", enabled=True, confirm=True)
        assert not err and self.backend.steering is True
        self.poller.poll()  # the real poller thread refreshes the snapshot after an action
        text, _, _ = self.run_tool("set_client_steering", enabled=True)
        assert "already on" in text and "No action needed" in text

    def test_reboot_preview_by_name_ip_or_serial_suffix(self):
        for ref in ("Hall", "hall", "192.168.1.96", "OHALL"):  # name, IP, last 5 of the serial
            text, err, _ = self.run_tool("reboot_node", node=ref)
            assert not err and "PREVIEW" in text and "Reboot Hall" in text, (ref, text)

    def test_rebooting_the_main_node_is_flagged_high_risk(self):
        text, _, _ = self.run_tool("reboot_node", node="192.168.1.1")
        assert "MAIN node" in text and "Risk: high" in text

    def test_reboot_unknown_or_stale_node_lists_the_real_ones(self):
        text, err, _ = self.run_tool("reboot_node", node="Garage")
        assert err and "Hall (192.168.1.96)" in text
        text, err, _ = self.run_tool("reboot_node", node="Old Bedroom (stale)")
        assert err, "a node that is not in the mesh has nothing to reboot"

    def test_reboot_confirm_sends_the_command(self):
        text, err, _ = self.run_tool("reboot_node", node="Hall", confirm=True)
        assert not err and "Reboot command sent" in text

    def test_read_only_mode_does_not_offer_actions(self):
        ro = build_server(Dashboard(self.url), read_only=True)
        names = {t["name"] for t in rpc(ro, "tools/list")["result"]["tools"]}
        assert "get_status" in names and "ping" in names
        assert not names & {"set_client_steering", "reboot_node"}
        assert call(ro, "reboot_node", {"node": "Hall"})["error"]["code"] == -32602

    def test_action_tools_are_annotated_honestly(self):
        tools = {t["name"]: t["annotations"] for t in rpc(self.server, "tools/list")["result"]["tools"]}
        assert tools["get_status"]["readOnlyHint"] is True
        assert tools["reboot_node"]["destructiveHint"] is True and tools["reboot_node"]["readOnlyHint"] is False
        assert tools["set_client_steering"]["destructiveHint"] is False
        assert tools["ping"]["openWorldHint"] is True and tools["ping"]["readOnlyHint"] is False
        for name, a in tools.items():
            if a["readOnlyHint"]:
                assert a["destructiveHint"] is False, name


class ClientTests(DashboardCase):
    def test_a_stale_token_is_refreshed_once(self):
        dash = Dashboard(self.url)
        dash._token = "left over from a previous run"
        server = build_server(dash)
        r = call(server, "set_client_steering", {"enabled": True})["result"]
        assert not r["isError"] and "PREVIEW" in r["content"][0]["text"]

    def test_an_older_dashboard_is_reported_as_needing_a_restart(self):
        server = build_server(Dashboard(self.url + "/old-version"))
        r = call(server, "get_status")["result"]
        self.assertTrue(r["isError"])
        self.assertIn("restart `velop-doctor serve`", r["content"][0]["text"])

    def test_unreachable_dashboard_explains_how_to_start_it(self):
        server = build_server(Dashboard("http://127.0.0.1:1", timeout=1))
        r = call(server, "get_status")["result"]
        assert r["isError"] and "velop-doctor serve" in r["content"][0]["text"]

    def test_diagnose_endpoint_needs_the_token(self):
        req = urllib.request.Request(self.url + "/api/diagnose", json.dumps({"tool": "ping"}).encode(),
                                     {"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        e.exception.close()


class OfflineNodeTests(unittest.TestCase):
    """A node that does not answer cannot be rebooted over the network, so say so."""

    class Stub:
        def __init__(self, reachable):
            snap = make_snapshot(Config(primary="192.168.1.1"), "healthy", now=1_800_000_000)
            [n for n in snap["nodes"] if n["name"] == "Hall"][0]["reachable"] = reachable
            self.state = {"snapshot": snap, "now": 1_800_000_000, "findings": [], "health": "ok"}
            self.posted = []

        def get(self, path, timeout=None):
            return self.state

        def post(self, path, body, timeout=None):
            self.posted.append(body)
            return {"preview": {"title": "Reboot Hall", "effects": [], "risk": "medium", "noop": False}}

    def test_offline_node_is_not_offered_a_reboot(self):
        stub = self.Stub(reachable=False)
        r = call(build_server(stub), "reboot_node", {"node": "Hall", "confirm": True})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("Unplug its power", r["content"][0]["text"])
        self.assertEqual(stub.posted, [], "nothing may be sent to the dashboard for an unreachable node")

    def test_reachable_node_goes_through_to_the_preview(self):
        stub = self.Stub(reachable=True)
        r = call(build_server(stub), "reboot_node", {"node": "Hall"})["result"]
        self.assertFalse(r["isError"])
        self.assertEqual(len(stub.posted), 1)


class FormattingTests(unittest.TestCase):
    def test_status_text_for_a_dashboard_that_is_not_ready(self):
        for state, fragment in (({"setup_required": True}, "not connected"), ({"snapshot": None}, "first poll")):
            with self.subTest(fragment=fragment), self.assertRaisesRegex(ToolError, fragment):
                status_text(state)

    def test_ago_wording(self):
        self.assertEqual([ago(x) for x in (5, 89, 120, 7200, 400000)], ["5 s", "89 s", "2 min", "2 h", "5 d"])


if __name__ == "__main__":
    unittest.main()
