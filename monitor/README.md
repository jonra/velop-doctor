# velop-doctor monitor

A local web dashboard and command-line checker for Linksys Velop mesh networks.
Standard library only: nothing to install except Python 3.9+.

## What you get

- **Problems** panel: plain-language findings, each saying what is wrong and what to try.
- **Network map**: a topology diagram from the internet through your upstream router to the main node and satellites, with wired or wireless backhaul and link speed on each link. Every device is drawn as a dot on the node it uses, grouped by band (wired ones as squares) and coloured by last reported signal, so crowding and weak spots are visible at a glance. A node with nothing connected shows an empty tray, an offline node says so. Hover a dot for details; click it to find it in the table.
- **Nodes** table: reachable? answering its API? **in sync with the mesh?** firmware, ping, Wi-Fi device count.
- **Devices** table: every online device, which node it is on, band, signal, link rate.
- **Connection tests**: DNS timing through the Velop, your ISP and a public resolver; TCP reachability; path MTU; the internet seen from the router *and* from this computer (so you can tell a bad line from bad Wi-Fi); this computer's own Wi-Fi quality; DHCP pool usage.
- **Latency** charts for every node, your upstream router and the internet, plus 24-hour uptime per node.
- **Timeline** of changes: nodes dropping out, backhaul switching to wireless, devices roaming, WAN address changes, gaps in monitoring.
- **Actions** (always with a preview and a confirm step): turn client steering on or off, reboot a node.
- **Claude can use it**: `velop-doctor mcp` is an MCP server with 12 tools (status, problems, nodes, devices, history, re-check now, ping from the computer and the router, DNS lookups, plus steering and reboot with preview-then-confirm). See [`../docs/mcp.md`](../docs/mcp.md).

## Install and run

```bash
python3 -m pip install -e .

velop-doctor serve --demo   # made-up data, to look around
velop-doctor serve          # dashboard on http://127.0.0.1:8765; first run asks for the password in the page
```

On first run the page asks for your main node's address and the admin password, checks them
against your Velop, and stores the password in your keychain. Prefer the terminal?
`velop-doctor setup` does the same thing.

Other commands:

```bash
velop-doctor check                   # one-off report in the terminal; exit code 0 / 1 / 2 = ok / warnings / critical
velop-doctor steering on             # change client steering from the terminal
velop-doctor mcp --port 8765          # MCP server for Claude (stdio); needs the dashboard running
velop-doctor install-service         # start the dashboard at login (launchd / systemd --user)
velop-doctor uninstall-service
```

History is kept for 7 days in `~/.velop-doctor/velop.db`. Set `VELOP_DOCTOR_HOME` to move it.

## Where the password goes

Your Velop **admin** password (the router password, not a Linksys cloud account) is
needed for most of the data. It is stored in:

1. the `VELOP_PASSWORD` environment variable, if set;
2. the **macOS Keychain**, on macOS;
3. a file with mode `600` in `~/.velop-doctor/`, elsewhere.

It is never written to the config file, the database, or logs. The only machine it
is sent to is your own Velop, over your LAN, which is how the Linksys app itself talks
to it (plain HTTP: the Velop offers nothing better).

## Security model

The dashboard listens on `127.0.0.1` only. It has no login, because it is meant
for the machine it runs on. Write actions need a per-run token that only the page
itself carries, requests with a foreign `Host` or `Origin` are rejected (so a web
page you visit cannot drive it), and targets are always taken from the latest
snapshot, never from the request.

If you pass `--host 0.0.0.0` to reach it from your phone, **anyone on your network
can use its actions**. Do that only on a network you trust.

## How it works

```
collector ──▶ snapshot (JSON) ──▶ analyzer ──▶ findings ──▶ store (SQLite) ──▶ web UI
   ▲                                  │                           ▲
 Velop (JNAP) + ping                  └─ events (changes) ────────┘
```

- `collector.py` asks the main node for the device list, backhaul, wireless
  connections, WAN and steering settings, then asks **each node directly** whether
  it answers, whether it is at factory defaults, and whether it **accepts the mesh
  password**. A node that answers pings but rejects the password is not following
  the main node's settings. That is how a half-added node gives itself away.
- `analyzer.py` is pure functions over snapshots, so it is easy to test.
- `mcp_server.py` and `mcp_tools.py` are the MCP server: a small standard-library JSON-RPC layer over stdio, and the tools, which call the dashboard's local API.
- `diagnostics.py` validates and runs the on-demand ping and DNS lookups.
- `probes.py` holds the active probes (ping with jitter, DNS, TCP, MTU, router-side ping, DHCP, host Wi-Fi), each of which gives up quickly and never raises.
- `store.py` only reports a finding after it has persisted for its `min_seconds`,
  and restarts that clock after a gap in monitoring (a sleeping laptop is not an hour of outage).
- `actions.py` previews every change, applies it, then reads the setting back to verify.

See [`../docs/checks.md`](../docs/checks.md) for every check.

## Limits you should know about

- Monitoring only happens while the computer running it is awake. Gaps show as gaps.
- Backhaul speed is a **periodic measurement** made by the node, not a live reading;
  the dashboard shows how old it is.
- **Signal values are "last reported", not live.** The Velop records a device's signal when it connects
  or roams. The dashboard shows how old each value is, dims readings over 2 hours old, and the
  weak-signal check ignores readings over 24 hours old. Which node a device is on *is* current.
- **On macOS, reading this computer's own Wi-Fi link makes the radio scan**, which adds ~40 ms to
  pings running at that moment. The monitor reads it at most every 10 minutes and never during a
  measurement. (We found this the hard way: it made the Wi-Fi look terrible in our own numbers.)
- Node reboot uses `core/Reboot` and is **experimental**: not every firmware may support it.
- Tested on WHW03 v2. Other models: please report.

## Tests

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```
