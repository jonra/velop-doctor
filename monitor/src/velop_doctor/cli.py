"""Command line entry point: `velop-doctor <command>`."""
from __future__ import annotations

import argparse
import getpass
import re
import subprocess
import sys
import time

from . import __version__, analyzer, config, service
from .backend import DemoBackend, LiveBackend
from .collector import collect
from .demo import make_snapshot
from .jnap import AuthError, JnapClient, JnapError
from .poller import Poller
from .server import App, serve
from .store import Store


def detect_gateway():
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True).stdout
            m = re.search(r"gateway:\s*(\S+)", out)
        else:
            out = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout
            m = re.search(r"via\s+(\S+)", out)
        return m.group(1) if m else None
    except OSError:
        return None


def cmd_setup(args):
    cfg = config.load()
    host = args.primary or detect_gateway() or input("IP address of your main Velop node: ").strip()
    print(f"Looking for a Velop at {host} …")
    try:
        info = JnapClient(host).call("core/GetDeviceInfo", auth=False)
    except JnapError:
        print(f"Nothing that looks like a Linksys node answered at {host}.\n"
              "Pass the main node's address: velop-doctor setup --primary 192.168.1.1")
        return 1
    print(f"Found {info.get('modelNumber')} (serial …{str(info.get('serialNumber'))[-5:]}), "
          f"firmware {info.get('firmwareVersion')}.")
    cfg.primary = host
    cfg.username = args.username or cfg.username
    password = getpass.getpass(f"Admin password for {cfg.username} (not echoed): ")
    try:
        JnapClient(host, cfg.username, password).call("devicelist/GetDevices3")
    except AuthError:
        print("The node rejected that password. Nothing was saved.")
        return 1
    except JnapError as e:
        print(f"Login test failed: {e.result}")
        return 1
    where = config.set_password(cfg, password)
    config.save(cfg)
    print(f"Saved. Password stored in {where}; settings in {config.config_path()}.\n"
          "Next: velop-doctor serve")
    return 0


def _seed_demo(store, cfg, scenario):
    now = int(time.time())
    for t in range(now - 3600, now, 60):
        store.add_snapshot(make_snapshot(cfg, scenario, now=t))
    # Backdate the conditions so the demo shows problems that have already persisted.
    findings = analyzer.evaluate(make_snapshot(cfg, scenario, now=now - 1000), cfg)
    store.update_conditions(now - 1000, findings)
    store.update_conditions(now - 10, findings, gap_seconds=10_000)
    store.add_event(now - 3000, "info", "roam", "iPhone moved from Hall to Living room (main)")
    store.add_event(now - 1800, "warning", "backhaul_type", "Bedroom backhaul changed from Wired to Wireless")
    store.add_event(now - 1500, "info", "backhaul_type", "Bedroom backhaul changed from Wireless to Wired")


def cmd_serve(args):
    cfg = config.load()
    cfg.port = args.port or cfg.port
    if args.demo:
        backend, store = DemoBackend(cfg), Store(":memory:")
        _seed_demo(store, cfg, "problems")
    else:
        if not cfg.primary:
            print("Not set up yet. Run: velop-doctor setup   (or try: velop-doctor serve --demo)")
            return 1
        password = config.get_password(cfg)
        if not password:
            print("No stored password found. Run: velop-doctor setup")
            return 1
        backend, store = LiveBackend(cfg, password), Store(cfg.db_path)
    poller = Poller(backend, store, cfg)
    app = App(cfg, backend, store, poller, host=args.host)
    httpd = serve(app, args.host, cfg.port)
    poller.start()
    print(f"velop-doctor {__version__}{' (demo data)' if args.demo else ''}: "
          f"http://{args.host}:{cfg.port}   (Ctrl-C to stop)")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: listening beyond this computer. The dashboard has no login, and "
              "anyone who can reach it can use its actions (e.g. reboot a node).")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
        httpd.server_close()
    return 0


def cmd_check(args):
    cfg = config.load()
    password = config.get_password(cfg)
    if not cfg.primary or not password:
        print("Not set up yet. Run: velop-doctor setup")
        return 1
    snap = collect(cfg, password)
    findings = analyzer.evaluate(snap, cfg)
    nodes = [n for n in snap["nodes"]]
    print(f"{len(nodes)} node(s) listed, {len(snap['clients'])} device(s) online\n")
    for n in nodes:
        bh = n.get("backhaul")
        link = f"{bh['type']} {bh['mbps']} Mbps" if bh else "main"
        state = "offline" if n["reachable"] is False else "not in mesh" if not n["in_mesh"] else "ok"
        print(f"  {n['name']:<28} {n['ip'] or '?':<15} {link:<18} {n['clients']:>3} Wi-Fi  {state}")
    print()
    if not findings:
        print("No problems detected.")
        return 0
    for f in sorted(findings, key=lambda f: ("critical", "warning", "info").index(f["severity"])):
        print(f"[{f['severity'].upper():<8}] {f['title']}\n           {f['detail']}")
    print("\n(Duration filters are not applied in a one-off check; the dashboard waits for "
          "conditions to persist before alerting.)")
    return 2 if any(f["severity"] == "critical" for f in findings) else \
        1 if any(f["severity"] == "warning" for f in findings) else 0


def cmd_steering(args):
    from . import actions
    cfg = config.load()
    password = config.get_password(cfg)
    if not cfg.primary or not password:
        print("Not set up yet. Run: velop-doctor setup")
        return 1
    snap = collect(cfg, password)
    action = "steering_on" if args.state == "on" else "steering_off"
    pv = actions.preview(action, {}, snap)
    print(pv["title"])
    for e in pv["effects"]:
        print(f"  - {e}")
    if pv["noop"]:
        return 0
    if not args.yes and input("Apply? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 1
    result = actions.execute(action, {}, snap, lambda h: JnapClient(h, cfg.username, password), cfg)
    print(result["message"])
    return 0 if result["ok"] else 1


def cmd_install(args):
    return service.install(args.port)


def cmd_uninstall(args):
    return service.uninstall()


def main(argv=None):
    p = argparse.ArgumentParser(prog="velop-doctor", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="find the main node and store the admin password")
    s.add_argument("--primary", help="IP of the main node (default: your default gateway)")
    s.add_argument("--username", help="admin username (default: admin)")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("serve", help="run the monitor and the web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int)
    s.add_argument("--demo", action="store_true", help="use made-up data instead of a real Velop")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("check", help="one-off health check printed to the terminal")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("steering", help="turn client steering on or off")
    s.add_argument("state", choices=["on", "off"])
    s.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    s.set_defaults(func=cmd_steering)

    s = sub.add_parser("install-service", help="start the dashboard automatically at login")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("uninstall-service", help="remove the login service")
    s.set_defaults(func=cmd_uninstall)

    args = p.parse_args(argv)
    return args.func(args)
