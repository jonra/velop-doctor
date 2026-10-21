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
        store = Store(":memory:")
        _seed_demo(store, cfg, "problems")
    else:
        store = Store(cfg.db_path)
    app = App(cfg, None, store, None, host=args.host, guess=detect_gateway())
    if args.demo:
        app.attach(DemoBackend(cfg))
    else:
        password = config.get_password(cfg) if cfg.primary else None
        if password:
            app.attach(LiveBackend(cfg, password))
    httpd = serve(app, args.host, cfg.port)
    print(f"velop-doctor {__version__}{' (demo data)' if args.demo else ''}: "
          f"http://{args.host}:{cfg.port}   (Ctrl-C to stop)")
    if not app.configured:
        print("Not connected to a Velop yet: open the address above to finish setup in the browser.")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: listening beyond this computer. The dashboard has no login, and "
              "anyone who can reach it can use its actions (e.g. reboot a node).")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.poller:
            app.poller.stop()
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


def cmd_mcp(args):
    """Serve MCP over stdio for Claude. Talks to the running dashboard; prints nothing else to stdout."""
    import os
    from .mcp_tools import Dashboard, build_server
    cfg = config.load()
    url = args.url or os.environ.get("VELOP_DOCTOR_URL") or f"http://127.0.0.1:{args.port or cfg.port}"
    read_only = args.read_only or os.environ.get("VELOP_DOCTOR_MCP_READONLY") == "1"
    server = build_server(Dashboard(url), read_only=read_only)
    if not args.http:
        server.serve_stdio()
        return 0
    from .mcp_http import serve_http
    if args.no_auth and args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"Refusing to run without a token while listening on {args.host}: that would let anyone on the "
              "network reboot your nodes. Drop --no-auth (a token is created for you), or keep the default "
              "--host 127.0.0.1.", file=sys.stderr)
        return 2
    token = None if args.no_auth else config.get_mcp_token()
    app, httpd = serve_http(server, token, args.host, args.listen_port,
                            allow_origins=args.allow_origin or (), require_auth=not args.no_auth)
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"velop-doctor MCP over HTTP: http://{shown}:{args.listen_port}/mcp"
          f"   (reads the dashboard at {url}; {'read-only; ' if read_only else ''}Ctrl-C to stop)")
    url_ = f"http://{shown}:{args.listen_port}/mcp"
    if args.no_auth:
        print("\nNO AUTHENTICATION (--no-auth). Any program on this computer can call these tools"
              + ("" if read_only else ", including reboot_node") + ". Web pages are still blocked "
              "(Origin and Host are checked). Add --read-only to remove the tools that change anything.")
        print(f"Claude Code:  claude mcp add --transport http velop-doctor {url_}\n")
    else:
        print(f"\nBearer token (every request needs it): {token}")
        print(f'Claude Code:  claude mcp add --transport http velop-doctor {url_} '
              f'--header "Authorization: Bearer {token}"\n')
    if not app.loopback:
        print("WARNING: listening beyond this computer. The token is the only protection and the connection "
              "is plain HTTP: use it only on a network you trust (a VPN such as Tailscale is ideal).")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def cmd_mcp_token(args):
    token = config.get_mcp_token(rotate=args.rotate)
    cfg = config.load()
    url = f"http://localhost:{args.listen_port}/mcp"
    print(f"{'New token' if args.rotate else 'Token'} (stored in {config.mcp_token_path()}, mode 600):\n  {token}\n")
    print("Claude Code:")
    print(f'  claude mcp add --transport http velop-doctor {url} --header "Authorization: Bearer {token}"')
    print("\nAnything else that speaks MCP over HTTP: URL above, header 'Authorization: Bearer <token>'.")
    print("Anyone with this token can use the tools, including rebooting nodes. Rotate it with --rotate.")
    return 0


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

    s = sub.add_parser("mcp", help="MCP server for Claude (stdio); needs the dashboard to be running")
    s.add_argument("--port", type=int, help="port of the running dashboard (default: from config, 8765)")
    s.add_argument("--url", help="full dashboard URL, overrides --port")
    s.add_argument("--read-only", action="store_true", help="do not offer the steering and reboot tools")
    s.add_argument("--http", action="store_true", help="serve MCP over HTTP (bearer token required) instead of stdio")
    s.add_argument("--host", default="127.0.0.1", help="HTTP listen address (default 127.0.0.1; use 0.0.0.0 or a VPN address to reach it from elsewhere)")
    s.add_argument("--listen-port", type=int, default=8780, help="HTTP listen port (default 8780)")
    s.add_argument("--allow-origin", action="append", help="extra browser Origin to accept (repeatable)")
    s.add_argument("--no-auth", action="store_true",
                   help="HTTP without a bearer token. Only on localhost: any local program can then use the tools")
    s.set_defaults(func=cmd_mcp)

    s = sub.add_parser("mcp-token", help="show (or --rotate) the bearer token for MCP over HTTP")
    s.add_argument("--rotate", action="store_true", help="invalidate the old token and make a new one")
    s.add_argument("--listen-port", type=int, default=8780)
    s.set_defaults(func=cmd_mcp_token)

    s = sub.add_parser("install-service", help="start the dashboard automatically at login")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("uninstall-service", help="remove the login service")
    s.set_defaults(func=cmd_uninstall)

    args = p.parse_args(argv)
    return args.func(args)
