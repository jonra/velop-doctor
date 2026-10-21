# velop-doctor

Debugging tools for **Linksys Velop** mesh networks, for when the official app
tells you everything is fine and the Wi-Fi says otherwise.

The Linksys app shows a pretty diagram. It does not show link speeds, history,
which devices sit on which node, whether a node is really in sync with the mesh,
or what happened while you were not looking. This repo does.

| Part | What it is |
|---|---|
| [`monitor/`](monitor) | A Python dashboard (no dependencies) that polls your Velop every minute, keeps history, and runs checks: nodes offline or out of sync, stale "ghost" nodes, wireless backhaul, slow wired links, idle nodes, weak devices, double NAT, internet drops. |
| [`probe/`](probe) | A Raspberry Pi Zero cable tester with an e-paper display: plug it into any wall socket and it shows link speed, which switch port you reached, DHCP, gateway, DNS and internet in seconds. |
| [`docs/`](docs) | What each check means, and notes on the (unofficial) JNAP API the Velop speaks. |

The two parts are independent. Use either, or both.

## Quick start (monitor)

```bash
cd monitor
python3 -m pip install -e .
velop-doctor serve --demo      # try the dashboard with made-up data first
velop-doctor setup             # finds your main node and stores the admin password
velop-doctor serve             # http://127.0.0.1:8765
```

Needs Python 3.9 or newer. Works on macOS and Linux. See [`monitor/README.md`](monitor/README.md).

## Status

Early. Built and tested against a three-node WHW03 v2 (firmware 2.1.20) mesh.
Other Velop models and firmware will probably work, since they share the same
local API, but that is untested. Reports and fixes welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Not affiliated

This is an independent community project. It is not made, endorsed or supported
by Linksys or Belkin. "Linksys" and "Velop" are trademarks of their owners.

## License

[MIT](LICENSE)
