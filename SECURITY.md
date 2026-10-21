# Security

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use GitHub's private reporting:
the repository's **Security** tab, then **Report a vulnerability**. Include what you found, how to
reproduce it, and what it lets an attacker do. You will get an answer as soon as a maintainer can
look at it.

## What is in scope

velop-doctor can read your network and, when asked, change a setting or reboot a node, so these
matter most:

- anything that lets a web page, another user, or another machine drive the dashboard or the MCP
  server without being allowed to (token, `Origin` and `Host` checks, `--no-auth` guards);
- anything that sends the Velop admin password somewhere other than your own Velop, or stores or
  logs it in plain text;
- argument handling in the diagnostics and MCP tools (anything that could reach a command line).

## Design, in short

- The admin password is stored in the macOS Keychain, or a mode-600 file elsewhere. It is never in
  the config file, the database or logs.
- The dashboard listens on `127.0.0.1`; write actions need a per-run token, and requests with a foreign
  `Host` or `Origin` are refused.
- MCP over stdio exposes nothing on the network. MCP over HTTP needs a bearer token unless started
  with `--no-auth`, which is refused on any non-loopback address.
- Write actions (`set_client_steering`, `reboot_node`) preview first and only apply with an explicit
  confirmation.

See [docs/mcp.md](docs/mcp.md) and [monitor/README.md](monitor/README.md) for the details.

## Known limits

- Velop nodes only offer plain HTTP on the local network, so traffic between the monitor and a node is
  not encrypted. Run the monitor on a network you trust.
- MCP over HTTP has no TLS. Use it on localhost, or over a VPN.
- `--no-auth` means any program on the same computer can use the tools.
