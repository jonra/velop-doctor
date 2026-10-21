"""Poll a Velop mesh and return one JSON-serialisable snapshot."""
from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .jnap import AuthError, JnapClient, JnapError

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.I)


def ping_host(ip, count=3):
    """Return (loss_percent, avg_rtt_ms or None)."""
    if sys.platform == "darwin":
        cmd = ["ping", "-c", str(count), "-i", "0.3", "-t", "4", ip]
    else:
        cmd = ["ping", "-c", str(count), "-i", "0.3", "-W", "2", ip]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return 100.0, None
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"=\s*[\d.]+/([\d.]+)/", out)
    return (float(loss.group(1)) if loss else 100.0,
            float(rtt.group(1)) if rtt else None)


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


def collect(cfg, password, *, client=JnapClient, pinger=ping_host, now=time.time):
    ts = int(now())
    snap = {
        "ts": ts, "primary": {"ip": cfg.primary, "reachable": False, "auth_ok": None},
        "nodes": [], "clients": [], "wan": {}, "steering": None,
        "internet": [], "upstream": None, "errors": {}, "offline_known": 0,
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
        ip = conn.get("ipAddress")
        is_primary = bool(dev.get("isAuthority")) or dev.get("nodeType") == "Master" \
            or ip == cfg.primary
        bh = backhaul.get(dev["deviceID"])
        node = {
            "id": dev["deviceID"], "name": dev.get("friendlyName") or "Node",
            "serial": (dev.get("unit") or {}).get("serialNumber"), "ip": ip,
            "role": "primary" if is_primary else "node",
            "firmware": (dev.get("unit") or {}).get("firmwareVersion"),
            "parent_id": conn.get("parentDeviceID"),
            "parent_ip": bh.get("parentIPAddress") if bh else None,
            "backhaul": None,
            "in_mesh": is_primary or bh is not None,
            "reachable": None, "loss": None, "rtt_ms": None,
            "api_ok": None, "auth_ok": None, "default_pw": None,
            "radios": [], "clients": 0,
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

    # --- probe every node in parallel ---------------------------------------
    def probe(node):
        if not node["ip"]:
            return
        loss, rtt = pinger(node["ip"])
        node.update(loss=loss, rtt_ms=rtt, reachable=loss < 100)
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

    wan_gw = (snap["wan"] or {}).get("gateway")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(probe, n) for n in nodes]
        inet = pool.submit(_ping_many, pinger, cfg.ping_targets, "internet")
        up = pool.submit(_ping_one, pinger, wan_gw, "upstream") if wan_gw else None
        for f in futures:
            f.result()
        snap["internet"] = inet.result()
        snap["upstream"] = up.result() if up else None

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
        node_id = (w or {}).get("node_id") or conns[0].get("parentDeviceID")
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
        if n and c["kind"] == "wireless" and (
                c["age_s"] is None or c["age_s"] <= cfg.stale_client_seconds):
            n["clients"] += 1

    snap["nodes"] = nodes
    return snap


def _ping_one(pinger, ip, label):
    loss, rtt = pinger(ip)
    return {"target": ip, "label": label, "loss": loss, "rtt_ms": rtt}


def _ping_many(pinger, targets, label):
    if not targets:
        return []
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        return list(pool.map(lambda t: _ping_one(pinger, t, label), targets))
