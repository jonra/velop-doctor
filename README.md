# velop-doctor

Debugging tools for **Linksys Velop** mesh networks, for when the official app
tells you everything is fine and the Wi-Fi says otherwise.

The Linksys app shows a pretty diagram. It does not show link speeds, history,
which devices sit on which node, whether a node is really in sync with the mesh,
or what happened while you were not looking. This repo does.

| Part | What it is |
|---|---|
| [`monitor/`](monitor) | A Python dashboard (no dependencies) that polls your Velop every minute, keeps history, and runs checks: nodes offline or out of sync, stale "ghost" nodes, wireless backhaul, slow wired links, idle nodes, weak devices, double NAT, internet drops. |
| [`probe/`](probe) (**ethtester**) | A Raspberry Pi Zero with a USB Ethernet dongle and an e-ink screen. Plug it into any wall socket or switch port and it shows how far the connection gets (link speed and flaps, which switch port you reached, DHCP, gateway, DNS, internet) and what is most likely at fault. Own README, installer and systemd service. |
| [`docs/`](docs) | What each check means, how to use the monitor from Claude over MCP, and notes on the (unofficial) JNAP API the Velop speaks. |

The two parts are independent. Use either, or both: the monitor tells you what the mesh
thinks, the probe tells you what the cable actually does.

A note that saves an afternoon: a link negotiated at **100 Mbps** is not proof of a bad cable.
A USB Ethernet dongle that only does 10/100 produces exactly the same reading. Check the
adapter before blaming the wall.

## Quick start (monitor)

```bash
cd monitor
python3 -m pip install -e .
velop-doctor serve --demo      # try the dashboard with made-up data first
velop-doctor serve             # http://127.0.0.1:8765, the first run asks for your password in the page
```

Needs Python 3.9 or newer. Works on macOS and Linux. See [`monitor/README.md`](monitor/README.md).

## Status

Early. Built and tested against a three-node WHW03 v2 (firmware 2.1.20) mesh.
Other Velop models and firmware will probably work, since they share the same
local API, but that is untested. Reports and fixes welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Third-party code

`probe/ethtester/vendor/waveshare_epd/` is the Waveshare e-Paper driver, vendored under its
MIT licence (headers retained in the files), with one documented change. See
[THIRD_PARTY.md](THIRD_PARTY.md).

## Not affiliated

This is an independent community project. It is not made, endorsed or supported
by Linksys or Belkin. "Linksys" and "Velop" are trademarks of their owners.

## License

[MIT](LICENSE)
