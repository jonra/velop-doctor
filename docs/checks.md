# Checks

Every finding has a severity (**critical**, **warning**, **info**) and a hold time:
it appears only after the condition has lasted that long, so short blips do not alarm.

| Finding | Severity | Hold | What it means and what to try |
|---|---|---|---|
| Internet is unreachable | critical | 30 s | No ping target answers. Check the upstream router and ISP; compare with the node ping charts. |
| Internet connection is unstable | warning | 2 min | 10% or more loss, or latency above 150 ms. The finding says **where** the trouble is: if the router's own ping is fine, it is between this computer and the router; if the router sees it too, it is upstream. |
| Main node is unreachable | critical | 30 s | The router node does not answer. Check power and its cable to the upstream router. |
| Admin password rejected | critical | – | The stored password is wrong or was changed. Run `velop-doctor setup`. |
| Main node reports no WAN connection | critical | 30 s | The Velop has no uplink. |
| Double NAT | info | – | The Velop's WAN address is private: it sits behind another router. Usually harmless; can break port forwarding or calls. |
| *Node* is offline | critical | 1 min | A node in the mesh does not answer pings. |
| *Node* drops packets | warning | 1 min | 20% or more loss to the node. |
| *Node* answers pings but not its API | warning | 2 min | May still be booting, or hung. |
| *Node* is at factory defaults | warning | – | Its admin password is the default: it was reset and not (successfully) added to the mesh. |
| *Node* is out of sync | critical | 1 min | Reachable but rejects the mesh admin password, so it is not following the main node's settings. Reboot it; if that fails remove and re-add it. |
| *Node* broadcasts a different network | critical | 1 min | Its Wi-Fi name differs from the main node's: it is probably running as its own network. |
| *Node* has a Wi-Fi radio turned off | warning | 1 min | A radio is disabled on the node. |
| *Node* has no Wi-Fi clients | warning | 15 min | Five or more devices are on Wi-Fi, none on this node. Its radios may not serve, or devices never move to it. |
| *Node* is registered but not in the mesh | warning | – | The main node lists a unit that has no backhaul link ("ghost"), typical after a failed setup. Remove it in the Linksys app before adding the unit again. |
| *Node* wired link is only N Mbps | warning | – | Gigabit expected on a wired backhaul. Check cable and switch port at both ends. Note that a 100 Mbps link works, it just caps speed. |
| *Node* uses a wireless backhaul | info | – | With signal and link speed. |
| Nodes run different firmware | warning | – | Mixed versions can cause joining problems. |
| Client steering is off | info | – | With several nodes, steering moves devices to the closest one. |
| N device(s) with a weak signal | warning | 5 min | Wireless devices whose **last reported** signal is −80 dBm or worse (configurable). The Velop records signal when a device connects or roams, so this is the last known value, not a live reading. Readings older than 24 h are ignored. |
| N device(s) connected at 11 Mbps or less | info | – | Slow devices take long on the air and slow everyone sharing that radio. |
| *Node* has N devices on 2.4 GHz | info | – | 12 or more on one node's 2.4 GHz radio. |
| The DHCP address pool is almost full | warning | – | 80% or more of the pool is leased; new devices may fail to join. |
| *Node* keeps dropping out | critical | – | Three or more drop-outs in the last hour. Check power, cable and position. |
| The Velop's DNS is not answering | critical | 1 min | Lookups through the router fail while a public resolver works: sites fail to load although the internet is up. |
| Name lookups through the Velop are slow | warning | 5 min | Over 250 ms to resolve a name. |
| Your ISP's DNS server is not answering | warning | 2 min | The DNS server your ISP hands out fails while a public one works. |
| The internet answers pings but refuses connections | warning | 1 min | ICMP passes, TCP port 443 does not. |
| Path MTU is N, below the usual 1500 | info | – | Typical for PPPoE (1492). |
| This computer's own Wi-Fi link is weak | warning | 5 min | SNR below 20 dB or signal at −75 dBm or worse. Measurements from here will exaggerate problems. |
| Internet latency is jumpy | warning | 5 min | Jitter above 30 ms. |

## Connection tests

Run on every poll from the computer hosting the monitor, shown in the dashboard:

- **DNS**: a real lookup through the Velop, your ISP's server and 1.1.1.1, timed.
- **TCP**: connect time to port 443 on each ping target. Catches "pings pass, traffic does not".
- **MTU**: largest packet that crosses to the internet without fragmenting.
- **Internet seen from two places**: a ping from the router itself (via the Velop's own diagnostics) next to the same ping from this computer.
- **This computer's Wi-Fi**: signal, noise, SNR, rate and channel. On macOS reading it makes the radio scan, which adds about 40 ms to any ping running at that moment, so it is cached for 10 minutes and never runs during a measurement.
- **DHCP**: leases against pool size.

## Events (timeline)

Changes between two polls: a node joining or leaving the node list or the mesh,
backhaul type, parent or speed changing, firmware changing, the WAN address
changing, steering being toggled, a node stopping or resuming answering, a device roaming between nodes, and gaps in monitoring.

## Thresholds

Defaults live in `Config` (`monitor/src/velop_doctor/config.py`) and can be overridden in
`~/.velop-doctor/config.json`: `weak_dbm`, `weak_seconds`, `idle_node_seconds`,
`stale_client_seconds`, `interval`, `ping_targets`, `retention_days`.
