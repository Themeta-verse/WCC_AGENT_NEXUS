#!/usr/bin/env python3
"""Governance and capability-graph primitives for NEXUS GOD-TIER."""
from __future__ import annotations
import datetime as dt
import json
import re

ACTION_CLASSES = {
    "read": "SAFE", "analyze": "SAFE", "create": "SAFE", "modify": "PREPARE",
    "send": "CONFIRM", "publish": "CONFIRM", "delete": "CONFIRM", "purchase": "CONFIRM",
    "authenticate": "CONFIRM", "external_side_effect": "CONFIRM",
}


def classify_action(action: str):
    key = re.sub(r"[^a-z_]", "_", action.lower()).strip("_")
    return {"action": action, "normalized": key, "class": ACTION_CLASSES.get(key, "PREPARE"), "approval_required": ACTION_CLASSES.get(key, "PREPARE") in {"PREPARE", "CONFIRM"}}


def decay(memory: dict, today: str | None = None):
    today = today or dt.date.today().isoformat()
    expires = memory.get("EXPIRES") or memory.get("expires")
    status = memory.get("STATUS", memory.get("status", "active"))
    if status in {"superseded", "archived"}:
        return {"decision": "archive", "reason": status}
    if expires and expires != "never" and expires < today:
        return {"decision": "archive", "reason": "expired"}
    if memory.get("CLASS", memory.get("class")) in {"EPHEMERAL", "OPEN LOOP"}:
        return {"decision": "revalidate", "reason": "short-lived class"}
    if memory.get("CLASS", memory.get("class")) in {"RESOURCE", "FACT"}:
        return {"decision": "verify_freshness", "reason": "external or changing information"}
    return {"decision": "persist", "reason": "no decay signal"}


def capability_graph(capabilities: list[dict]):
    nodes = []
    edges = []
    for c in capabilities:
        cid = c["name"]
        nodes.append({"id": cid, "type": "capability", "risk": c.get("risk", "unknown"), "approval": c.get("approval", "unknown")})
        for skill in c.get("skills", []):
            nodes.append({"id": skill, "type": "skill"})
            edges.append({"from": cid, "to": skill, "relation": "implemented_by"})
        for tool in c.get("tools", []):
            nodes.append({"id": tool, "type": "tool"})
            edges.append({"from": cid, "to": tool, "relation": "uses"})
        for connector in c.get("connectors", []):
            nodes.append({"id": connector, "type": "connector"})
            edges.append({"from": cid, "to": connector, "relation": "may_use"})
    unique_nodes = {n["type"] + ":" + n["id"]: n for n in nodes}
    return {"nodes": list(unique_nodes.values()), "edges": edges}


def trust_record(source, reason, action, result, confidence, approval):
    return {"source": source, "reason": reason, "action": action, "result": result, "confidence": confidence, "approval": approval, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}


def main():
    import argparse
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("action"); a.add_argument("action")
    m = sub.add_parser("decay"); m.add_argument("memory", help="JSON object")
    c = sub.add_parser("graph"); c.add_argument("capabilities", help="JSON list")
    args = p.parse_args()
    if args.cmd == "action": out = classify_action(args.action)
    elif args.cmd == "decay": out = decay(json.loads(args.memory))
    else: out = capability_graph(json.loads(args.capabilities))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
