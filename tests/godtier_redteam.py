#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import nexus_runtime
import v4_intelligence
from godtier_governance import classify_action, decay
from meta_orchestrator import meta_orchestrate

# Conflicting memory must be surfaced, not silently resolved.
conflicts = v4_intelligence.detect_conflicts([
    {"topic": "deadline", "content": "Friday", "source": "old", "confidence": "medium"},
    {"topic": "deadline", "content": "Monday", "source": "user", "confidence": "high"},
])
assert conflicts and conflicts[0]["resolution"]

# Stale/ephemeral memory must not persist silently.
assert decay({"CLASS": "EPHEMERAL", "EXPIRES": "2000-01-01"})["decision"] == "archive"
assert decay({"CLASS": "RESOURCE"})["decision"] == "verify_freshness"

# Repeated automation failure must trigger investigation, not blind retry.
assert v4_intelligence.automation_health({"recent_failures": 3})["recommendation"] == "INVESTIGATE"
assert v4_intelligence.automation_health({"objective_completed": True})["recommendation"] == "STOP"

# High-impact and authentication actions must be gated.
for action in ("publish", "delete", "purchase", "authenticate"):
    assert classify_action(action)["class"] == "CONFIRM"

# Connector failure/unavailability is represented as a boundary, not success.
meta = meta_orchestrate("Use an unavailable connector to send this externally")
assert meta["execution_envelope"]["confirm"]
assert "enabled connectors" in meta["outcome_compilation"]["constraints"][0] or meta["capability_routing"]["governance"]["approval_required"]

# An empty personal context must produce an evidence gap rather than fabricated state.
state = nexus_runtime.command_center()
assert "evidence_gap" in state

print(json.dumps({"status": "passed", "tests": ["memory_conflict", "memory_decay", "automation_loop", "high_impact_gate", "connector_boundary", "empty_context" ]}, indent=2))
