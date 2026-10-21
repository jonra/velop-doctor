"""Turn snapshots into findings (current problems) and events (changes).

`evaluate` is a pure function of one snapshot. Each finding carries `min_seconds`:
the store only reports it once the condition has held that long, so brief blips
(a node rebooting, a phone walking past) do not raise alarms.
"""
from __future__ import annotations

CRITICAL, WARNING, INFO = "critical", "warning", "info"


def _f(key, severity, title, detail, min_seconds=0):
    return {"key": key, "severity": severity, "title": title,
            "detail": detail, "min_seconds": min_seconds}


def evaluate(snap, cfg):
    out = []
    primary = snap.get("primary", {})

    inet = snap.get("internet", [])
    if inet:
        if all(i["loss"] >= 100 for i in inet):
            out.append(_f("internet_down", CRITICAL, "Internet is unreachable",
                          "None of the ping targets answer: " +
                          ", ".join(i["target"] for i in inet) + ".", 30))
        else:
            worst = max(i["loss"] for i in inet)
            rtts = [i["rtt_ms"] for i in inet if i["rtt_ms"] is not None]
            if worst >= 10 or (rtts and max(rtts) > 150):
                out.append(_f("internet_lossy", WARNING, "Internet connection is unstable",
                              f"Up to {worst:.0f}% packet loss"
                              + (f", latency up to {max(rtts):.0f} ms" if rtts else "") + ".",
                              120))

    if not primary.get("reachable"):
        out.append(_f("primary_unreachable", CRITICAL, "Main node is unreachable",
                      f"No answer from {primary.get('ip') or 'the main node'}. "
                      "Check its power and its cable to the upstream router.", 30))
        return out
    if primary.get("auth_ok") is False:
        out.append(_f("auth_failed", CRITICAL, "Admin password rejected",
                      "The main node refused the stored password. "
                      "Run `velop-doctor setup` to update it."))
        return out

    wan = snap.get("wan") or {}
    if wan.get("status") and wan["status"] != "Connected":
        out.append(_f("wan_down", CRITICAL, "Main node reports no WAN connection",
                      f"WAN status: {wan['status']}.", 30))
    if wan.get("double_nat"):
        out.append(_f("double_nat", INFO, "Double NAT",
                      f"The Velop's WAN address is private ({wan.get('ip')}), so it sits behind "
                      "another router. Usually fine, but it can break port forwarding and some "
                      "games or calls. Consider bridge mode on the upstream router."))

    nodes = snap.get("nodes", [])
    in_mesh = [n for n in nodes if n["in_mesh"]]
    ssids = {r["ssid"] for n in in_mesh if n["role"] == "primary"
             for r in n["radios"] if r.get("ssid")}
    wireless_clients = [c for c in snap.get("clients", [])
                        if c["kind"] == "wireless" and _fresh(c, cfg)]

    for n in nodes:
        name = n["name"]
        if not n["in_mesh"]:
            out.append(_f(f"ghost:{n['id']}", WARNING, f"{name} is registered but not in the mesh",
                          "The main node lists this unit, but it has no backhaul link. "
                          "Typical after a failed setup. Remove it in the Linksys app "
                          "before adding the unit again."))
            continue
        if n["role"] == "primary":
            continue
        if n["reachable"] is False:
            out.append(_f(f"node_down:{n['id']}", CRITICAL, f"{name} is offline",
                          f"{n['ip']} does not answer pings.", 60))
            continue
        if n["loss"] is not None and n["loss"] >= 20:
            out.append(_f(f"node_flaky:{n['id']}", WARNING, f"{name} drops packets",
                          f"{n['loss']:.0f}% packet loss to {n['ip']}.", 60))
        if n["api_ok"] is False:
            out.append(_f(f"node_api:{n['id']}", WARNING, f"{name} answers pings but not its API",
                          "It may still be booting, or is hung.", 120))
            continue
        if n["default_pw"]:
            out.append(_f(f"node_unconfigured:{n['id']}", WARNING, f"{name} is at factory defaults",
                          "Its admin password is the default: it was reset and not "
                          "(successfully) added to the mesh."))
        elif n["auth_ok"] is False:
            out.append(_f(f"node_sync:{n['id']}", CRITICAL, f"{name} is out of sync",
                          "It is reachable but rejects the mesh admin password, so it is not "
                          "following the main node's settings.", 60))
        else:
            for r in n["radios"]:
                if r.get("ssid") and ssids and r["ssid"] not in ssids:
                    out.append(_f(f"ssid:{n['id']}", CRITICAL, f"{name} broadcasts a different network",
                                  f"It broadcasts '{r['ssid']}', not '{sorted(ssids)[0]}'. "
                                  "It is probably running as its own network.", 60))
                    break
            if any(r.get("enabled") is False for r in n["radios"]):
                out.append(_f(f"radio_off:{n['id']}", WARNING, f"{name} has a Wi-Fi radio turned off",
                              ", ".join(r["id"] for r in n["radios"] if r.get("enabled") is False), 60))
            if n["clients"] == 0 and len(wireless_clients) >= 5 and n["radios"]:
                out.append(_f(f"idle:{n['id']}", WARNING, f"{name} has no Wi-Fi clients",
                              f"{len(wireless_clients)} devices are on Wi-Fi, none on this node. "
                              "Its radios may not be serving, or devices never move to it.",
                              cfg.idle_node_seconds))
        bh = n.get("backhaul")
        if bh and bh.get("type") == "Wired" and bh.get("mbps") and bh["mbps"] < 1000:
            out.append(_f(f"slow_link:{n['id']}", WARNING, f"{name} wired link is only {bh['mbps']} Mbps",
                          "Gigabit expected. Check the cable and the switch port at both ends.", 0))
        if bh and bh.get("type") == "Wireless":
            out.append(_f(f"wireless_bh:{n['id']}", INFO, f"{name} uses a wireless backhaul",
                          f"Signal {bh.get('rssi')} dBm, link {bh.get('mbps')} Mbps."
                          if bh.get("rssi") else f"Link {bh.get('mbps')} Mbps."))

    versions = {n["firmware"] for n in in_mesh if n.get("firmware")}
    if len(versions) > 1:
        out.append(_f("firmware_mismatch", WARNING, "Nodes run different firmware",
                      ", ".join(f"{n['name']}: {n['firmware']}" for n in in_mesh)))

    if snap.get("steering") is False and len(in_mesh) > 1:
        out.append(_f("steering_off", INFO, "Client steering is off",
                      "With several nodes, steering nudges devices to the closest one. "
                      "Without it, devices stay where they first connected."))

    weak = [c for c in wireless_clients if c["dbm"] is not None and c["dbm"] <= cfg.weak_dbm]
    if weak:
        weak.sort(key=lambda c: c["dbm"])
        out.append(_f("weak_clients", WARNING, f"{len(weak)} device(s) with a weak signal",
                      "; ".join(f"{c['name']} {c['dbm']} dBm on {c['node']}" for c in weak[:6])
                      + ("…" if len(weak) > 6 else ""), cfg.weak_seconds))
    return out


