#!/usr/bin/env python3
"""Outcome compiler for NEXUS GOD-TIER.

It produces an explicit, inspectable objective model without claiming knowledge that
was not supplied. The result is a planning artifact, not permission to perform side effects.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re


def infer_intent(text: str) -> dict:
    t = text.lower().strip()
    if any(x in t for x in ("this is terrible", "make it better", "make this better", "make it memorable", "improve", "fix")):
        return {"mode": "diagnose-improve", "confidence": "medium", "ambiguity": "ask if the target artifact is unclear"}
    if any(x in t for x in ("handle this", "handle it", "take care of")):
        return {"mode": "handle", "confidence": "medium", "ambiguity": "identify the referent from current context; ask if multiple plausible referents exist"}
    if any(x in t for x in ("research", "what is true", "investigate")):
        return {"mode": "research", "confidence": "high", "ambiguity": "define scope and freshness requirements if material"}
    if any(x in t for x in ("creative", "idea", "concept", "memorable", "original", "combine", "build", "launch", "create", "develop")):
        return {"mode": "build", "confidence": "medium", "ambiguity": "define target user and definition of done if product scope is unclear"}
    if any(x in t for x in ("continue", "where we left off", "resume")):
        return {"mode": "continue", "confidence": "medium", "ambiguity": "recover the most relevant active project and ask if multiple candidates remain"}
    if any(x in t for x in ("find something valuable", "find opportunities", "valuable", "opportunity")):
        return {"mode": "opportunity", "confidence": "low", "ambiguity": "use available context and only surface high-value opportunities"}
    if any(x in t for x in ("what should i stop", "stop doing", "low-value", "busywork", "simplify")):
        return {"mode": "stop", "confidence": "medium", "ambiguity": "recommend rather than autonomously stop personal commitments"}
    if any(x in t for x in ("choose", "compare", "recommend", "decision")):
        return {"mode": "decision", "confidence": "high", "ambiguity": "identify objective and constraints before recommending"}
    if any(x in t for x in ("automate", "repetitive", "workflow")):
        return {"mode": "automation", "confidence": "high", "ambiguity": "define trigger, destination, cadence, and approval boundary"}
    return {"mode": "general-outcome", "confidence": "low", "ambiguity": "ask only for information that materially changes the result"}


def compile_outcome(outcome: str) -> dict:
    intent = infer_intent(outcome)
    t = outcome.strip()
    high_impact = any(x in t.lower() for x in ("send", "publish", "delete", "purchase", "pay", "legal", "financial", "credential", "account"))
    mode = intent["mode"]
    research = mode in {"research", "build", "decision", "diagnose-improve"}
    if mode == "automation":
        workflow = ["identify trigger", "model inputs and condition", "define action and output", "simulate", "verify", "activate only with approval"]
    elif mode == "build":
        workflow = ["problem", "user", "value", "research", "concept", "specification", "architecture", "prototype", "test", "critic", "iterate", "verify"]
    elif mode == "decision":
        workflow = ["objective", "options", "evidence", "constraints", "trade-offs", "risks", "counterfactuals", "second-order effects", "recommendation", "next action"]
    else:
        workflow = ["understand", "retrieve relevant context", "research if needed", "plan", "execute safe portion", "critic", "verify", "follow up"]
    return {
        "compiled_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "input_outcome": outcome,
        "intent": intent,
        "objective": "Make the requested outcome real, or identify the smallest safe path toward it.",
        "success_criteria": ["A verified result exists", "or a concrete blocked state explains what is missing", "and the next action is explicit"],
        "constraints": ["Do not fabricate context, access, evidence, or completion", "Preserve privacy and approval boundaries"],
        "context_required": ["relevant current state", "related projects, decisions, resources, deadlines, and open loops"],
        "resources": ["NEXUS workspace", "validated specialist skills", "enabled connectors only", "available research and artifact capabilities"],
        "research_required": research,
        "workflows": workflow,
        "actions": ["retrieve context", "compose capabilities", "execute safe actions", "prepare consequential actions"],
        "approvals": ["explicit confirmation before external side effects, publication, deletion, purchase, account, legal, financial, or privacy-sensitive actions"] if high_impact else ["none for low-risk local analysis; ask if scope changes"],
        "verification": ["check the actual result", "check side effects", "record source, reason, action, result, confidence, approval, and timestamp"],
        "definition_of_done": "Outcome achieved and verified, or explicitly blocked with preserved partial work and a next action.",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("outcome")
    args = p.parse_args()
    print(json.dumps(compile_outcome(args.outcome), indent=2))


if __name__ == "__main__":
    main()
