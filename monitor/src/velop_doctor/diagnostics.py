"""On-demand diagnostics: ping and DNS lookups that a person (or Claude) asks for.

Unlike the polling probes, these take arguments, so every argument is validated: they end up
in a command line (`ping`) or in a request to the router, never in a shell.
"""
from __future__ import annotations

import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor

from . import probes

_HOSTNAME = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})*$")
DNS_ALIASES = ("router", "isp", "public")


class DiagnosticError(Exception):
    """A bad argument, or a diagnostic that cannot run. The message is safe to show."""


def valid_host(value):
    """An IPv4 address or a hostname; nothing that could be read as a command-line option."""
    host = str(value or "").strip()
    if not host or len(host) > 253:
        raise DiagnosticError("host must be an IPv4 address or a hostname.")
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    if not _HOSTNAME.match(host):
        raise DiagnosticError(f"{host[:40]!r} is not a valid IPv4 address or hostname.")
    return host


def valid_count(value, default=5, low=1, high=20):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise DiagnosticError(f"count must be a whole number from {low} to {high}.")
    return value


def run_ping(params, *, pinger=probes.ping_host, router_ping=None):
    """Ping from this computer, from the router, or both, so the two views can be compared."""
    host = valid_host(params.get("host"))
    count = valid_count(params.get("count"))
    source = params.get("from") or "both"
    if source not in ("computer", "router", "both"):
        raise DiagnosticError("from must be computer, router or both.")
    if source == "router" and router_ping is None:
        raise DiagnosticError("The router cannot ping from here (not connected to a Velop).")
    out = {"host": host, "count": count}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {}
        if source in ("computer", "both"):
            futures["computer"] = pool.submit(pinger, host, count)
        if source in ("router", "both") and router_ping is not None:
            futures["router"] = pool.submit(router_ping, host, count)
        for name, future in futures.items():
            result = future.result()
            if name == "computer":
                loss, avg, jitter = result
                out["computer"] = {"loss_pct": loss, "avg_ms": avg, "jitter_ms": jitter}
            else:
                out["router"] = (None if result is None else
                                 {"loss_pct": result["loss"], "avg_ms": result["rtt_ms"]})
    if "router" in out and out["router"] is None:
        out["router_note"] = "The router did not return a result (unsupported by this firmware, or busy)."
    return out


def run_dns(params, servers, *, dns_probe=probes.dns_probe):
    """Resolve a name through the router, the ISP's server, a public one, or an IPv4 address."""
    name = valid_host(params.get("name"))
    wanted = str(params.get("server") or "router").strip()
    if wanted in DNS_ALIASES:
        address = servers.get(wanted)
        if not address:
            raise DiagnosticError(f"No {wanted} DNS server is known yet; run the diagnostics first.")
    else:
        try:
            address = str(ipaddress.IPv4Address(wanted))
        except ValueError:
            raise DiagnosticError("server must be router, isp, public or an IPv4 address.") from None
    result = dns_probe(address, name)
    return {"name": name, "server": address, "resolved": result["ok"], "ms": result["ms"]}


def diagnose(tool, params, snap, *, pinger=probes.ping_host, router_ping=None,
             dns_probe=probes.dns_probe):
    if tool == "ping":
        return run_ping(params or {}, pinger=pinger, router_ping=router_ping)
    if tool == "dns_lookup":
        return run_dns(params or {}, known_dns_servers(snap), dns_probe=dns_probe)
    raise DiagnosticError(f"Unknown diagnostic: {str(tool)[:40]}")


def known_dns_servers(snapshot):
    """Map router/isp/public to the addresses the last poll used."""
    return {d["label"]: d["server"] for d in ((snapshot or {}).get("probes") or {}).get("dns", [])}
