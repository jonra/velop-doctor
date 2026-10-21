# Notes on the Velop local API (JNAP)

Velop nodes expose an undocumented JSON API at `http://<node>/JNAP/`. It is what the
Linksys app uses on your LAN. These notes come from a WHW03 v2 on firmware 2.1.20 and may
differ on other models.

**Request:** `POST /JNAP/` with headers `X-JNAP-Action: http://linksys.com/jnap/<service>/<Action>`
and, for most actions, `X-JNAP-Authorization: Basic base64(admin:<router password>)`.
The body is a JSON object (usually `{}`).

**Response:** `{"result": "OK", "output": {...}}` or `{"result": "_ErrorUnauthorized"}` and so on.

> **A failed login echoes the submitted credentials back** in the response's `error`
> field. Never log or display that field.

## Actions used

| Action | Auth | Used for |
|---|---|---|
| `core/GetDeviceInfo` | no | Model, serial, firmware, list of supported services. Works on every node. |
| `core/IsAdminPasswordDefault` | no | Whether a node is at factory defaults. |
| `devicelist/GetDevices3` | yes | Every known device, including nodes (`deviceType: Infrastructure`), with `connections[].parentDeviceID` telling which node a device uses. |
| `nodes/diagnostics/GetBackhaulInfo` | yes | Per node: wired or wireless, parent IP, speed, timestamp. |
| `nodes/networkconnections/GetNodesWirelessNetworkConnections` | yes | Wireless clients per node with band, **dBm**, link rate, timestamp. |
| `nodes/topologyoptimization/Get/SetTopologyOptimizationSettings` | yes | Client steering (`isClientSteeringEnabled`). |
| `router/GetWANStatus3` | yes | WAN status, address, gateway. |
| `wirelessap/GetRadioInfo3` | yes | Radios: SSID, enabled, BSSID. Works on **each node directly**. |
| `core/Reboot` | yes | Reboot (experimental). |

## Things that surprised us

- **Each node has its own API and checks the password itself.** A node that was reset or
  is not synced answers `GetDeviceInfo` but rejects the mesh password. That is the cheapest reliable
  sync test.
- **Backhaul speed is a periodic measurement**, with its own timestamp (hours old is normal).
  Do not read it as a live link rate.
- **`GetNetworkConnections2` reports signal as a small positive number (SNR-like)**, while
  `GetNodesWirelessNetworkConnections` reports negative dBm. Use the latter.
- **Wireless entries of sleeping devices linger** with an old timestamp. Check the age.
- **A reset node can still answer on your LAN** if it rejoined by Wi-Fi, and a re-add in the app can
  leave a half-registered entry that counts as a node but has no backhaul ("ghost").
- Node names default to `LINKSYS<last 5 digits of serial>`; clients often have a UUID as name.
