#!/usr/bin/env python3
"""NEXUS FORGE: governed product/software creation primitives.

FORGE compiles intent into explicit planning artifacts. It does not silently invent
requirements and does not perform repository, deployment, financial, or other
consequential side effects.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import re
from copy import deepcopy
from typing import Any

REALITY = {"LIVE", "LIMITED", "EXPERIMENTAL", "SIMULATED", "UNSUPPORTED"}
DIRECTIONS = ["CONVENTIONAL", "IMPROVED", "PREMIUM", "AI_NATIVE", "UNEXPECTED", "RADICAL"]
SECURITY_CONTROLS = [
    "authentication integrity", "authorization and protected resources", "session integrity",
    "input validation", "output validation", "secret management", "privacy and data protection",
    "rate limiting where appropriate", "CSRF where relevant", "XSS and injection resistance",
    "SSRF and path traversal resistance", "safe file handling", "command execution isolation",
    "dependency vulnerability review", "privacy-preserving logging", "tenant isolation where relevant",
]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:70] or "forge-product"


def reality(label: str, evidence: list[str] | None = None, limitation: str = "") -> dict:
    label = label.upper()
    if label not in REALITY:
        raise ValueError(f"invalid reality classification: {label}")
    return {"classification": label, "evidence": evidence or [], "limitation": limitation}


def _missing(intent: str, supplied: dict[str, Any]) -> list[str]:
    required = ["target_user", "problem", "success_criteria", "constraints", "non_goals"]
    lowered = intent.lower()
    missing = []
    if not supplied.get("target_user"):
        missing.append("target_user")
    if not supplied.get("problem"):
        missing.append("problem")
    if not supplied.get("success_criteria"):
        missing.append("success_criteria")
    if not supplied.get("constraints"):
        missing.append("constraints")
    if not supplied.get("non_goals"):
        missing.append("non_goals")
    if not lowered:
        missing.append("product_intent")
    return missing


def compile_product_intent(intent: str, supplied: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict:
    """Compile only explicit facts; put unknowns in missing_information/questions."""
    supplied = deepcopy(supplied or {})
    context = deepcopy(context or {})
    missing = _missing(intent, supplied)
    objective = supplied.get("objective") or intent.strip()
    product_id = supplied.get("product_id") or _slug(intent)
    target_user = supplied.get("target_user")
    problem = supplied.get("problem")
    value = supplied.get("value_proposition")
    core = supplied.get("core_experience")
    success = supplied.get("success_criteria", [])
    constraints = supplied.get("constraints", [])
    non_goals = supplied.get("non_goals", [])
    questions = [f"What evidence establishes {field}?" for field in missing]
    return {
        "compiled_at": _now(), "product_id": product_id, "input_intent": intent,
        "status": "NEEDS_CLARIFICATION" if missing else "DEFINED",
        "objective": objective, "target_user": target_user, "user_problem": problem,
        "value_proposition": value, "core_experience": core, "success_criteria": success,
        "constraints": constraints, "non_goals": non_goals, "risks": supplied.get("risks", []),
        "research_questions": supplied.get("research_questions", questions),
        "technical_requirements": supplied.get("technical_requirements", []),
        "ux_requirements": supplied.get("ux_requirements", []),
        "security_requirements": supplied.get("security_requirements", SECURITY_CONTROLS),
        "verification_requirements": supplied.get("verification_requirements", ["verify core workflow", "verify failure states", "verify security boundaries"]),
        "definition_of_done": supplied.get("definition_of_done"),
        "missing_information": missing,
        "assumptions": [],
        "context_refs": context.get("references", []),
        "reality": reality("LIVE", ["local compilation", "explicit input preservation"], "This is a planning artifact, not a built product."),
    }


def discovery_directions(product: dict, evidence: list[dict] | None = None) -> dict:
    evidence = evidence or []
    problem = product.get("user_problem") or "the stated user problem"
    user = product.get("target_user") or "the target user, once clarified"
    templates = {
        "CONVENTIONAL": f"A focused solution for {user} to address {problem} with familiar interaction patterns.",
        "IMPROVED": f"A simpler, faster, more reliable version of the conventional solution for {user}.",
        "PREMIUM": f"A high-trust workflow with deeper controls, polish, support, and measurable outcomes for {user}.",
        "AI_NATIVE": f"A context-aware assistant that reduces effort for {user}, with deterministic validation around AI outputs.",
        "UNEXPECTED": f"A non-obvious workflow that reframes {problem} around a new user advantage.",
        "RADICAL": f"A deliberately ambitious system that changes how {user} achieves the desired outcome.",
    }
    return {"directions": [{"name": name, "concept": concept, "evidence_refs": list(range(len(evidence)))} for name, concept in templates.items()], "research_evidence": evidence, "status": "EXPLORATORY"}


def evaluate_directions(discovery: dict, product: dict) -> dict:
    scores = []
    for d in discovery.get("directions", []):
        name = d["name"]
        base = {"CONVENTIONAL": (3, 5, 2), "IMPROVED": (4, 5, 3), "PREMIUM": (4, 3, 4), "AI_NATIVE": (4, 3, 4), "UNEXPECTED": (4, 2, 5), "RADICAL": (2, 1, 5)}[name]
        scores.append({**d, "user_value": base[0], "feasibility": base[1], "defensibility": base[2], "risk": 6 - base[1], "evidence_level": "UNVALIDATED" if not discovery.get("research_evidence") else "INITIAL"})
    scores.sort(key=lambda x: (x["user_value"] + x["feasibility"] + x["defensibility"] - x["risk"]), reverse=True)
    selected = scores[0]["name"] if scores and not product.get("missing_information") else None
    return {"evaluations": scores, "selected_direction": selected, "selection_status": "PROPOSED" if selected else "BLOCKED_PENDING_CLARIFICATION", "selection_rule": "maximize justified user value, feasibility, defensibility while minimizing risk"}


def compile_ux(product: dict, direction: str | None = None) -> dict:
    return {
        "direction": direction, "user_journeys": ["onboard", "complete core outcome", "recover from failure", "return and inspect state"],
        "screens": ["landing/onboarding", "primary workspace", "detail/result", "settings/permissions", "recovery/error"],
        "states": ["empty", "loading", "partial", "success", "error", "offline/degraded", "permission denied"],
        "interactions": ["clear input", "explicit confirmation for consequential actions", "undo/retry where safe", "preserve progress"],
        "quality_checks": ["clarity", "hierarchy", "navigation", "feedback", "accessibility", "responsiveness", "performance", "consistency", "mobile and desktop behavior"],
        "design_identity": "intentional product identity to be selected from evidence and target-user context; no generic AI aesthetic assumed",
        "reality": reality("EXPERIMENTAL", ["structured UX compilation"], "Visual implementation requires a project scaffold and visual verification."),
    }


def compile_architecture(product: dict, direction: str | None = None, capabilities: dict | None = None) -> dict:
    capabilities = capabilities or {}
    ai_needed = capabilities.get("ai_required", False)
    return {
        "direction": direction, "principle": "simplest architecture capable of meeting explicit requirements",
        "frontend": capabilities.get("frontend", "choose after requirements and target surfaces are known"),
        "backend": capabilities.get("backend", "choose only if server-side behavior is required"),
        "database": capabilities.get("database", "minimize stored data; choose after entity and retention analysis"),
        "authentication": capabilities.get("authentication", "only if users or protected state require it"),
        "authorization": capabilities.get("authorization", "least privilege and resource-level checks where applicable"),
        "storage": capabilities.get("storage", "only for required artifacts/data"), "apis": capabilities.get("apis", []),
        "ai_services": capabilities.get("ai_services", []) if ai_needed else [],
        "background_jobs": capabilities.get("background_jobs", []), "caching": capabilities.get("caching", "defer until evidence supports it"),
        "observability": ["errors", "latency", "workflow completion", "AI/connector failures"],
        "deployment": capabilities.get("deployment", "prepare-only until an environment is explicitly available"),
        "testing": ["unit", "integration", "end-to-end", "security", "regression", "failure and degraded modes"],
        "scale": {"current": "unknown until usage is specified", "next": "identify expected next scale", "breaking_points": [], "migration_path": []},
        "economics": {"api_costs": "estimate before production", "storage": "estimate before production", "compute": "estimate before production", "operational_complexity": "measure", "maintenance": "track"},
    }


def compile_data_architecture(product: dict, entities: list[dict] | None = None) -> dict:
    return {"entities": entities or [], "relationships": [], "validation": ["schema validation", "business constraints"], "constraints": [], "indexes": [], "retention": "define only when data classes are known", "deletion": "support deletion where applicable", "privacy": "minimize collection and preserve purpose limitation", "status": "REQUIRES_DOMAIN_INPUT" if not entities else "DRAFT"}


def compile_ai_feature(product: dict, ai_required: bool = False, spec: dict | None = None) -> dict:
    spec = spec or {}
    if not ai_required:
        return {"ai_necessary": False, "deterministic_alternative": "prefer ordinary software", "output_validation": [], "failure_behavior": "not applicable", "reality": reality("LIMITED", ["no AI requirement supplied"], "AI was not added merely for novelty.")}
    return {"ai_necessary": True, "why": spec.get("why", "requires evidence"), "ai_does": spec.get("ai_does", []), "deterministic_software_does": spec.get("deterministic_software_does", []), "input_data": spec.get("input_data", []), "output_schema": spec.get("output_schema", {}), "output_validation": ["schema validation", "constraint validation", "source attribution where applicable"], "failure_behavior": ["bounded retry", "timeout", "fallback", "human confirmation before consequential action"], "unavailable_behavior": "degraded deterministic path or explicit blocked state", "reality": reality("EXPERIMENTAL", ["AI feature contract"], "AI quality requires real evaluation and runtime availability tests.")}


def product_task_graph(product: dict, architecture: dict, include_ai: bool = False) -> dict:
    names = ["foundation", "core-domain", "auth-and-authorization", "data", "core-experience", "ai", "integrations", "polish", "testing", "hardening"]
    if not include_ai: names.remove("ai")
    nodes = []
    for i, name in enumerate(names):
        deps = [names[i-1]] if i else []
        nodes.append({"id": name, "depends_on": deps, "epic": name, "tasks": [f"define {name}", f"implement {name}", f"verify {name}"], "tests": [f"{name} unit/integration checks"], "status": "PLANNED"})
    return {"product_id": product["product_id"], "nodes": nodes, "critical_path": names, "parallel_work": [["auth-and-authorization", "data"]] if len(names) > 3 else [], "blockers": product.get("missing_information", []), "milestones": ["definition", "validated foundation", "core workflow", "hardening", "release assessment"]}


def forge_product(intent: str, supplied: dict | None = None, context: dict | None = None, evidence: list[dict] | None = None, capabilities: dict | None = None) -> dict:
    product = compile_product_intent(intent, supplied, context)
    discovery = discovery_directions(product, evidence)
    selection = evaluate_directions(discovery, product)
    ux = compile_ux(product, selection["selected_direction"])
    architecture = compile_architecture(product, selection["selected_direction"], capabilities)
    data = compile_data_architecture(product, (supplied or {}).get("entities", []))
    ai = compile_ai_feature(product, bool((capabilities or {}).get("ai_required")), (supplied or {}).get("ai", {}))
    graph = product_task_graph(product, architecture, ai["ai_necessary"])
    return {"forge_version": "0.1", "compiled_at": _now(), "product": product, "discovery": discovery, "direction_evaluation": selection, "ux": ux, "architecture": architecture, "data": data, "ai": ai, "task_graph": graph, "execution_boundary": {"safe_now": ["compile", "research", "design", "simulate", "prepare artifacts"], "prepare": ["repository changes", "deployment package", "connector activation"], "confirm": ["commit", "push", "publish", "deploy", "destructive or consequential actions"], "writes_performed": False}, "fingerprint": hashlib.sha256(json.dumps({"product": product, "selection": selection}, sort_keys=True).encode()).hexdigest(), "reality": reality("EXPERIMENTAL", ["local FORGE compilation"], "No product has been built or deployed by compilation alone.")}


def main():
    parser = argparse.ArgumentParser(description="Compile a governed NEXUS FORGE product blueprint")
    parser.add_argument("intent")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    print(json.dumps(forge_product(args.intent), indent=2))


if __name__ == "__main__":
    main()


# --- Lifecycle and governance extensions ---

def product_health(metrics: dict | None = None) -> dict:
    metrics = metrics or {}
    dimensions = ["functionality", "reliability", "security", "performance", "ux", "ai_quality", "data_integrity", "operability"]
    return {"dimensions": {d: metrics.get(d, {"status": "UNKNOWN", "evidence": []}) for d in dimensions}, "principle": "health is multidimensional; no arbitrary single score", "reality": reality("LIVE", ["structured health model"], "Unknown dimensions require evidence." )}


def product_self_audit(state: dict | None = None) -> dict:
    state = state or {}
    areas = ["ux", "architecture", "security", "performance", "ai", "data", "tests", "github", "documentation", "technical_debt"]
    findings = []
    for area in areas:
        value = state.get(area)
        if value in (None, {}, [], ""):
            findings.append({"area": area, "severity": "UNKNOWN", "finding": "evidence not supplied", "action": f"inspect {area}"})
    return {"audited_areas": areas, "findings": findings, "prioritization": "risk × user impact × uncertainty × reversibility", "writes_performed": False}


def product_red_team() -> list[dict]:
    personas = ["malicious user", "confused user", "new user", "expert user", "mobile user", "slow-network user", "unauthorized user", "broken-tool user"]
    return [{"persona": p, "attack": "attempt unsafe, confusing, stale, invalid, or degraded workflow", "expected_control": "bounded error state, preserved data, verification, and governance"} for p in personas]


def forge_security_red_team() -> list[dict]:
    attacks = ["unauthorized repository write", "secret exposure", "authentication bypass", "authorization bypass", "malicious input", "unsafe AI output", "broken validation", "database corruption", "duplicate execution", "failed deployment", "false success", "stale state", "dependency vulnerability", "incomplete rollback"]
    return [{"attack": a, "required_result": "blocked, safely handled, or explicitly reported with evidence"} for a in attacks]


def deployment_plan(product: dict, environment: dict | None = None) -> dict:
    environment = environment or {}
    supported = bool(environment.get("supported"))
    return {"classification": "PREPARE" if supported else "UNSUPPORTED", "build": "prepare reproducible build", "configuration": "document environment variables without values", "migration": "safeguarded and reversible", "smoke_tests": ["availability", "authentication", "core workflow", "API health", "database connectivity", "AI/connector behavior"], "rollback": "document exact rollback", "secrets_exposed": False, "next_step": "execute only when environment and authorization are present"}


def github_genesis_plan(product: dict, repository: str | None = None, authorized: bool = False) -> dict:
    return {"repository": repository, "branch_structure": ["stable", "development", "experimental"], "files": ["README", "architecture", "environment", "development", "test strategy", "security", "roadmap"], "status": "AUTHORIZED_PLAN" if authorized else "PREPARE_ONLY", "writes_performed": False}


def reawaken_project(previous: dict | None, current: dict | None) -> dict:
    previous, current = previous or {}, current or {}
    keys = sorted(set(previous) | set(current))
    changes = [{"field": k, "before": previous.get(k), "after": current.get(k)} for k in keys if previous.get(k) != current.get(k)]
    return {"what_exists": list(current.keys()), "what_changed": changes, "what_broke": current.get("breakages", []), "what_is_outdated": current.get("outdated", []), "what_matters": current.get("priorities", []), "next": current.get("next", "inspect and re-verify")}


def classify_opportunity(item: dict) -> dict:
    scores = {k: float(item.get(k, 0)) for k in ("problem_strength", "user_frequency", "differentiation", "feasibility", "ai_advantage")}
    total = sum(scores.values())
    category = "PRODUCT" if total >= 20 else "FEATURE" if total >= 15 else "AUTOMATION" if total >= 10 else "SKILL" if total >= 5 else "NOT_WORTH_BUILDING"
    return {"category": category, "scores": scores, "total": total, "evidence": item.get("evidence", []), "status": "PROPOSED"}


def graveyard_record(idea: str, why_rejected: str, failed_assumption: str, viability_trigger: str) -> dict:
    return {"idea": idea, "why_rejected": why_rejected, "failed_assumption": failed_assumption, "when_viable": viability_trigger, "status": "REJECTED_MEMORY"}


def forge_command(command: str, supplied: dict | None = None, context: dict | None = None) -> dict:
    text = command.strip()
    valid = text.lower() in {"forge this", "build this end-to-end", "turn this into a product"} or text.lower().startswith("forge this:")
    if not valid:
        return {"status": "NOT_FORGE_COMMAND", "command": command}
    intent = text.split(":", 1)[1].strip() if ":" in text else text
    return {"status": "COMPILED", "stages": ["understand", "research", "define product", "design experience", "architect", "plan", "build", "test", "security test", "review", "github", "deploy where supported", "verify", "document", "next iteration"], "blueprint": forge_product(intent, supplied, context), "requires_authorization": ["repository write", "deployment", "publication", "destructive or consequential actions"]}
