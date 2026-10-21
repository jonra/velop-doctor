# Ethernet Tester MCP Server — Design

Date: 2026-10-21

## Goal

Let a Claude Code session on the user's own machines read the tester's status and run extra network probes through the tester's Ethernet dongle, from anywhere on the internet. Claude can then drive a diagnosis ("ping the AP, renew DHCP, force 100M and see if the link holds") while the user moves the tester between plugs.

Builds on the existing `ethtester` service (see `2026-10-21-ethernet-tester-design.md`).

## Decisions

- **Clients:** Claude Code only, on the user's Macs or other machines they own. claude.ai connectors are out of scope.
- **Transport:** MCP over streamable HTTP. No OAuth.
- **Reachability:** Tailscale. The server listens only on the Pi's Tailscale IPv4 address. Nothing is exposed on the LAN or the public internet, and no router ports are opened.
- **Auth:** a static bearer token, checked on every request.
- **Process model:** a separate systemd service, `ethtester-mcp`. The display loop is untouched, so a slow probe can never freeze the screen.

## Architecture

```
Claude Code ──HTTP over Tailscale──► ethtester-mcp (port 8765, tailscale IP only)
                                                 │  bearer-token middleware
                                                 │  tools ──► probes (bound to eth0) ──► network
                                                 │       └──► reads /run/ethtester/state.json
                                                 │       └──► reads /var/log/ethtester/ethtester.log
ethtester (existing) ──writes each tick──► /run/ethtester/state.json
```

Tailscale already encrypts traffic between devices, so plain HTTP inside the tailnet is acceptable.

## Changes to the existing service

`main.run()` writes the latest snapshot and verdict to `/run/ethtester/state.json` after every tick: JSON of `{"ts", "verdict", "text", "snapshot"}`, the same shape as a state-log line. The write is atomic (write a temp file, then `os.replace`). A failed write is logged and ignored, the same way state-log failures are handled.

## Components (new package `ethtester/mcp/`)

| Module | Responsibility |
|---|---|
| `server.py` | Builds the FastMCP app, registers the tools, wraps the app in token middleware, runs uvicorn on `<bind>:8765` |
| `auth.py` | ASGI middleware. Rejects requests without `Authorization: Bearer <token>`, using a constant-time compare, with 401 |
| `validate.py` | Input validation: hostname/IP, URL, record type, numeric bounds. Raises `ValueError` with a clear message |
| `probes.py` | One function per probe. Each builds an argv list (never a shell), runs it through `run_command` with a timeout, and parses the output into a dict |
| `dnsq.py` | Extends the existing raw DNS query to any record type in A, AAAA, CNAME, MX, NS, TXT, and parses the answers, including name compression. Always bound to the interface with `SO_BINDTODEVICE` |
| `linkctl.py` | Forced-speed and renegotiate logic with automatic revert, a single global lock for disruptive actions, and an "ensure autoneg on" call at server start |
| `probelog.py` | Appends one JSON line per tool call to `/var/log/ethtester/probes.log` (1 MB × 5 rotation) with: time, tool, arguments, duration, ok/error |

The parsers are pure functions, so they can be tested with captured output on the Mac.

## Tools

The probe tools always use the configured interface (`eth0`). Limits are enforced by validation, and results are JSON objects.

| Tool | Arguments (limits) | Implementation | Returns |
|---|---|---|---|
| `get_status` | none | read `state.json` | verdict, rows, snapshot, `age_s` (stale if > 30 s) |
| `get_history` | `limit` (1–200, default 50), `since` (ISO time, optional) | read the state log | list of state changes |
| `get_link_details` | none | `ethtool eth0`, `ethtool -S eth0`, sysfs | speed, duplex, autoneg, advertised and link-partner modes, error counters |
| `ping` | `host`, `count` (1–20, default 5), `size` (16–1472, default 56) | `ping -I eth0 -n -c -s -W 2` | sent, received, loss %, min/avg/max RTT |
| `traceroute` | `host`, `max_hops` (1–30, default 20) | `traceroute -i eth0 -n -q 1 -w 2 -m` | list of hops (number, IP or `*`, RTT) |
| `dns_lookup` | `name`, `type` (A/AAAA/CNAME/MX/NS/TXT, default A), `server` (IP, default: the DHCP DNS server) | `dnsq` | rcode, answers, server used, time |
| `http_check` | `url` (http/https only) | `curl --interface eth0 -sS -o /dev/null -m 15 -w <timings> -v` | status code, DNS/connect/TLS/first-byte/total times, TLS certificate expiry (parsed from `-v`) |
| `dhcp_renew` | none | lock; `nmcli --wait 30 device connect eth0`; poll `nmcli` until an IP appears | seconds to IP, IP/prefix, gateway, DHCP server, DNS servers |
| `link_control` | `action` (`renegotiate` / `force_10` / `force_100`), `duration_s` (10–300, default 60) for force actions | lock; `ethtool -r eth0`, or `ethtool -s eth0 speed N duplex full autoneg off`, then a timer runs `ethtool -s eth0 autoneg on` | the link state shortly after the action, and when the revert happens |
| `neighbour_scan` | none | lock; `arp-scan --interface eth0 --localnet --plain` (only when the prefix is /22 or smaller) | list of IP, MAC, vendor |
| `speed_test` | `server` (host), `seconds` (3–30, default 10), `reverse` (bool) | `iperf3 -c --bind-dev eth0 -t -J [-R]` | Mbit/s, retransmits |

