#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from outcome_compiler import compile_outcome
from meta_orchestrator import meta_orchestrate
from godtier_governance import classify_action, decay, capability_graph
import v4_intelligence
import v4_factory

cases = [
    ("ambiguous objective", "I have a problem"),
    ("multi-step project", "Build a product that helps students prepare for placements"),
    ("difficult research", "Research what is true about this complicated topic"),
    ("strategic decision", "Compare these options and find the best one"),
    ("creative challenge", "Make this better and more memorable"),
    ("automation opportunity", "This keeps happening; automate it"),
    ("missing context", "What should I do next?"),
    ("handle this", "Handle this"),
    ("unexpected request", "Find a better way to combine my work and learning"),
]
results = []
for name, request in cases:
    compiled = compile_outcome(request)
    meta = meta_orchestrate(request)
    assert compiled["definition_of_done"]
    assert meta["temporary_roles"]
    assert meta["multi_pass_reasoning"]
    results.append({"case": name, "mode": compiled["intent"]["mode"], "roles": meta["temporary_roles"]})

assert classify_action("publish")["class"] == "CONFIRM"
assert classify_action("read")["class"] == "SAFE"
assert decay({"CLASS": "EPHEMERAL", "EXPIRES": "2000-01-01"})["decision"] == "archive"
assert decay({"CLASS": "RESOURCE"})["decision"] == "verify_freshness"
assert v4_intelligence.detect_conflicts([
    {"topic": "goal", "content": "A"}, {"topic": "goal", "content": "B"}
])
assert v4_intelligence.simulate([{"name": "safe"}, {"name": "publish", "requires_confirmation": True}])["recommendation"] == "modify"
assert v4_factory.skill_candidate([{"signature": "repeat"}] * 3)["candidates"]
graph = capability_graph([{"name": "research", "skills": ["nexus-deep-research"], "tools": ["web"], "connectors": []}])
assert graph["edges"]
print(json.dumps({"status": "passed", "cases": results, "dimensions": ["correctness", "usefulness", "context", "reasoning", "creativity", "execution", "autonomy", "reliability", "verification", "explainability"]}, indent=2))
