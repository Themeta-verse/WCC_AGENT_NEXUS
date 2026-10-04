#!/usr/bin/env python3
"""NEXUS V4 unified orchestration layer.

This deterministic router does not replace judgment; it makes the workflow explicit,
selects relevant local capabilities, and exposes evidence and approval boundaries.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))
import nexus_runtime  # noqa: E402
import v4_intelligence  # noqa: E402

ROUTES = [
    ("research", ["research", "what is true", "investigate", "evidence", "study"], ["nexus-deep-research", "nexus-context-retrieval", "nexus-critic"]),
    ("product", ["product", "app", "website", "build", "prototype", "idea"], ["nexus-deep-research", "nexus-creative-lab", "nexus-product-builder", "nexus-technical-architecture", "nexus-critic"]),
    ("decision", ["decide", "decision", "compare", "choose", "recommend", "trade-off", "what do you think", "opinion"], ["nexus-context-retrieval", "nexus-decision-engine", "nexus-critic"]),
    ("project", ["project", "stalled", "blocked", "progress", "review everything", "what changed"], ["nexus-context-retrieval", "nexus-project-autopilot", "nexus-project-auditor", "nexus-critic"]),
    ("automation", ["automate", "repetitive", "automation", "every day", "every week", "workflow"], ["nexus-context-retrieval", "nexus-automation-factory", "nexus-critic"]),
    ("creative", ["creative", "concept", "campaign", "story", "make it better", "make this better", "memorable", "original", "unforgettable", "combine domains", "combine my work", "better way"], ["nexus-creative-lab", "nexus-critic"]),
    ("priority", ["what should i do", "what's important", "what matters", "next", "focus", "what am i missing", "what's blocking me"], ["nexus-context-retrieval", "nexus-project-autopilot", "nexus-decision-engine"]),
    ("continue", ["continue", "where we left off", "resume"], ["nexus-context-retrieval", "nexus-project-autopilot", "nexus-project-auditor"]),
    ("opportunity", ["find something valuable", "find opportunities", "valuable", "opportunity"], ["nexus-context-retrieval", "nexus-opportunity-engine", "nexus-deep-research", "nexus-critic"]),
    ("stop", ["what should i stop", "stop doing", "low-value", "busywork", "simplify"], ["nexus-context-retrieval", "nexus-project-auditor", "nexus-decision-engine"]),
]


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def classify(request: str):
    text = clean(request)
    hits = []
    for name, terms, skills in ROUTES:
        score = sum(1 for term in terms if term in text)
        if score:
            hits.append((score, name, skills))
    hits.sort(reverse=True)
    if not hits:
        return {"intent": "novel", "confidence": "low", "skills": ["nexus-context-retrieval", "nexus-critic"], "dynamic_workflow": True}
    primary = hits[0]
    skills = []
    for _, _, group in hits[:3]:
        for skill in group:
            if skill not in skills:
                skills.append(skill)
    return {"intent": primary[1], "confidence": "high" if primary[0] >= 2 else "medium", "skills": skills, "matched_routes": [h[1] for h in hits[:3]], "dynamic_workflow": False}


def autonomy(request: str):
    text = clean(request)
    if any(x in text for x in ("send", "publish", "delete", "purchase", "pay", "post", "authenticate", "change account")):
        return "CONFIRM"
    if any(x in text for x in ("handle", "execute", "do this", "automate")):
        return "PREPARE"
    return "SAFE"


def relevant_context(request: str):
    result = nexus_runtime.retrieve(request, limit=6)
    return [{"path": r["path"], "score": r["score"], "excerpt": r["excerpt"]} for r in result]


def state():
    cc = nexus_runtime.command_center()
    return {"now": cc["NOW"], "today": cc["TODAY"], "in_progress": cc["IN_PROGRESS"], "blocked": cc["BLOCKED"], "at_risk": cc["AT_RISK"], "waiting": cc["WAITING"], "next": cc["NEXT"], "evidence_gap": cc["evidence_gap"]}


def orchestrate(request: str):
    route = classify(request)
    context = relevant_context(request)
    cognitive_checks = {}
    if route["intent"] == "decision":
        cognitive_checks["counterfactuals"] = v4_intelligence.counterfactuals(["leading option", "alternative option"])
        cognitive_checks["second_order"] = v4_intelligence.second_order(request)
    if route["intent"] == "automation":
        cognitive_checks["automation_health"] = v4_intelligence.automation_health({"recent_failures": 0, "objective_completed": False})
    if route["intent"] == "novel":
        cognitive_checks["dynamic_workflow"] = {"steps": ["clarify objective", "retrieve relevant context", "compose capabilities", "simulate", "execute safe portion", "criticize", "preserve if reusable"]}
    result = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "request": request,
        "intent": route,
        "objective_model": {
            "desired_outcome": "Determine from the request and confirm if materially ambiguous.",
            "definition_of_done": "A verified result or a clear prepared plan with evidence, approval state, and next action.",
            "constraints": ["Do not invent personal context or unavailable access.", "Preserve approval boundaries."],
        },
        "context": context,
        "personal_state": state(),
        "capability_plan": route["skills"],
        "governance": {"autonomy_class": autonomy(request), "approval_required": autonomy(request) in {"PREPARE", "CONFIRM"}},
        "cognitive_checks": cognitive_checks,
        "workflow": ["intent recognition", "context retrieval", "objective modeling", "capability composition", "execution or preparation", "quality control", "result verification", "memory decision", "follow-up"],
        "memory_decision": "Store selectively only if the result is a durable fact, decision, lesson, resource, or project output.",
        "next_action": "Run the selected specialist chain; ask only for missing information that would materially change the result.",
    }
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("request")
    args = p.parse_args()
    print(json.dumps(orchestrate(args.request), indent=2))


if __name__ == "__main__":
    main()
