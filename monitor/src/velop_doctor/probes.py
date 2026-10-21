"""Active probes run from the computer hosting the monitor (standard library only).

Each probe returns plain data, never raises, and gives up quickly: a probe that
cannot run must not stall a poll.
"""
from __future__ import annotations

import ipaddress
import random
import threading
import re
import socket
import struct
import subprocess
import sys
import time

from .jnap import JnapError


# --- ICMP --------------------------------------------------------------------

def ping_host(ip, count=6):
    """Return (loss_percent, avg_rtt_ms or None, jitter_ms or None)."""
    if sys.platform == "darwin":
        cmd = ["ping", "-c", str(count), "-i", "0.25", "-t", "5", ip]
    else:
        cmd = ["ping", "-c", str(count), "-i", "0.25", "-W", "2", ip]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return 100.0, None, None
    loss = re.search(r"([\d.]+)% packet loss", out)
    stats = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+/([\d.]+)", out)
    return (float(loss.group(1)) if loss else 100.0,
            float(stats.group(1)) if stats else None,
            float(stats.group(2)) if stats else None)


def path_mtu(ip="1.1.1.1"):
    """Largest packet that crosses the path without fragmenting, or None."""
    for payload in (1472, 1464, 1452, 1400, 1280):
        if sys.platform == "darwin":
            cmd = ["ping", "-c", "1", "-D", "-s", str(payload), "-t", "3", ip]
        else:
            cmd = ["ping", "-c", "1", "-M", "do", "-s", str(payload), "-W", "2", ip]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0:
            return payload + 28
    return None


# --- DNS ---------------------------------------------------------------------

def _dns_query(name):
    qid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    return qid, header + labels + b"\x00" + struct.pack(">HH", 1, 1)


def dns_probe(server, name="example.com", timeout=3.0):
    """Resolve `name` through `server`. Returns {"ok": bool, "ms": float or None}."""
    qid, packet = _dns_query(name)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    started = time.monotonic()
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(1500)
    except OSError:
        return {"ok": False, "ms": None}
    finally:
        sock.close()
    ms = (time.monotonic() - started) * 1000
    if len(data) < 12:
        return {"ok": False, "ms": None}
    rid, flags, _, answers = struct.unpack(">HHHH", data[:8])
    ok = rid == qid and (flags & 0x000F) == 0 and answers > 0
    return {"ok": ok, "ms": round(ms, 1) if ok else None}


# --- TCP ---------------------------------------------------------------------

def tcp_probe(host, port=443, timeout=3.0):
    """Time a TCP connect. Returns milliseconds, or None on failure."""
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.monotonic() - started) * 1000, 1)
    except OSError:
        return None


# --- this computer's own Wi-Fi ---------------------------------------------------

WIFI_CACHE_SECONDS = 600
_wifi_cache = {"at": 0.0, "value": None}
_wifi_lock = threading.Lock()


def host_wifi(max_age=None):
    """Signal quality of the monitoring computer's Wi-Fi link, or None if wired/unknown.

    On macOS, reading it makes the radio scan for networks, which adds ~40 ms to every
    ping that runs at the same time and briefly disturbs the computer's own traffic. So the
    result is cached (10 minutes by default) and callers must not run it during a measurement.
    """
    max_age = WIFI_CACHE_SECONDS if max_age is None else max_age
    with _wifi_lock:
        if time.monotonic() - _wifi_cache["at"] < max_age and _wifi_cache["at"]:
            return _wifi_cache["value"]
        value = _host_wifi_uncached()
        _wifi_cache.update(at=time.monotonic(), value=value)
        return value


def _host_wifi_uncached():
    try:
        if sys.platform == "darwin":
            return _host_wifi_macos()
        return _host_wifi_linux()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _host_wifi_macos():
    out = subprocess.run(["system_profiler", "SPAirPortDataType"], capture_output=True,
                         text=True, timeout=25).stdout
    return parse_macos_airport(out)


def parse_macos_airport(out):
    section = out.split("Current Network Information:", 1)
    if len(section) < 2:
        return None
    block = re.split(r"\n\s*Other Local Wi-Fi Networks:", section[1], maxsplit=1)[0]
    sig = re.search(r"Signal / Noise:\s*(-?\d+) dBm / (-?\d+) dBm", block)
    if not sig:
        return None
    channel = re.search(r"Channel:\s*(\d+) \((\d(?:\.\d)?)GHz", block)
    rate = re.search(r"Transmit Rate:\s*(\d+)", block)
    phy = re.search(r"PHY Mode:\s*(\S+)", block)
    signal, noise = int(sig.group(1)), int(sig.group(2))
    return {"signal_dbm": signal, "noise_dbm": noise, "snr_db": signal - noise,
            "channel": int(channel.group(1)) if channel else None,
            "band": f"{channel.group(2)}GHz" if channel else None,
            "rate_mbps": int(rate.group(1)) if rate else None,
            "phy": phy.group(1) if phy else None}


def _host_wifi_linux():
    dev = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=5).stdout
    iface = re.search(r"Interface\s+(\S+)", dev)
    if not iface:
        return None
    link = subprocess.run(["iw", "dev", iface.group(1), "link"], capture_output=True,
                          text=True, timeout=5).stdout
    sig = re.search(r"signal:\s*(-?\d+) dBm", link)
    if not sig:
        return None
    rate = re.search(r"tx bitrate:\s*([\d.]+)", link)
    freq = re.search(r"freq:\s*(\d+)", link)
    return {"signal_dbm": int(sig.group(1)), "noise_dbm": None, "snr_db": None,
            "channel": None, "band": "5GHz" if freq and int(freq.group(1)) > 4900 else "2.4GHz",
            "rate_mbps": int(float(rate.group(1))) if rate else None, "phy": None}


# --- router-side probes (JNAP) ---------------------------------------------------

def router_ping(client, target="1.1.1.1", count=4, wait=10):
    """Ping from the router itself. Separates 'the line is bad' from 'my Wi-Fi is bad'."""
    try:
        client.call("diagnostics/StartPing",
                    {"host": target, "packetSizeBytes": 32, "pingCount": count})
        deadline = time.monotonic() + wait
        while True:
            time.sleep(1)
            status = client.call("diagnostics/GetPingStatus")
            if not status.get("isRunning") or time.monotonic() > deadline:
                break
    except JnapError:
        return None
    log = status.get("pingLog") or ""
    loss = re.search(r"([\d.]+)% packet loss", log)
    avg = re.search(r"=\s*[\d.]+/([\d.]+)/", log)
    if not loss:
        return None
    return {"target": target, "loss": float(loss.group(1)),
            "rtt_ms": float(avg.group(1)) if avg else None}


def dhcp_usage(client):
    """How full the DHCP pool is. Returns {"pool": int, "leases": int} or None."""
    try:
        lan = client.call("router/GetLANSettings").get("dhcpSettings") or {}
        first, last = lan.get("firstClientIPAddress"), lan.get("lastClientIPAddress")
        leases = client.call("router/GetDHCPClientLeases").get("leases", [])
        pool = int(ipaddress.ip_address(last)) - int(ipaddress.ip_address(first)) + 1
    except (JnapError, TypeError, ValueError):
        return None
    return {"pool": pool, "leases": len(leases)}
