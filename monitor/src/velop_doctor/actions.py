"""Write operations. Every action has a preview step and a verified apply step.

Targets are always looked up in the latest snapshot, so the web API can never be
used to send commands to an arbitrary host.
"""
from __future__ import annotations

from .jnap import JnapError, Unreachable

STEERING_GET = "nodes/topologyoptimization/GetTopologyOptimizationSettings"
STEERING_SET = "nodes/topologyoptimization/SetTopologyOptimizationSettings"

ACTIONS = ("steering_on", "steering_off", "reboot_node")


class ActionError(Exception):
    pass


def _node(snap, ip):
    for n in (snap or {}).get("nodes", []):
        if n["ip"] == ip:
            return n
    raise ActionError("Unknown node: it is not in the latest snapshot.")


def preview(action, params, snap):
    if action in ("steering_on", "steering_off"):
        enable = action == "steering_on"
        if (snap or {}).get("steering") == enable:
            return {"title": f"Client steering is already {'on' if enable else 'off'}",
                    "effects": [], "risk": "none", "noop": True}
        return {
            "title": f"Turn client steering {'on' if enable else 'off'}",
            "effects": ["Changes one setting on the main node; nothing else is touched."]
            + (["Devices may briefly reconnect as they are moved to a closer node."] if enable
               else ["Devices will stay on whichever node they connected to first."]),
            "risk": "low", "noop": False,
        }
    if action == "reboot_node":
        node = _node(snap, (params or {}).get("ip"))
        effects = [f"Restarts {node['name']} ({node['ip']}); it is offline for about 2 minutes.",
                   "Devices connected to it reconnect to another node or wait for it."]
        risk = "medium"
        if node["role"] == "primary":
            effects.insert(0, "This is the MAIN node: the whole network and internet "
                              "drop until it is back.")
            risk = "high"
        effects.append("Experimental: depends on the node's firmware supporting core/Reboot.")
        return {"title": f"Reboot {node['name']}", "effects": effects, "risk": risk, "noop": False}
    raise ActionError(f"Unknown action: {action}")


def execute(action, params, snap, client, cfg):
    """`client(host)` returns an authenticated JnapClient for that host."""
    if action in ("steering_on", "steering_off"):
        enable = action == "steering_on"
        c = client(cfg.primary)
        try:
            before = c.call(STEERING_GET)
            c.call(STEERING_SET, {**before, "isClientSteeringEnabled": enable})
            after = c.call(STEERING_GET)
        except JnapError as e:
            raise ActionError(f"The main node refused the change ({e.result}).") from None
        ok = bool(after.get("isClientSteeringEnabled")) == enable
        return {"ok": ok, "message": f"Client steering is now {'on' if after.get('isClientSteeringEnabled') else 'off'}."
                if ok else "The setting did not change."}
    if action == "reboot_node":
        node = _node(snap, (params or {}).get("ip"))
        try:
            client(node["ip"]).call("core/Reboot")
        except Unreachable:
            pass  # the node drops the connection as it restarts
        except JnapError as e:
            raise ActionError(f"The node refused the reboot ({e.result}).") from None
        return {"ok": True, "message": f"Reboot command sent to {node['name']}."}
    raise ActionError(f"Unknown action: {action}")