Rules:
- **Disruptive actions:** `dhcp_renew`, `link_control` and `neighbour_scan` share one lock. A second call while one is running gets a clear "busy" error. `link_control` holds the lock until it has reverted.
- **Revert safety:** at server start, and when the service stops, the server runs `ethtool -s eth0 autoneg on`. A crash during a forced speed is therefore fixed by the next start (`Restart=always`).
- **Interface down:** if `eth0` is missing or has no carrier, the probe tools return an error result saying so, rather than running and timing out.
- **Errors:** validation errors, timeouts and tool failures come back as MCP tool errors with a short message. The server never crashes on a tool error.

## Configuration

New keys in `/boot/firmware/ethtester.toml`, with defaults:
```toml
mcp_port = 8765
mcp_bind = "tailscale"   # "tailscale" = use `tailscale ip -4`; or an explicit IP
```
The token lives in `/etc/ethtester/mcp-token` (mode 0600, root). `install.sh` creates it from 32 random bytes in hex if it doesn't exist. To rotate it, delete the file and re-run the installer.

If `mcp_bind = "tailscale"` and Tailscale has no IPv4 address yet, the service logs this and exits non-zero. systemd retries it (`Restart=always`, `RestartSec=10`). The server never falls back to listening on 0.0.0.0.

## Deployment

`install.sh` additions:
- **Packages:** `traceroute ethtool iperf3 arp-scan python3-venv`.
- **Tailscale:** installed from Tailscale's official Debian repository (pkgs.tailscale.com, trixie). The installer adds the signing key and the source list, then runs `apt-get install tailscale`.
- **Virtualenv:** `/opt/ethtester/venv` (`python3 -m venv --system-site-packages`) with `pip install "mcp>=1.10,<2" "uvicorn>=0.30"`. The existing service keeps using system Python.
- **Token:** created as described above.
- **systemd unit:** `ethtester-mcp.service`:
  - `ExecStart=/opt/ethtester/venv/bin/python -m ethtester.mcp`
  - `After=tailscaled.service`, `Wants=tailscaled.service`
  - `Restart=always`, `RestartSec=10`
  - runs as root, like the tester, because it binds to the device and changes link settings
- **Last step:** the installer prints `tailscale up` instructions if the Pi isn't logged in to Tailscale yet. It also prints the exact `claude mcp add` command with the hostname and token filled in.

User setup, once:
1. On the Pi, run `sudo tailscale up` and open the sign-in link it prints.
2. On each Mac, install Tailscale and sign in to the same account.
3. On each Mac, run:
   `claude mcp add --transport http ethtester http://<pi-tailscale-name>:8765/mcp --header "Authorization: Bearer <token>"`

## Testing

- **Unit tests on the Mac** (pytest):
  - validators, including rejected inputs: shell metacharacters, out-of-range numbers, non-http URLs;
  - every parser against captured output from ping, traceroute, `ethtool`, `ethtool -S`, curl `-w`/`-v`, arp-scan, `iperf3 -J`, nmcli;
  - `dnsq` encoding and decoding for every supported type, including name compression;
  - the link-control lock, timer and revert, with fake commands and clock;
  - the auth middleware (missing, wrong and right token) through an ASGI test client;
  - `state.json` atomic writing.
- **Integration test on the Mac:** start the MCP app in-process with fake probe runners, list the tools, and call `get_status` over HTTP with the token.
- **On the device:** add the server to Claude Code over Tailscale and call each tool once. Specifically check that:
  - `force_10` reverts on its own;
  - a request without the token gets 401;
  - the port isn't reachable on the LAN address.

## Out of scope

OAuth and claude.ai connectors, public exposure, arbitrary shell commands, packet capture, PoE measurement, and multiple testers.
