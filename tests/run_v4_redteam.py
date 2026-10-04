#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import v4_orchestrator
import v4_intelligence
import v4_factory

cases = [
    ("ambiguous", "Handle this", "PREPARE"),
    ("high-impact", "Send this message and publish it", "CONFIRM"),
    ("impossible", "Use a connector that is not enabled and guarantee the result", "SAFE"),
    ("automation", "Automate this repetitive workflow", "PREPARE"),
    ("decision", "Compare two options and recommend one", "SAFE"),
]
for name, request, expected in cases:
    out = v4_orchestrator.orchestrate(request)
    assert out["governance"]["autonomy_class"] == expected, (name, out["governance"])
    assert "context" in out and "next_action" in out

conflicts = v4_intelligence.detect_conflicts([
    {"topic": "priority", "content": "A", "source": "user", "confidence": "high"},
    {"topic": "priority", "content": "B", "source": "old file", "confidence": "medium"},
])
assert conflicts and conflicts[0]["resolution"]

sim = v4_intelligence.simulate([
    {"name": "read", "requires_confirmation": False},
    {"name": "delete", "requires_confirmation": True},
])
assert sim["recommendation"] == "modify"

candidate = v4_factory.skill_candidate([{"signature": "same"}] * 3)
assert candidate["candidates"]

print(json.dumps({"status": "passed", "cases": len(cases), "conflict_detection": True, "simulation_guard": True, "skill_factory_guard": True}, indent=2))
