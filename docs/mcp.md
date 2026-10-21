# Using velop-doctor from Claude (MCP)

`velop-doctor mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server. It lets
Claude read your mesh's state, run extra checks, and (with your say-so) change a setting.

It is a thin client of the running dashboard: **the dashboard owns the Velop password and the
history; Claude never sees the password**, and only one thing polls your network.

## Set up

1. Keep the dashboard running (`velop-doctor serve`, or `velop-doctor install-service` to start it at login).
2. Register the MCP server with your client. The `--port` must match the dashboard's (default 8765).

**Claude Code**

```bash
claude mcp add velop-doctor -- velop-doctor mcp --port 8765
```

**Claude Desktop** (`claude_desktop_config.json`; use the full path to `velop-doctor` if it is not on your PATH)

```json
{ "mcpServers": { "velop-doctor": { "command": "velop-doctor", "args": ["mcp", "--port", "8765"] } } }
```

Add `--read-only` to leave out the tools that change anything.

### Over HTTP instead

For clients that connect to a URL rather than starting a command:

```bash
velop-doctor mcp --http --port 8765       # --port is the dashboard's, as above
```

On startup it prints the **bearer token** and a ready-to-paste command:

```bash
claude mcp add --transport http velop-doctor http://localhost:8780/mcp \
  --header "Authorization: Bearer <token>"
```

- URL: `http://localhost:8780/mcp` (`--listen-port` changes it). Every request must carry
  `Authorization: Bearer <token>`; there is no way to turn that off.
- The token lives in `~/.velop-doctor/mcp-token` (mode 600), is created on first use, and
  `velop-doctor mcp-token` shows it again. `velop-doctor mcp-token --rotate` replaces it.
- It listens on `127.0.0.1` only. To reach it from another machine, pass `--host` (a VPN address such
  as Tailscale's, or `0.0.0.0`). That is plain HTTP and the token is the only protection, so only do it
  on a network you trust. There is no TLS.
- Not supported: claude.ai custom connectors, which need a public HTTPS address and OAuth.
- It speaks the MCP Streamable HTTP transport (spec 2025-06-18): one endpoint, JSON replies, sessions
  via `Mcp-Session-Id`, no server-initiated stream. Browsers cannot drive it: `Origin` is checked, and on
  a loopback listener so is `Host`.
- If you run it as a background service, the startup message, token included, goes to that service's log.

## Tools

| Tool | What it does | Changes anything? |
|---|---|---|
| `get_status` | One readable report: health, problems with what to try, each node, internet and DNS summary. **Start here.** | No |
| `list_findings` | Active problems, optionally only warning/critical. | No |
| `list_nodes` | Every node: reachable, in sync, in the mesh, backhaul, device counts, 24 h uptime, radios. | No |
| `list_devices` | Devices with node, band, last reported signal, link rate. Filter by node, band, name, weak signal. Weakest first. | No |
| `get_events` | Timeline of changes (drop-outs, roaming, backhaul changes, gaps). | No |
| `get_latency_history` | Latency and loss statistics per target over up to 24 h. | No |
| `get_connection_tests` | DNS, TCP, MTU, internet from this computer and from the router, this computer's Wi-Fi, DHCP pool. | No |
| `run_diagnostics` | Re-run every check **now** and return the new status (about 15 s). | Sends probe traffic |
| `ping` | Ping a host from this computer, from the router, or both, and compare the two. | Sends pings |
| `dns_lookup` | Resolve a name through the Velop, your ISP, a public server or an address, and time it. | Sends one DNS query |
| `set_client_steering` | Turn client steering on or off. | **Yes**, preview first |
| `reboot_node` | Reboot one node. | **Yes**, preview first |

## Safety

- **Preview, then confirm.** `set_client_steering` and `reboot_node` do nothing unless called with
  `confirm=true`. Without it they return a preview (what happens, the risk) and say so. The tool
  descriptions tell Claude to show that to you and wait for your agreement. Your client's own permission
  prompt is a second gate.
- **Honest about what cannot work.** A node that is not answering cannot be rebooted remotely; the tool
  says to unplug it instead.
- **Everything is validated.** Hosts must be an IPv4 address or a hostname (nothing that could be read
  as a command-line option), counts are bounded, and node names are looked up in the latest snapshot,
  never sent as raw addresses.
- **Nothing is exposed on the network by default.** Over stdio the server talks only to your client. The
  HTTP form listens on `127.0.0.1` and demands a bearer token on every request. The dashboard it talks to
  also listens on `127.0.0.1` only, and its write calls need a per-run token.
- **Network data is data.** Device names, Wi-Fi names and similar text come from your network, which
  anyone nearby can influence. The server's instructions tell Claude to treat them as data, never as
  instructions. Use `--read-only` if you want no write path at all.
- **Signal values are "last reported"**, not live: the Velop records them when a device connects or
  roams. Results say how old each value is.

## Checking it against the official SDK

`monitor/scripts/mcp_sdk_check.py` drives the server with the official MCP Python client and asserts
the basics (handshake, tool listing, a read, a preview, a refused hostile argument). It needs
`pip install mcp`, which the monitor itself never does, so it is not part of the unit tests.

## The probe is separate

The Raspberry Pi probe in `probe/` has its own, separate MCP design (`probe/docs/superpowers/specs/`):
it runs on the Pi and your MCP client connects to it directly, not through the monitor.
