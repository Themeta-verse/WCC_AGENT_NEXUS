#!/usr/bin/env python3
"""NEXUS Ω² bounded intelligence layer.

This module plans and simulates locally. It does not invoke connectors, persist
personal data, activate automations, create approvals, or perform side effects.
All missing information and unavailable capabilities remain explicit.
"""
from __future__ import annotations
import json,re
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone
from typing import Any
try:
    from capability_registry import CapabilityRegistry, discover_actual_registry
    from canonical_core import governance_for, core_id, utc_now
except ImportError:
    from .capability_registry import CapabilityRegistry, discover_actual_registry
    from .canonical_core import governance_for, core_id, utc_now

REALITY={"REAL","SIMULATED","UNAVAILABLE","UNKNOWN"}
ACTION_STATE={"PLANNED","PREPARED","EXECUTED","SUCCEEDED","VERIFIED","PERSISTED"}
KNOWLEDGE={"KNOWN","VERIFIED","ASSUMED","UNKNOWN","CONFLICTING","STALE"}
MODES={"REAL","SIMULATION","DRY_RUN","PLAN_ONLY"}

@dataclass
class IntentModel:
    goal:str
    desired_outcome:str
    mode:str
    scope:str
    constraints:list[str]=field(default_factory=list)
    resources:list[str]=field(default_factory=list)
    deadline:str|None=None
    preferences:list[str]=field(default_factory=list)
    success_criteria:list[str]=field(default_factory=list)
    ambiguities:list[str]=field(default_factory=list)
    motivation:str|None=None
    urgency:str="unknown"
    risk_tolerance:str="unknown"
    reality:str="REAL"

@dataclass
class KnowledgeGap:
    id:str
    question:str
    importance:str
    status:str
    cheapest_resolution:str
    impact_if_unresolved:str
    source_scope:str
    reality:str="UNKNOWN"

@dataclass
class ProjectState:
    project_id:str
    objective:str
    status:str
    milestones:list[str]=field(default_factory=list)
    tasks:list[dict]=field(default_factory=list)
    dependencies:list[str]=field(default_factory=list)
    artifacts:list[str]=field(default_factory=list)
    decisions:list[str]=field(default_factory=list)
    risks:list[str]=field(default_factory=list)
    blockers:list[str]=field(default_factory=list)
    capabilities:list[str]=field(default_factory=list)
    verification_status:str="UNKNOWN"
    last_known_state:str="UNKNOWN"
    next_action:str=""
    scope:str="project"
    reality:str="REAL"

@dataclass
class Decision:
    id:str
    question:str
    options:list[dict]
    recommendation:str
    confidence:str
    assumptions:list[str]
    verification_plan:list[str]
    reality:str="INFERRED"


def classify_intent(text:str)->str:
    t=text.lower()
    for label,words in [("BUILD",("build","launch","create","develop")),("RESEARCH",("research","investigate","find out")),("ANALYZE",("analyze","audit","check")),("DECIDE",("choose","decide","recommend","compare")),("FIX",("fix","repair")),("AUTOMATE",("automate","schedule","workflow")),("SIMULATE",("simulate","what if","scenario")),("REVIEW",("review","critique"))]:
        if any(w in t for w in words): return label
    return "PLAN"

def parse_intent(text:str,context:dict|None=None)->IntentModel:
    context=context or {}; mode=classify_intent(text); ambiguities=[]
    if len(text.strip())<8: ambiguities.append("desired outcome is underspecified")
    if mode in {"BUILD","CREATE"} and not any(x in text.lower() for x in ("for ","user","customer","product")): ambiguities.append("target user or beneficiary is unknown")
    if not context.get("project_id"): ambiguities.append("project scope is not supplied")
    return IntentModel(goal=text.strip(),desired_outcome=text.strip(),mode=mode,scope=context.get("project_id","unscoped"),constraints=["do not fabricate missing facts","no external side effects without authorization"],resources=[],deadline=context.get("deadline"),preferences=[],success_criteria=["produce an explicit plan or verified result","preserve unknowns and blockers"],ambiguities=ambiguities,urgency=context.get("urgency","unknown"),risk_tolerance=context.get("risk_tolerance","unknown"))

def knowledge_gaps(intent:IntentModel,registry:CapabilityRegistry,project:ProjectState|None=None):
    gaps=[]
    if "target user or beneficiary is unknown" in intent.ambiguities: gaps.append(KnowledgeGap(core_id("gap"),"Who is the target user or beneficiary?","high","UNKNOWN","ask the user","may change the product/workflow","project scope"))
    if not intent.deadline: gaps.append(KnowledgeGap(core_id("gap"),"Is there a material deadline?","medium","UNKNOWN","ask only if scheduling changes the plan","may change critical path","project scope"))
    if not project: gaps.append(KnowledgeGap(core_id("gap"),"Which project should own this outcome?","high","UNKNOWN","ask or bind explicitly to a project","prevents cross-project contamination","personal state"))
    unavailable=[r.name for r in registry.records.values() if r.availability=="UNAVAILABLE"]
    if unavailable: gaps.append(KnowledgeGap(core_id("gap"),"Which unavailable capabilities are essential rather than optional?","medium","UNKNOWN","compile a plan-only or simulation path","may change execution mode","capability registry"))
    return gaps

