"""Poll a Velop mesh and return one JSON-serialisable snapshot."""
from __future__ import annotations

import ipaddress
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import probes
from .jnap import AuthError, JnapClient, JnapError

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.I)


def _is_private(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _parse_ts(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _client_name(dev, mac):
    name = (dev.get("friendlyName") or "").strip()
    if name and not UUID_RE.match(name):
        return name
    model = dev.get("model") or {}
    base = model.get("modelNumber") or model.get("manufacturer") or "Device"
    return f"{base} ({mac[-5:]})"


def _radios(output):
    radios = []
    for r in output.get("radios", []):
        s = r.get("settings", {})
        radios.append({
            "id": r.get("radioID"), "band": r.get("band"), "bssid": r.get("bssid"),
            "enabled": s.get("isEnabled"), "ssid": s.get("ssid"),
            "broadcast": s.get("broadcastSSID"), "channel": s.get("channel"),
        })
    return radios


def _ping(pinger, ip):
    """Normalise a pinger result to (loss, rtt, jitter); older pingers return two values."""
    res = pinger(ip)
    return res[0], res[1], (res[2] if len(res) > 2 else None)


def _ping_entry(pinger, ip, label):
    loss, rtt, jitter = _ping(pinger, ip)
    return {"target": ip, "label": label, "loss": loss, "rtt_ms": rtt, "jitter_ms": jitter}


def _ping_many(pinger, targets, label):
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        return list(pool.map(lambda t: _ping_entry(pinger, t, label), targets))


def collect(cfg, password, *, client=JnapClient, pinger=probes.ping_host,
            probe=probes, now=time.time):
    ts = int(now())
    snap = {
        "ts": ts, "primary": {"ip": cfg.primary, "reachable": False, "auth_ok": None},
        "nodes": [], "clients": [], "wan": {}, "steering": None,
        "internet": [], "upstream": None, "errors": {}, "offline_known": 0,
        "probes": {}, "dhcp": None,
    }

    def jc(host):
        return client(host, cfg.username, password)

    primary = jc(cfg.primary)
    try:
        info = primary.call("core/GetDeviceInfo", auth=False)
    except JnapError:
        snap["internet"] = _ping_many(pinger, cfg.ping_targets, "internet")
        return snap
    snap["primary"].update(
        reachable=True, serial=info.get("serialNumber"),
        model=info.get("modelNumber"), firmware=info.get("firmwareVersion"))

    def authed(key, action, payload=None):
        try:
            out = primary.call(action, payload)
            if snap["primary"]["auth_ok"] is None:
                snap["primary"]["auth_ok"] = True
            return out
        except AuthError:
            snap["primary"]["auth_ok"] = False
        except JnapError as e:
            snap["errors"][key] = e.result
        return None

    devices_out = authed("devices", "devicelist/GetDevices3")
    if snap["primary"]["auth_ok"] is False:
        snap["internet"] = _ping_many(pinger, cfg.ping_targets, "internet")
        return snap
    backhaul_out = authed("backhaul", "nodes/diagnostics/GetBackhaulInfo")
    wireless_out = authed("wireless", "nodes/networkconnections/GetNodesWirelessNetworkConnections")
    steering_out = authed("steering", "nodes/topologyoptimization/GetTopologyOptimizationSettings")
    wan_out = authed("wan", "router/GetWANStatus3")
    primary_radios = authed("radios", "wirelessap/GetRadioInfo3")

    if steering_out is not None:
        snap["steering"] = bool(steering_out.get("isClientSteeringEnabled"))
    if wan_out:
        conn = wan_out.get("wanConnection") or {}
        wan_ip = conn.get("ipAddress")
        snap["wan"] = {
            "status": wan_out.get("wanStatus"), "type": conn.get("wanType"),
            "ip": wan_ip, "gateway": conn.get("gateway"),
            "dns": [d for d in (conn.get("dnsServer1"), conn.get("dnsServer2")) if d],
            "double_nat": bool(wan_ip) and _is_private(wan_ip),
        }

    devices = (devices_out or {}).get("devices", [])
    backhaul = {b["deviceUUID"]: b for b in (backhaul_out or {}).get("backhaulDevices", [])
                if "deviceUUID" in b}

    # --- nodes ---------------------------------------------------------------
    nodes = []
    for dev in devices:
        if (dev.get("model") or {}).get("deviceType") != "Infrastructure":
            continue
        conn = (dev.get("connections") or [{}])[0]
        bh = backhaul.get(dev["deviceID"])
        # Prefer the backhaul address: after a failed setup the device entry can keep
        # an address from a second network the node briefly created.
        ip = (bh.get("ipAddress") if bh else None) or conn.get("ipAddress")
        is_primary = bool(dev.get("isAuthority")) or dev.get("nodeType") == "Master" \
            or ip == cfg.primary
        node = {
            "id": dev["deviceID"], "name": dev.get("friendlyName") or "Node",
            "serial": (dev.get("unit") or {}).get("serialNumber"), "ip": ip,
            "role": "primary" if is_primary else "node",
            "firmware": (dev.get("unit") or {}).get("firmwareVersion"),
            "parent_id": conn.get("parentDeviceID"),
            "parent_ip": bh.get("parentIPAddress") if bh else None,
            "backhaul": None,
            "in_mesh": is_primary or bh is not None,
            "reachable": None, "loss": None, "rtt_ms": None, "jitter_ms": None,
            "api_ok": None, "auth_ok": None, "default_pw": None,
            "radios": [], "clients": 0, "clients_24ghz": 0,
        }
        if bh:
            try:
                mbps = int(bh.get("speedMbps"))
            except (TypeError, ValueError):
                mbps = None
            measured = _parse_ts(bh.get("timestamp"))
            node["backhaul"] = {
                "type": bh.get("connectionType"), "mbps": mbps,
                "age_s": int(ts - measured) if measured else None,
                "rssi": (bh.get("wirelessConnectionInfo") or {}).get("stationRSSI"),
            }
        if is_primary:
            node["radios"] = _radios(primary_radios or {})
            node["api_ok"], node["auth_ok"] = True, True
        nodes.append(node)

    # --- probe every node, and run the active probes, in parallel ----------------
    def probe_node(node):
        if not node["ip"]:
            if node["in_mesh"]:
                node["reachable"] = False
            return
        loss, rtt, jitter = _ping(pinger, node["ip"])
        node.update(loss=loss, rtt_ms=rtt, jitter_ms=jitter, reachable=loss < 100)
        if node["role"] == "primary" or not node["reachable"]:
            return
        c = jc(node["ip"])
        try:
            c.call("core/GetDeviceInfo", auth=False)
            node["api_ok"] = True
        except JnapError:
            node["api_ok"] = False
            return
        try:
            node["default_pw"] = bool(c.call("core/IsAdminPasswordDefault", auth=False)
                                      .get("isAdminPasswordDefault"))
        except JnapError:
            pass
        try:
            node["radios"] = _radios(c.call("wirelessap/GetRadioInfo3"))
            node["auth_ok"] = True
        except AuthError:
            node["auth_ok"] = False
        except JnapError:
            pass

    wan = snap["wan"] or {}
    inet_target = cfg.ping_targets[0] if cfg.ping_targets else "1.1.1.1"
    dns_targets = [("router", cfg.primary)]
    dns_targets += [("isp", d) for d in wan.get("dns", [])[:1] if d != cfg.primary]
    dns_targets.append(("public", "1.1.1.1"))

    with ThreadPoolExecutor(max_workers=16) as pool:
        node_f = [pool.submit(probe_node, n) for n in nodes]
        inet_f = pool.submit(_ping_many, pinger, cfg.ping_targets, "internet")
        up_f = pool.submit(_ping_entry, pinger, wan["gateway"], "upstream") \
            if wan.get("gateway") else None
        dns_f = [(label, server, pool.submit(probe.dns_probe, server)) for label, server in dns_targets]
        tcp_f = [(t, pool.submit(probe.tcp_probe, t)) for t in cfg.ping_targets]
        mtu_f = pool.submit(probe.path_mtu, inet_target)
        rping_f = pool.submit(probe.router_ping, primary, inet_target) \
            if getattr(cfg, "router_ping", True) else None
        dhcp_f = pool.submit(probe.dhcp_usage, primary)
        for f in node_f:
            f.result()
        snap["internet"] = inet_f.result()
        snap["upstream"] = up_f.result() if up_f else None
        snap["probes"] = {
            "dns": [{"label": label, "server": server, **f.result()} for label, server, f in dns_f],
            "tcp": [{"target": f"{t}:443", "ms": f.result()} for t, f in tcp_f],
            "mtu": mtu_f.result(),
            "host_wifi": None,
            "router_ping": rping_f.result() if rping_f else None,
        }
        snap["dhcp"] = dhcp_f.result()

    # Asking macOS for the Wi-Fi link makes it scan for networks, which pauses the radio
    # and adds tens of milliseconds to every ping. So it must run after all measurements.
    snap["probes"]["host_wifi"] = probe.host_wifi()

    # --- clients -------------------------------------------------------------
    node_by_id = {n["id"]: n for n in nodes}
    wl = {}
    for entry in (wireless_out or {}).get("nodeWirelessConnections", []):
        for c in entry.get("connections", []):
            w = c.get("wireless") or {}
            seen = _parse_ts(c.get("timestamp"))
            wl[c["macAddress"].upper()] = {
                "node_id": entry.get("deviceID"), "band": w.get("band"),
                "dbm": w.get("signalDecibels"), "mbps": c.get("negotiatedMbps"),
                "age_s": int(ts - seen) if seen else None,
            }
    for dev in devices:
        if (dev.get("model") or {}).get("deviceType") == "Infrastructure":
            continue
        conns = dev.get("connections") or []
        if not conns:
            snap["offline_known"] += 1
            continue
        mac = conns[0].get("macAddress", "").upper()
        w = wl.get(mac)
        kinds = {i.get("interfaceType") for i in dev.get("knownInterfaces", [])}
        # The device list is current; the wireless table only records signal when a
        # device connects or roams, so it is used for numbers, not for placement.
        node_id = conns[0].get("parentDeviceID") or (w or {}).get("node_id")
        parent = node_by_id.get(node_id)
        snap["clients"].append({
            "mac": mac, "name": _client_name(dev, mac), "ip": conns[0].get("ipAddress"),
            "kind": "wireless" if (w or "Wireless" in kinds) else "wired",
            "node_id": node_id, "node": parent["name"] if parent else None,
            "band": (w or {}).get("band"), "dbm": (w or {}).get("dbm"),
            "mbps": (w or {}).get("mbps"), "age_s": (w or {}).get("age_s"),
            "vendor": (dev.get("model") or {}).get("manufacturer"),
        })
    for c in snap["clients"]:
        n = node_by_id.get(c["node_id"])
        if n and c["kind"] == "wireless":
            n["clients"] += 1
            if c["band"] == "2.4GHz":
                n["clients_24ghz"] += 1

    snap["nodes"] = nodes
    return snap
