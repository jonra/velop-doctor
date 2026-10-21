# Ethernet Tester — Design

Date: 2026-10-21

## Goal

A pocket tester for diagnosing why access points don't connect properly. Plug it into a wall socket or switch port and the e-ink screen shows, layer by layer, how far the connection gets. The first failing layer narrows the fault to **plug/cable**, **switch port**, or **router/DHCP/uplink**.

## Hardware

- Raspberry Pi Zero 2 W (the code also works on other Zero models). Wi-Fi is used only for SSH access.
- USB Ethernet dongle on the micro-USB OTG port (expected interface name `eth0`)
- Waveshare 2.13" e-Paper HAT V4, 250×122, black/white, partial refresh. V3 is also supported through config.

Known limitations:
- A 10/100 dongle cannot detect the "gigabit dropped to 100M" cable fault. A gigabit dongle can detect it (throughput is still capped at ~300 Mbit by USB 2.0, which doesn't matter for link negotiation).
- PoE power cannot be measured. Use a separate inline PoE tester.

## Architecture

A single Python 3 service (`ethtester`), started at boot by systemd. Every `interval` seconds (default 5) it runs the check pipeline against `eth0`, computes a verdict, logs any changes, and updates the display when the rendered content changes.

All network checks are bound to `eth0` (by interface or source address). Wi-Fi, if present, is used only for SSH access and must never satisfy a check.

```
main loop ─► checks (link → lldp → dhcp → gateway → dns → internet)
          ─► verdict(results)
          ─► logger (on change)
          ─► display.render(state)  (only if changed)
```

Checks are ordered. When a check fails, later checks are skipped and shown as `–`.

## Components

Each module exposes one function that returns a small dataclass result. Command output is parsed by separate pure functions so it can be tested with saved sample output.

| Module | Result fields | Source |
|---|---|---|
| `checks/link.py` | `up`, `speed_mbps`, `duplex`, `flaps_last_minute` | `/sys/class/net/eth0/{carrier,speed,duplex,carrier_changes}` |
| `checks/lldp.py` | `switch_name`, `port` (or None) | `lldpcli -f json show neighbors` (`lldpd` package) |
| `checks/dhcp.py` | `ip`, `prefix`, `gateway`, `dhcp_server`, `seconds_to_ip` | `nmcli -t -f IP4,DHCP4 dev show eth0`; time measured from link-up to first IPv4 address |
| `checks/gateway.py` | `reachable`, `rtt_ms`, `loss_pct` | `ping -I eth0 -c 3 -W 1 <gateway>` |
| `checks/dns.py` | `ok`, `server` | Lookup of `dns_test_name` against the DHCP-provided DNS server, sent from the `eth0` source address |
| `checks/internet.py` | `ok`, `public_ip` | `curl --interface eth0 -s https://1.1.1.1/cdn-cgi/trace` (parse `ip=`) |
| `verdict.py` | `code`, `text` | Pure function of all check results |
| `display.py` | — | Pillow renders a 250×122 1-bit image. Backends: `waveshare` (`epd2in13_V3` / `epd2in13_V4`) or `png` (writes a file, for development) |
| `main.py` | — | Loop, change detection, plug-swap handling, logging |

## Verdict rules

Evaluated in order; the first match wins.

| # | Condition | Verdict text |
|---|---|---|
| 1 | Link down | `NO LINK: cable/plug/port` |
| 2 | `speed_mbps < expected_speed` | `CABLE? (<speed>M)` |
| 3 | `flaps_last_minute > 2` | `FLAPPING: loose plug?` |
| 4 | No IPv4 address within `dhcp_timeout` (20 s) of link-up | `NO DHCP: router/VLAN` |
| 5 | Gateway unreachable or loss > 20% | `GATEWAY: router/VLAN` |
| 6 | DNS lookup fails | `DNS` |
| 7 | Internet check fails | `NO INTERNET: uplink` |
| 8 | Everything passes | `OK` |

Between link-up and `dhcp_timeout`, if no IP has been assigned yet, the verdict is `WAITING FOR DHCP…` rather than a failure.

## Display layout

```
LINK  ✓ 1000M full     flaps:0
PORT  SW-Office / gi0/14
IP    ✓ 192.168.1.57/24  2.1s
GW    ✓ 192.168.1.1  3ms 0%
DNS   ✓
NET   ✓ 84.x.x.x
────────────────────────────
OK                    10:42
```

- Each row starts with `✓`, `✗` or `–` (skipped).
- The verdict appears in a larger bold font on the bottom row, with the time of the last update.
- Refresh policy: use partial refresh for small changes. Use a full refresh when the verdict code changes, after every 20 partial refreshes (to clear ghosting), and at startup.
- Startup screen: `ethtester – waiting for link` plus the software version.

## Plug-swap behaviour

- Link goes down: clear the per-plug state (DHCP timer, LLDP) and show the `NO LINK` screen immediately. Flap history is kept, because it is a 60 s sliding window and clearing it would hide flapping.
- Link comes up: start the DHCP timer and run the full pipeline on every tick until the result is stable.
- LLDP data can take up to ~30 s to arrive. Show `PORT  …` until it does, or until 60 s have passed, then show `–`.

## Configuration

File: `/boot/firmware/ethtester.toml` (editable from a Mac by mounting the SD card). Defaults are used for any missing key.

```toml
interface      = "eth0"
display        = "V4"         # "V3", "V4" or "png"
expected_speed = 1000         # Mbps; set 100 for a 10/100 dongle
interval       = 5            # seconds
dhcp_timeout   = 20           # seconds
ping_count     = 3
dns_test_name  = "example.com"
internet_url   = "https://1.1.1.1/cdn-cgi/trace"
```

## Logging

- JSON lines written to `/var/log/ethtester/ethtester.log`, one line per state change (verdict change, link up/down, speed change, new IP, new LLDP neighbour). Each line includes a timestamp and the full result set.
- The log file is rotated by size (1 MB × 5).
- Errors in a check (such as a missing tool or a parse failure) mark that check `✗`, log the exception, and never crash the loop. An unexpected exception in the loop is logged and the loop continues on the next tick. systemd restarts the service if the process dies.

## Deployment

- Raspberry Pi OS Lite (Bookworm). Packages: `python3-pil`, `python3-spidev`, `python3-gpiozero`, `lldpd`, `curl`. SPI enabled.
- Waveshare driver files for V3 and V4 are vendored into the project.
- A systemd unit `ethtester.service` (`Restart=always`, after `NetworkManager.service`).
- An `install.sh` script installs the packages, enables SPI and lldpd, copies the code to `/opt/ethtester`, installs the unit, and writes a default config if none exists.

## Testing

- **Unit tests (pytest, run on a Mac):** verdict rules (one test per rule plus ordering); parsers for sysfs values, `lldpcli` JSON, `nmcli` output, `ping` output and the trace response, using sample output embedded in the test files.
- **Render tests:** the `png` backend renders sample states (all OK, no link, cable 100M, no DHCP, long switch names) to `tests/output/`, for visual review.
- **On-device checklist:**
  1. Boot with no cable → `NO LINK`.
  2. Plug in a known-good port → `OK` with the correct IP.
  3. Use a cable with one pair cut, or force 100M on the switch → `CABLE? (100M)`.
  4. Use a port with no DHCP → `NO DHCP` after 20 s.
  5. Unplug and replug quickly several times → `FLAPPING`.
  6. With Wi-Fi connected and Ethernet unplugged, confirm no check passes.

## Out of scope (for now)

Throughput testing (iperf3), VLAN-tag checks, PoE measurement, buttons, and a web UI.