def select_minimum_capabilities(intent:IntentModel,registry:CapabilityRegistry):
    required=["READ"] if intent.mode in {"RESEARCH","ANALYZE","REVIEW"} else []
    candidates=registry.select(intent.goal,required_ops=required or ["DISCOVER"],allow_writes=False)
    selected=candidates.get("selected",[])
    return {"required":required,"selected":selected,"unavailable_requirements":[] if selected else ["No verified available capability satisfies the current requirement"],"selection_reason":"minimum sufficient safe set; no write-like operations considered","reality":"REAL" if selected else "UNKNOWN","action_state":"PLANNED"}

def temporal_analysis(state:ProjectState):
    blockers=list(state.blockers); critical=[]
    if state.dependencies: critical.extend(state.dependencies[:3])
    return {"past":state.last_known_state,"present":state.status,"future":"AT_RISK" if blockers else "PLANNED","critical_path":critical,"waiting":[],"stale":False,"overdue":False,"reality":"REAL"}

def compile_decision(question:str,options:list[str]|None=None):
    opts=options or ["proceed with the smallest safe plan","defer until missing information is resolved","do nothing"]
    return Decision(id=core_id("decision"),question=question,options=[{"name":x,"status":"HYPOTHESIS","benefits":[],"cost":"unknown","risk":"unknown","reversibility":"unknown"} for x in opts],recommendation=opts[0],confidence="low",assumptions=["available evidence is incomplete"],verification_plan=["obtain relevant evidence","reassess before consequential action"],reality="INFERRED")

def simulate_workflow(intent:IntentModel,capability_selection:dict):
    tasks=[{"id":"understand","title":"Clarify intent and missing information","reality":"REAL","action_state":"PLANNED"},{"id":"plan","title":"Compile minimum sufficient workflow","reality":"REAL","action_state":"PLANNED"}]
    if not capability_selection.get("selected"): tasks.append({"id":"shadow","title":"Produce unavailable-capability shadow plan","reality":"SIMULATED","action_state":"PREPARED"})
    return {"mode":"SIMULATION","reality":"SIMULATED","action_state":"PREPARED","tasks":tasks,"external_side_effects":False,"verification":"simulation labels and capability availability checked","final_state":"PREPARED"}

def compile_omega2(outcome:str,context:dict|None=None,registry:CapabilityRegistry|None=None,mode="PLAN_ONLY"):
    if mode not in MODES: raise ValueError("invalid Ω² mode")
    context=context or {}; registry=registry or discover_actual_registry(); intent=parse_intent(outcome,context)
    project=ProjectState(project_id=context.get("project_id","unscoped"),objective=outcome,status="PLANNED",next_action="resolve material knowledge gaps before execution",scope=context.get("project_id","unscoped"))
    gaps=knowledge_gaps(intent,registry,context.get("project_state")); selection=select_minimum_capabilities(intent,registry); temporal=temporal_analysis(project); decision=compile_decision("What is the smallest safe sequence for this outcome?")
    workflow=simulate_workflow(intent,selection) if mode!="REAL" or not selection.get("selected") else {"mode":"REAL","reality":"REAL","action_state":"PLANNED","tasks":[],"external_side_effects":False,"final_state":"PLANNED"}
    return {"id":core_id("omega2"),"intent":asdict(intent),"outcome":{"text":outcome,"status":"PLANNED","reality":"REAL","action_state":"PLANNED"},"project_state":asdict(project),"knowledge_gaps":[asdict(x) for x in gaps],"capability_selection":selection,"temporal":temporal,"decision":asdict(decision),"workflow":workflow,"governance":governance_for(outcome),"mode":mode,"reality":"REAL" if mode=="REAL" and selection.get("selected") else ("SIMULATED" if mode in {"SIMULATION","DRY_RUN"} else "UNKNOWN"),"execution_performed":False,"verification_status":"UNKNOWN","persisted":False,"external_invocations":0}

def route_command(command:str):
    token=command.strip().split(maxsplit=1)[0].upper() if command.strip() else "PLAN"
    allowed={"BUILD","RESEARCH","ANALYZE","DECIDE","COMPARE","CREATE","FIX","IMPROVE","CONTINUE","RECOVER","REVIEW","AUTOMATE","SIMULATE","EXPLAIN","CRITIQUE","STOP","REVERSE","WHAT-IF","OPPORTUNITY","AUDIT"}
    return {"command":token,"recognized":token in allowed,"mode":"PLAN_ONLY" if token not in {"SIMULATE","WHAT-IF"} else "SIMULATION","governed":True}

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("outcome"); p.add_argument("--mode",default="PLAN_ONLY",choices=sorted(MODES)); a=p.parse_args(); print(json.dumps(compile_omega2(a.outcome,mode=a.mode),indent=2))
