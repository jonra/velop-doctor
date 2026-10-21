# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0]

First public release.

### monitor
- Dashboard (standard library only, Python 3.9+): problems with what to try, a topology diagram with every
  device drawn on its node, node and device tables, latency charts, event timeline, GitHub Primer light theme.
- Checks for nodes offline or out of sync, "ghost" nodes, nodes that keep dropping out, wireless or slow
  backhaul, idle nodes, weak and slow devices, crowded 2.4 GHz, double NAT, DHCP pool, and internet, DNS,
  TCP and MTU problems, including a router-side ping that separates a bad line from bad Wi-Fi.
- Password entry in the browser; password stored in the macOS Keychain.
- Actions with preview and confirm: client steering, node reboot (experimental).
- MCP server for Claude with 12 tools, over stdio and over Streamable HTTP (bearer token, or `--no-auth`
  on localhost).

### probe
- Raspberry Pi Zero cable tester ("ethtester"), imported with its history. Reads the dongle's maximum speed
  so a 10/100 adapter is not reported as a cable fault.
