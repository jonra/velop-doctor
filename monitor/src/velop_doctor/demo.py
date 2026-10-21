"""Synthetic snapshots: a made-up house, used by --demo and by the tests."""
from __future__ import annotations

import math
import time

MAIN, HALL, BED, GHOST = "id-main", "id-hall", "id-bed", "id-ghost"


def _node(id_, name, ip, role, **kw):
    n = {"id": id_, "name": name, "serial": "DEMO" + id_[-4:].upper(), "ip": ip, "role": role,
         "firmware": "2.1.20.216892", "parent_id": MAIN if role == "node" else None,
         "parent_ip": "192.168.1.1" if role == "node" else None, "backhaul": None,
         "in_mesh": True, "reachable": True, "loss": 0.0, "rtt_ms": 4.0, "jitter_ms": 0.8, "api_ok": True,
         "auth_ok": True, "default_pw": False, "clients": 0, "clients_24ghz": 0,
         "radios": [{"id": r, "band": b, "bssid": None, "enabled": True, "ssid": "Home Wi-Fi",
                     "broadcast": True, "channel": 0}
                    for r, b in (("RADIO_2.4GHz", "2.4GHz"), ("RADIO_5GHz", "5GHz"),
                                 ("RADIO_5GHz_2", "5GHz"))]}
    n.update(kw)
    return n


_CLIENTS = [  # name, node, kind, band, dbm, mbps
    ("Living room TV", MAIN, "wired", None, None, None),
    ("NAS", MAIN, "wired", None, None, None),
    ("MacBook Air", MAIN, "wireless", "5GHz", -58, 866),
    ("iPhone", MAIN, "wireless", "5GHz", -62, 433),
    ("Sonos Play:5", MAIN, "wireless", "2.4GHz", -61, 72),
    ("Kitchen display", MAIN, "wireless", "5GHz", -71, 325),
    ("Smart plug", MAIN, "wireless", "2.4GHz", -64, 72),
    ("Thermostat", MAIN, "wireless", "2.4GHz", -55, 72),
    ("Living Room Display", MAIN, "wireless", "2.4GHz", -88, 13),
    ("Nest cam", MAIN, "wireless", "5GHz", -87, 6),
    ("Ring doorbell", MAIN, "wireless", "2.4GHz", -77, 1),
    ("Office speaker", MAIN, "wireless", "2.4GHz", -76, 2),
    ("Garden sensor", MAIN, "wireless", "2.4GHz", -82, 6),
    ("Work laptop", HALL, "wireless", "5GHz", -52, 650),
    ("Printer", HALL, "wireless", "2.4GHz", -60, 72),
]


def make_snapshot(cfg, scenario="problems", steering=False, now=None):
    ts = int(now or time.time())
    wob = math.sin(ts / 40.0)
    problems = scenario == "problems"

    main = _node(MAIN, "Living room (main)", "192.168.1.1", "primary", rtt_ms=3.6 + wob)
    hall = _node(HALL, "Hall", "192.168.1.96", "node", rtt_ms=5.2 + wob,
                 backhaul={"type": "Wired", "mbps": 1024, "age_s": 5400, "rssi": None})
    bed = _node(BED, "Bedroom", "192.168.1.130", "node", rtt_ms=9.0 + 3 * wob,
                backhaul={"type": "Wired", "mbps": 1024, "age_s": 5400, "rssi": None})
    nodes = [main, hall, bed]
    if problems:
        bed.update(auth_ok=False)
        nodes.append(_node(GHOST, "Old Bedroom (stale)", "192.168.1.131", "node", in_mesh=False,
                           reachable=None, loss=None, rtt_ms=None, api_ok=None, auth_ok=None,
                           radios=[]))

    clients = []
    for i, (name, node_id, kind, band, dbm, mbps) in enumerate(_CLIENTS):
        if not problems and node_id == MAIN and kind == "wireless" and i % 2:
            node_id = BED if i % 4 == 1 else HALL
        if not problems and dbm is not None:
            dbm = max(dbm, -68)  # a healthy house has no weak links
        node = next(n for n in nodes if n["id"] == node_id)
        clients.append({
            "mac": f"AA:BB:CC:00:00:{i:02X}", "name": name, "ip": f"192.168.1.{100 + i}",
            "kind": kind, "node_id": node_id, "node": node["name"], "band": band,
            "dbm": None if dbm is None else dbm + (1 if wob > 0 else 0),
            "mbps": mbps, "age_s": 20, "vendor": None})
        if kind == "wireless":
            node["clients"] += 1
            if band == "2.4GHz":
                node["clients_24ghz"] += 1

    return {
        "ts": ts,
        "primary": {"ip": "192.168.1.1", "reachable": True, "auth_ok": True,
                    "serial": main["serial"], "model": "WHW03", "firmware": main["firmware"]},
        "nodes": nodes, "clients": clients, "offline_known": 5,
        "wan": {"status": "Connected", "type": "DHCP", "ip": "192.168.178.150",
                "gateway": "192.168.178.1", "double_nat": True},
        "steering": steering,
        "internet": [
            {"target": "1.1.1.1", "label": "internet", "loss": 0.0, "rtt_ms": 24 + 6 * wob, "jitter_ms": 3.1},
            {"target": "8.8.8.8", "label": "internet", "loss": 0.0, "rtt_ms": 15 + 3 * wob, "jitter_ms": 2.2}],
        "upstream": {"target": "192.168.178.1", "label": "upstream", "loss": 0.0,
                     "rtt_ms": 7 + wob},
        "errors": {},
        "probes": {
            "dns": [{"label": "router", "server": "192.168.1.1", "ok": True, "ms": 18 + 4 * wob},
                    {"label": "isp", "server": "84.116.46.23", "ok": not problems, "ms": None if problems else 26.0},
                    {"label": "public", "server": "1.1.1.1", "ok": True, "ms": 14.0}],
            "tcp": [{"target": "1.1.1.1:443", "ms": 17 + 3 * wob}, {"target": "8.8.8.8:443", "ms": 16.0}],
            "mtu": 1492,
            "host_wifi": {"signal_dbm": -57, "noise_dbm": -94, "snr_db": 37, "channel": 100,
                          "band": "5GHz", "rate_mbps": 702, "phy": "802.11ac"},
            "router_ping": {"target": "1.1.1.1", "loss": 0.0, "rtt_ms": 10.5},
        },
        "dhcp": {"pool": 245, "leases": 214 if problems else 39},
    }
