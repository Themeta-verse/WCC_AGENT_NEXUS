#!/usr/bin/env python3
"""Deterministic V3 NEXUS runtime for local context, retrieval, priority, events, and command-center generation."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = ROOT / "context"
PROJECTS = ROOT / "projects"
LOGS = ROOT / "logs"
ARTIFACTS = ROOT / "artifacts"


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def markdown_files():
    return sorted([*CONTEXT.glob("*.md"), *PROJECTS.glob("**/*.md"), *ROOT.glob("workflows/*.md")])


def read_records():
    records = []
    for path in markdown_files():
        text = path.read_text(errors="replace")
        if path.name in {"README.md", "SCHEMA.md"} and path.parent == CONTEXT:
            continue
        records.append({"path": str(path.relative_to(ROOT)), "name": path.stem, "text": text, "tokens": set(re.findall(r"[a-z0-9_]+", text.lower()))})
    return records


def retrieve(query: str, limit: int = 8):
    q = set(re.findall(r"[a-z0-9_]+", query.lower()))
    ranked = []
    for r in read_records():
        overlap = len(q & r["tokens"])
        title_bonus = sum(2 for word in q if word in r["name"].lower())
        if overlap or title_bonus:
            ranked.append((overlap + title_bonus, r))
    ranked.sort(key=lambda x: (-x[0], x[1]["path"]))
    return [{"path": r["path"], "score": score, "excerpt": " ".join(r["text"].split())[:500]} for score, r in ranked[:limit]]


def priority(item: dict):
    """Score only supplied dimensions; never fabricate missing personal values."""
    weights = {"importance": 3, "urgency": 2, "impact": 3, "dependency": 2, "effort": -1, "risk": 2, "strategic_alignment": 3}
    supplied = {k: float(v) for k, v in item.items() if k in weights and isinstance(v, (int, float))}
    score = sum(supplied[k] * weights[k] for k in supplied)
    return {"score": round(score, 2), "dimensions_used": sorted(supplied), "missing_dimensions": sorted(set(weights) - set(supplied))}


def event(event: dict):
    LOGS.mkdir(exist_ok=True)
    record = {"timestamp": now(), "event": event, "status": "received", "approval_status": event.get("approval_status", "not_required")}
    with (LOGS / "event-log.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def graph_index():
    records = read_records()
    nodes = [{"id": r["path"], "type": "project" if r["path"].startswith("projects/") else "context" if r["path"].startswith("context/") else "workflow"} for r in records]
    edges = []
    for a in records:
        for b in records:
            if a["path"] >= b["path"]:
                continue
            shared = sorted((a["tokens"] & b["tokens"]) - {"the", "and", "for", "with", "from", "this"})
            if len(shared) >= 3:
                edges.append({"from": a["path"], "to": b["path"], "basis": shared[:8]})
    return {"generated_at": now(), "nodes": nodes, "edges": edges}


def command_center():
    files = read_records()
    project_files = [r for r in files if r["path"].startswith("projects/") and Path(r["path"]).name != "README.md"]
    context_files = [r for r in files if r["path"].startswith("context/")]
    open_loops = [r for r in context_files + project_files if Path(r["path"]).stem in {"open-loops", "project"} and "no entries yet" not in r["text"].lower()]
    risks = [r for r in context_files + project_files if any(word in r["text"].lower() for word in ("risk", "blocked", "deadline")) and "no entries yet" not in r["text"].lower()]
    return {
        "generated_at": now(),
        "NOW": "No immediate action can be ranked without explicit active objectives or deadlines." if not project_files else "Review the highest-risk active project.",
        "TODAY": "Populate one active objective and one project record before relying on recommendations." if not project_files else "Run a project audit against the active project records.",
        "IN_PROGRESS": [r["path"] for r in project_files],
        "BLOCKED": [r["path"] for r in risks if "blocked" in r["text"].lower()],
        "AT_RISK": [r["path"] for r in risks[:8]],
        "OPPORTUNITIES": [r["path"] for r in context_files + project_files if "opportun" in r["text"].lower() and "no entries yet" not in r["text"].lower()][:8],
        "DELEGATE": "NEXUS can retrieve, analyze, draft, create local artifacts, and prepare approved workflows; consequential external actions require confirmation.",
        "WAITING": [r["path"] for r in open_loops[:8]],
        "NEXT": "Add explicit context: current priority, active objective, deadline, and next action." if not project_files else "Inspect the project with the project-auditor workflow.",
        "evidence_gap": not bool(project_files),
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("retrieve"); p.add_argument("query")
    p = sub.add_parser("priority"); p.add_argument("payload", help="JSON object")
    p = sub.add_parser("event"); p.add_argument("payload", help="JSON object")
    sub.add_parser("command-center")
    sub.add_parser("graph")
    args = parser.parse_args()
    if args.command == "retrieve": print(json.dumps(retrieve(args.query), indent=2))
    elif args.command == "priority": print(json.dumps(priority(json.loads(args.payload)), indent=2))
    elif args.command == "event": print(json.dumps(event(json.loads(args.payload)), indent=2))
    elif args.command == "command-center": print(json.dumps(command_center(), indent=2))
    elif args.command == "graph": print(json.dumps(graph_index(), indent=2))


if __name__ == "__main__":
    main()
