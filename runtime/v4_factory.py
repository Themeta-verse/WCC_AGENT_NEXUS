#!/usr/bin/env python3
"""V4 meta-capabilities: composition, skill candidates, simulation, and governance checks."""
from __future__ import annotations
import argparse
import json
import re
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import v4_intelligence  # noqa: E402


def compose(request: str):
    text = request.lower()
    capabilities = []
    if any(x in text for x in ("research", "true", "evidence")): capabilities += ["retrieval", "research", "critique"]
    if any(x in text for x in ("decision", "choose", "compare", "recommend")): capabilities += ["context", "decision", "counterfactual", "second_order", "critique"]
    if any(x in text for x in ("idea", "creative", "product", "build")): capabilities += ["research", "creative", "product", "architecture", "critique"]
    if any(x in text for x in ("automate", "repetitive", "workflow")): capabilities += ["analysis", "automation_design", "simulation", "verification"]
    if not capabilities: capabilities = ["intent", "context", "dynamic_workflow", "simulation", "critique"]
    deduped = list(dict.fromkeys(capabilities))
    return {"request": request, "capabilities": deduped, "temporary_workflow": True, "preserve_if_reusable": True}


def skill_candidate(events: list[dict]):
    signatures = Counter(e.get("signature") for e in events if e.get("signature"))
    candidates = [{"signature": sig, "count": count, "recommendation": "create candidate Skill"} for sig, count in signatures.items() if count >= 3]
    return {"candidates": candidates, "rule": "do not create for one-off tasks; validate recurrence and leverage"}


def simulate(payload: dict):
    steps = payload.get("steps", [])
    return v4_intelligence.simulate(steps)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("compose"); x.add_argument("request")
    x = sub.add_parser("skill-candidate"); x.add_argument("events", help="JSON list")
    x = sub.add_parser("simulate"); x.add_argument("payload", help="JSON object")
    args = p.parse_args()
    if args.cmd == "compose": out = compose(args.request)
    elif args.cmd == "skill-candidate": out = skill_candidate(json.loads(args.events))
    else: out = simulate(json.loads(args.payload))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
