"""Test doubles: a fake JNAP client and a small, realistic mesh."""
from velop_doctor.config import Config
from velop_doctor.jnap import AuthError, JnapError, Unreachable

MAIN_ID, HALL_ID, BED_ID, GHOST_ID = "uuid-main", "uuid-hall", "uuid-bed", "uuid-ghost"


def infra(dev_id, name, serial, ip, *, authority=False, parent=None):
    return {
        "deviceID": dev_id, "friendlyName": name, "isAuthority": authority,
        "nodeType": "Master" if authority else "Slave",
        "model": {"deviceType": "Infrastructure", "manufacturer": "Linksys", "modelNumber": "WHW03"},
        "unit": {"serialNumber": serial, "firmwareVersion": "2.1.20.216892"},
        "knownInterfaces": [{"macAddress": "30:23:03:00:00:01", "interfaceType": "Wired"}],
        "connections": [{"macAddress": "30:23:03:00:00:01", "ipAddress": ip, "parentDeviceID": parent}],
    }


def phone(dev_id, name, mac, ip, parent):
    return {
        "deviceID": dev_id, "friendlyName": name,
        "model": {"deviceType": "Phone", "manufacturer": "Apple Inc.", "modelNumber": "iPhone"},
        "knownInterfaces": [{"macAddress": mac, "interfaceType": "Wireless"}],
        "connections": [{"macAddress": mac, "ipAddress": ip, "parentDeviceID": parent}],
    }


RADIOS = {"radios": [
    {"radioID": "RADIO_2.4GHz", "band": "2.4GHz", "bssid": "30:23:03:AA:00:01",
     "settings": {"isEnabled": True, "ssid": "Home", "broadcastSSID": True, "channel": 0}}]}

DEVICES = {"devices": [
    infra(MAIN_ID, "Main", "S-MAIN", "192.168.1.1", authority=True),
    infra(HALL_ID, "Hall", "S-HALL", "192.168.1.96", parent=MAIN_ID),
    infra(BED_ID, "Bedroom", "S-BED", "192.168.1.130", parent=MAIN_ID),
    infra(GHOST_ID, "Old unit", "S-OLD", "192.168.1.131", parent=MAIN_ID),
    phone("p1", "Jon iPhone", "AA:00:00:00:00:01", "192.168.1.50", MAIN_ID),
    phone("p2", "6a78ec9d-bb0f-4687-a63a-8b2d5da8f113", "AA:00:00:00:00:02", "192.168.1.51", HALL_ID),
    {"deviceID": "off", "friendlyName": "Old tablet", "model": {"deviceType": "Tablet"},
     "knownInterfaces": [], "connections": []},
]}

BACKHAUL = {"backhaulDevices": [
    {"deviceUUID": HALL_ID, "ipAddress": "192.168.1.96", "parentIPAddress": "192.168.1.1",
     "connectionType": "Wired", "speedMbps": "1024", "timestamp": "2026-10-20T14:45:16Z"},
    {"deviceUUID": BED_ID, "ipAddress": "192.168.1.130", "parentIPAddress": "192.168.1.1",
     "connectionType": "Wired", "speedMbps": "100", "timestamp": "2026-10-20T14:45:16Z"},
]}

WIRELESS = {"nodeWirelessConnections": [{"deviceID": MAIN_ID, "connections": [
    {"macAddress": "AA:00:00:00:00:01", "negotiatedMbps": 433, "timestamp": "2026-10-20T16:00:00Z",
     "wireless": {"band": "5GHz", "signalDecibels": -62}}]}]}

WAN = {"wanStatus": "Connected",
       "wanConnection": {"wanType": "DHCP", "ipAddress": "192.168.178.150", "gateway": "192.168.178.1"}}


class FakeClient:
    """Answers JNAP calls from a table: {host: {action: output | Exception}}."""

    def __init__(self, table, host, calls=None):
        self.table, self.host, self.calls = table, host, calls if calls is not None else []

    def call(self, action, payload=None, *, auth=True):
        self.calls.append((self.host, action, payload))
        host = self.table.get(self.host)
        if host is None:
            raise Unreachable("down", action)
        res = host.get(action, JnapError("_ErrorUnknownAction", action))
        if callable(res):
            res = res(payload)
        if isinstance(res, Exception):
            raise res
        return res


def world(**overrides):
    main = {
        "core/GetDeviceInfo": {"serialNumber": "S-MAIN", "modelNumber": "WHW03", "firmwareVersion": "2.1.20.216892"},
        "devicelist/GetDevices3": DEVICES,
        "nodes/diagnostics/GetBackhaulInfo": BACKHAUL,
        "nodes/networkconnections/GetNodesWirelessNetworkConnections": WIRELESS,
        "nodes/topologyoptimization/GetTopologyOptimizationSettings": {"isClientSteeringEnabled": False},
        "router/GetWANStatus3": WAN,
        "wirelessap/GetRadioInfo3": RADIOS,
    }
    node_ok = {"core/GetDeviceInfo": {"serialNumber": "x"},
               "core/IsAdminPasswordDefault": {"isAdminPasswordDefault": False},
               "wirelessap/GetRadioInfo3": RADIOS}
    node_bad = {**node_ok, "wirelessap/GetRadioInfo3": AuthError("_ErrorUnauthorized")}
    table = {"192.168.1.1": main, "192.168.1.96": node_ok, "192.168.1.130": node_bad}
    table.update(overrides)
    return table


def cfg():
    return Config(primary="192.168.1.1")


def pinger_ok(ip, count=3):
    return 0.0, 5.0


class StubProbes:
    """Stands in for velop_doctor.probes so collector tests never touch the network."""

    def __init__(self, **over):
        self.dns = over.get("dns", {"ok": True, "ms": 20.0})
        self.tcp = over.get("tcp", 15.0)
        self.mtu = over.get("mtu", 1500)
        self.wifi = over.get("wifi", None)
        self.rping = over.get("rping", {"target": "1.1.1.1", "loss": 0.0, "rtt_ms": 9.0})
        self.dhcp = over.get("dhcp", {"pool": 245, "leases": 40})

    def dns_probe(self, server, *a, **k):
        return self.dns(server) if callable(self.dns) else dict(self.dns)

    def tcp_probe(self, host, *a, **k):
        return self.tcp

    def path_mtu(self, ip="1.1.1.1"):
        return self.mtu

    def host_wifi(self):
        return self.wifi

    def router_ping(self, client, target="1.1.1.1", **k):
        return self.rping

    def dhcp_usage(self, client):
        return self.dhcp
