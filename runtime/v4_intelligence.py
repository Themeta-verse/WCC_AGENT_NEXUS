#!/usr/bin/env python3
"""Small deterministic V4 intelligence primitives with explicit uncertainty."""
from __future__ import annotations
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "context"
LOGS = ROOT / "logs"

MEMORY_CLASSES = {"EPHEMERAL", "PROJECT", "PERSISTENT", "DECISION", "LESSON", "PREFERENCE", "RESOURCE", "HYPOTHESIS", "OPEN LOOP"}


def memory_relevance(query: str, memories: list[dict], limit: int = 8):
    q = set(re.findall(r"[a-z0-9]+", query.lower()))
    ranked = []
    for m in memories:
        text = (m.get("content", "") + " " + m.get("why", "") + " " + m.get("when", "")).lower()
        score = len(q & set(re.findall(r"[a-z0-9]+", text)))
        if score:
            ranked.append((score, m))
    ranked.sort(key=lambda x: -x[0])
    return [{**m, "relevance_score": score} for score, m in ranked[:limit]]


def detect_conflicts(memories: list[dict]):
    conflicts = []
    for i, a in enumerate(memories):
        for b in memories[i + 1:]:
            if a.get("topic") and a.get("topic") == b.get("topic") and a.get("content") != b.get("content"):
                conflicts.append({"topic": a["topic"], "a": a, "b": b, "resolution": "inspect source, recency, confidence; ask if material"})
    return conflicts


def temporal_plan(deadline: str, remaining_work: float | None, available_hours: float | None, dependencies: list[str] | None = None):
    result = {"deadline": deadline, "dependencies": dependencies or [], "confidence": "low"}
    try:
        due = dt.datetime.fromisoformat(deadline).date()
        days = max((due - dt.date.today()).days, 0)
        result["days_remaining"] = days
        if remaining_work is not None and available_hours is not None:
            result["work_to_time_ratio"] = round(float(remaining_work) / max(float(available_hours), 0.1), 2)
            result["confidence"] = "medium"
            result["risk"] = "high" if remaining_work > available_hours else "manageable"
    except ValueError:
        result["error"] = "deadline must be ISO date or datetime"
    return result


def counterfactuals(options: list[str]):
    return {"do_nothing": "Objective remains unchanged; identify cost of delay.", "options": [{"option": x, "what_could_go_wrong": "Identify downside and cheapest test before commitment."} for x in options], "test": "Choose the cheapest reversible experiment that distinguishes the leading options."}


def second_order(decision: str):
    return {"decision": decision, "questions": ["What happens next?", "What incentives change?", "What dependencies are created?", "What new risks or opportunities appear?"], "status": "requires task-specific evidence"}


def automation_health(status: dict):
    failures = int(status.get("recent_failures", 0))
    completed = bool(status.get("objective_completed", False))
    if completed:
        return {"recommendation": "STOP", "reason": "objective completed"}
    if failures >= 3:
        return {"recommendation": "INVESTIGATE", "reason": "repeated failures"}
    if status.get("irrelevant"):
        return {"recommendation": "FLAG", "reason": "workflow may be obsolete"}
    return {"recommendation": "CONTINUE", "reason": "no stop signal supplied"}


def simulate(steps: list[dict]):
    state = {"status": "simulated", "risks": [], "completed_steps": []}
    for step in steps:
        if step.get("requires_confirmation"):
            state["risks"].append({"step": step.get("name", "unnamed"), "reason": "confirmation required"})
        else:
            state["completed_steps"].append(step.get("name", "unnamed"))
    state["recommendation"] = "modify" if state["risks"] else "execute"
    return state


def write_jsonl(name: str, record: dict):
    LOGS.mkdir(exist_ok=True)
    with (LOGS / name).open("a") as f:
        f.write(json.dumps(record) + "\n")
