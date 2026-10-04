#!/usr/bin/env python3
"""Highest-level NEXUS GOD-TIER orchestration entry point."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from outcome_compiler import compile_outcome  # noqa: E402
from v4_orchestrator import orchestrate  # noqa: E402
from ecosystem_engine import task_graph, registry, compose  # noqa: E402
from beyond_engine import compile_workflow, state_estimate  # noqa: E402
from forge_engine import forge_product  # noqa: E402
from os_engine import one_command  # noqa: E402
from convergence_engine import convergence_model, compile_workflow as convergence_workflow  # noqa: E402
from canonical_runtime import compile_request as compile_canonical_request  # noqa: E402
from personal_agent import compile_agent_request  # noqa: E402
from mission_composer import MissionComposer  # noqa: E402

ROLE_MAP = {
    "research": ["researcher", "auditor", "editor"],
    "build": ["researcher", "strategist", "creative director", "engineer", "auditor"],
    "decision": ["researcher", "strategist", "auditor", "editor"],
    "automation": ["analyst", "automation designer", "engineer", "auditor"],
    "diagnose-improve": ["auditor", "strategist", "editor", "creative director"],
    "handle": ["planner", "researcher", "engineer", "auditor"],
    "general-outcome": ["planner", "auditor"],
}


def meta_orchestrate(outcome: str):
    compiled = compile_outcome(outcome)
    canonical_workflow = compile_canonical_request(outcome, {"project_id": "nexus-local"}, "PLAN_ONLY")
    personal_agent = compile_agent_request(outcome, {"project_id": "nexus-local"}, "nexus-local", "PLAN_ONLY")
    mission_composition = MissionComposer().compose(outcome, "nexus-local", "DRY_RUN")
    routed = orchestrate(outcome)
    mode = compiled["intent"]["mode"]
    roles = ROLE_MAP.get(mode, ROLE_MAP["general-outcome"])
    forge_requested = any(token in outcome.lower() for token in ("forge this", "build this end-to-end", "turn this into a product"))
    forge = forge_product(outcome) if forge_requested else None
    os_operation = one_command(outcome)
    convergence = convergence_model()
    convergence_plan = convergence_workflow(outcome, {"github": forge_requested, "deploy": False}, {})
    passes = ["understand", "explore"]
    if compiled["research_required"]: passes.append("research")
    passes += ["synthesize", "critique", "improve", "verify"]
    task_nodes = [{"id": f"step_{i}", "depends_on": [f"step_{i-1}"] if i else [], "weight": 2 if step in {"research", "critique", "verify"} else 1} for i, step in enumerate(compiled["workflows"])]
    ecosystem = {"task_graph": task_graph(task_nodes), "registry": registry(), "composition": compose(outcome), "workflow_compilation": compile_workflow(outcome), "state_estimate": state_estimate({"active_projects": ["projects/nexus-v3/project.md"], "at_risk": ["projects/nexus-v3/project.md"]})}
    graph_path = ROOT / "capability-graph.json"
    capability_graph = json.loads(graph_path.read_text()) if graph_path.exists() else {"capabilities": []}
    return {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "outcome_compilation": compiled,
        "canonical_workflow": canonical_workflow,
        "canonical_entrypoint": "MissionComposer -> capability providers -> action-ready -> outcome-intelligence -> persistent fabric",
        "consolidation": {"status":"COMPATIBILITY_FACADE","legacy_planners_are_diagnostic_only":True,"legacy_planners":["outcome_compiler","v4_orchestrator","ecosystem_engine","beyond_engine","forge_engine","os_engine","convergence_engine","canonical_runtime","personal_agent"],"actual_execution_entrypoint":"nexus research-mission or MissionComposer.execute_capability_mission","no_duplicate_external_execution":True},
        "personal_agent": personal_agent,
        "mission_composition": mission_composition,

        "forge": forge,
        "os_operation": os_operation,
        "convergence": convergence,
        "convergence_plan": convergence_plan,
        "capability_routing": routed,
        "capability_graph": capability_graph,
        "ecosystem": ecosystem,
        "temporary_roles": roles,
        "multi_pass_reasoning": passes,
        "execution_envelope": {
            "safe_now": "local retrieval, analysis, drafting, simulation, verified read missions, and artifact preparation",
            "prepare": "external changes, connector activation, schedules, publication, and multi-step side effects",
            "confirm": "financial, legal, privacy-sensitive, destructive, irreversible, account, authentication, or public actions",
            "definition_of_done": compiled["definition_of_done"],
        },
        "memory_decision": "Store only durable facts, decisions, lessons, resources, project outputs, or open loops; keep ephemeral task context temporary.",
        "follow_up": "After execution, verify outcome, record trust fields, preserve partial work, and identify the next action.",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("outcome")
    args = p.parse_args()
    print(json.dumps(meta_orchestrate(args.outcome), indent=2))


if __name__ == "__main__":
    main()
