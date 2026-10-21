# ethtester

A Pi Zero with a USB Ethernet dongle and a Waveshare 2.13" e-ink HAT. Plug it into a wall socket or switch port, and the screen shows how far the connection gets and what is most likely at fault.

| Screen says | Likely cause |
|---|---|
| `NO LINK` | Cable, wall plug, or dead switch port |
| `CABLE? (100M)` | Damaged cable/termination (gigabit fell back to 100) |
| `FLAPPING` | Loose plug or worn connector |
| `NO DHCP` | Router/DHCP, or port on the wrong VLAN |
| `GATEWAY` | Router or VLAN configuration |
| `DNS` | DNS server |
| `NO INTERNET` | Router's uplink |

## Setup

1. Flash **Raspberry Pi OS Lite (64-bit, Bookworm or Trixie)** with Raspberry Pi Imager (use 32-bit on an original Pi Zero / Zero W). Enable SSH, set a user and, on Wi-Fi models, add your Wi-Fi so you can SSH in while the Ethernet port is under test.
2. Boot the Pi and copy this folder over:
   `scp -r ethernet-tester <user>@<pi-ip>:`
3. On the Pi, run `sudo ethernet-tester/deploy/install.sh`
4. In `/boot/firmware/ethtester.toml`, check that `display` matches the sticker on the back of the HAT (default `"V4"`). If your dongle is a 10/100 model, also set `expected_speed = 100`.
5. Reboot.

Logs: `journalctl -u ethtester` for errors, and `/var/log/ethtester/ethtester.log` for the state history.

Limitations: it can't measure PoE (use an inline PoE tester). LLDP needs a managed switch.

## Development (Mac)

```
python3 -m venv .venv && .venv/bin/pip install pillow pytest
.venv/bin/pytest
```

Sample screens are written to `tests/output/`.