def _fresh(client, cfg):
    return client.get("age_s") is None or client["age_s"] <= cfg.stale_client_seconds


def diff_events(prev, cur):
    """Events describing what changed between two consecutive snapshots."""
    if not prev:
        return []
    events = []
    pn = {n["id"]: n for n in prev.get("nodes", [])}
    cn = {n["id"]: n for n in cur.get("nodes", [])}
    for nid, n in cn.items():
        old = pn.get(nid)
        if old is None:
            events.append(("info", "node_added", f"{n['name']} appeared in the node list"))
            continue
        if old["in_mesh"] != n["in_mesh"]:
            events.append(("warning" if not n["in_mesh"] else "info", "mesh_membership",
                           f"{n['name']} {'joined' if n['in_mesh'] else 'left'} the mesh"))
        ob, nb = old.get("backhaul") or {}, n.get("backhaul") or {}
        if ob.get("type") and nb.get("type") and ob["type"] != nb["type"]:
            events.append(("warning", "backhaul_type",
                           f"{n['name']} backhaul changed from {ob['type']} to {nb['type']}"))
        if old.get("parent_ip") and n.get("parent_ip") and old["parent_ip"] != n["parent_ip"]:
            events.append(("warning", "parent_changed",
                           f"{n['name']} now connects via {n['parent_ip']} (was {old['parent_ip']})"))
        if ob.get("mbps") and nb.get("mbps") and ob["mbps"] != nb["mbps"]:
            events.append(("info", "backhaul_speed",
                           f"{n['name']} backhaul speed {ob['mbps']} → {nb['mbps']} Mbps"))
        if old.get("firmware") != n.get("firmware") and old.get("firmware") and n.get("firmware"):
            events.append(("info", "firmware", f"{n['name']} firmware {old['firmware']} → {n['firmware']}"))
    for nid, n in pn.items():
        if nid not in cn:
            events.append(("warning", "node_removed", f"{n['name']} disappeared from the node list"))

    pw, cw = prev.get("wan") or {}, cur.get("wan") or {}
    if pw.get("ip") and cw.get("ip") and pw["ip"] != cw["ip"]:
        events.append(("info", "wan_ip", f"WAN address changed {pw['ip']} → {cw['ip']}"))
    if prev.get("steering") is not None and cur.get("steering") is not None \
            and prev["steering"] != cur["steering"]:
        events.append(("info", "steering", f"Client steering turned {'on' if cur['steering'] else 'off'}"))

    pc = {c["mac"]: c for c in prev.get("clients", []) if c["kind"] == "wireless"}
    for c in cur.get("clients", []):
        old = pc.get(c["mac"])
        if old and c["kind"] == "wireless" and old["node_id"] and c["node_id"] \
                and old["node_id"] != c["node_id"]:
            events.append(("info", "roam", f"{c['name']} moved from {old['node']} to {c['node']}"))
    return events
