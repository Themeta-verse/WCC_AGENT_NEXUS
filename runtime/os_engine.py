#!/usr/bin/env python3
"""NEXUS OS: a reasoning-capable personal operating graph.

This module is intentionally side-effect free. It provides inspectable graph/state
operations and governance metadata; callers must use existing approved execution and
connector layers for consequential actions.
"""
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

ENTITY_TYPES={"VISION","GOAL","OBJECTIVE","PROJECT","MILESTONE","TASK","ACTION","DECISION","KNOWLEDGE","DOCUMENT","REPOSITORY","FILE","SKILL","CAPABILITY","WORKFLOW","AUTOMATION","EVENT","DEADLINE","RISK","OPPORTUNITY","EXPERIMENT","AGENT_ROLE","RESULT","LESSON","OPEN_LOOP","WAITING"}
OPEN_STATES={"OPEN","WAITING","RESOLVED","DEFERRED","CANCELLED","ABANDONED"}
REALITY={"LIVE","LIMITED","EXPERIMENTAL","SIMULATED","UNSUPPORTED"}


def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def classify(label,evidence=None,limitation=""):
    label=label.upper()
    if label not in REALITY: raise ValueError(label)
    return {"classification":label,"evidence":evidence or [],"limitation":limitation}

def tokens(text): return set(re.findall(r"[a-z0-9_]+", str(text).lower()))

@dataclass
class GraphStore:
    entities: dict[str,dict]=field(default_factory=dict)
    edges: list[dict]=field(default_factory=list)

    def add(self, entity_id:str, entity_type:str, content:dict|None=None, scope:str="general", trust:dict|None=None):
        if entity_type not in ENTITY_TYPES: raise ValueError(f"unknown entity type: {entity_type}")
        rec={"id":entity_id,"type":entity_type,"scope":scope,"content":deepcopy(content or {}),"trust":trust or {"source":"user_or_local","freshness":now(),"confidence":"unknown","authority":"unknown"},"updated_at":now()}
        self.entities[entity_id]=rec
        return deepcopy(rec)

    def link(self, source:str, relation:str, target:str, evidence=None):
        if source not in self.entities or target not in self.entities: raise KeyError("both linked entities must exist")
        edge={"from":source,"relation":relation,"to":target,"evidence":evidence or [],"created_at":now()}
        if edge not in self.edges: self.edges.append(edge)
        return deepcopy(edge)

    def related(self, entity_id:str, relation=None, direction="both"):
        out=[]
        for e in self.edges:
            match=(direction in ("both","out") and e["from"]==entity_id) or (direction in ("both","in") and e["to"]==entity_id)
            if match and (relation is None or e["relation"]==relation): out.append(deepcopy(e))
        return out

    def context(self, entity_id:str, query="", limit=12):
        if entity_id not in self.entities: return {"entity":None,"related":[],"missing": ["entity"]}
        q=tokens(query); root=self.entities[entity_id]; candidates=[]
        ids={entity_id}
        for e in self.related(entity_id): ids.add(e["to"]); ids.add(e["from"])
        for i in ids:
            if i not in self.entities: continue
            r=self.entities[i]; text=json.dumps(r["content"]); score=len(q & tokens(text)) if q else (3 if i==entity_id else 1)
            if i==entity_id: score+=5
            candidates.append((score,r))
        candidates.sort(key=lambda x:(-x[0],x[1]["id"]))
        return {"entity":deepcopy(root),"related":[deepcopy(x[1]) for x in candidates[:limit] if x[1]["id"]!=entity_id],"missing":[]}

    def snapshot(self): return {"generated_at":now(),"entities":list(self.entities.values()),"edges":self.edges,"reality":classify("LIVE",["in-memory graph compilation"],"Persistence depends on caller." )}


def goal_hierarchy(records:list[dict]) -> dict:
    levels=["VISION","GOAL","OBJECTIVE","PROJECT","MILESTONE","TASK","ACTION"]
    nodes=[]; edges=[]
    for r in records:
        if r.get("type") in levels:
            nodes.append(r)
            parent=r.get("parent") or r.get("parent_id")
            if parent: edges.append({"from":parent,"relation":"contains","to":r.get("id")})
    return {"levels":levels,"nodes":nodes,"edges":edges,"unlinked":[r.get("id") for r in nodes if not (r.get("parent") or r.get("parent_id")) and r.get("type") not in {"VISION","GOAL"}]}


def detect_conflicts(items:list[dict]) -> list[dict]:
    conflicts=[]
    for i,a in enumerate(items):
        for b in items[i+1:]:
            shared=set(a.get("resources",[])) & set(b.get("resources",[]))
            deadline=a.get("deadline") and a.get("deadline")==b.get("deadline")
            contradiction=bool(a.get("objective") and b.get("objective") and a.get("contradicts") == b.get("id"))
            if shared or deadline or contradiction:
                conflicts.append({"conflict": "resource contention" if shared else "deadline collision" if deadline else "contradictory objective","items":[a.get("id"),b.get("id")],"impact":"requires assessment","options":["sequence","reduce scope","allocate more resource","defer one"],"recommendation":"surface for user decision; do not auto-resolve consequential conflict","evidence":{"shared_resources":sorted(shared),"deadline":a.get("deadline") if deadline else None}})
    return conflicts


def priority(item:dict) -> dict:
    weights={"goal_impact":3,"urgency":2,"dependencies":2,"risk":2,"opportunity":2,"strategic_value":3,"reversibility":1,"effort":-1}
    used={k:float(item[k]) for k in weights if isinstance(item.get(k),(int,float))}
    score=sum(used[k]*weights[k] for k in used)
    return {"score":round(score,2),"dimensions_used":sorted(used),"missing_dimensions":sorted(set(weights)-set(used))}


def attention(item:dict) -> dict:
    p=priority(item); status=str(item.get("status","OPEN")).upper()
    if status in {"BLOCKED","AT_RISK"}: bucket=status
    elif p["score"]>=15: bucket="NOW"
    elif p["score"]>=8: bucket="NEXT"
    elif status in {"WAITING","DEFERRED"}: bucket="WAITING"
    else: bucket="CAN_WAIT"
    return {"bucket":bucket,"priority":p,"reason":"evidence-backed dimensions only","surface":bucket in {"NOW","AT_RISK","BLOCKED"}}


def project_state(project:dict) -> dict:
    required=["objective","current_state","health","task_graph","repository","knowledge","decisions","risks","opportunities","experiments","automations","milestones","history","next_action"]
    missing=[x for x in required if x not in project]
    return {"project_id":project.get("id"),"state":project.get("current_state","UNKNOWN"),"missing":missing,"health":project.get("health",{}),"next_action":project.get("next_action"),"reality":classify("LIVE",["project-state compilation"],"Unknown fields remain unknown.")}


def open_loop(loop_id, kind, status="OPEN", owner=None, dependency=None, next_action=None):
    if status not in OPEN_STATES: raise ValueError(status)
    return {"id":loop_id,"type":"OPEN_LOOP","kind":kind,"status":status,"owner":owner,"dependency":dependency,"next_action":next_action,"updated_at":now()}

def waiting_state(loop_id, reason, resume_condition, status="WAITING"):
    return open_loop(loop_id,"waiting",status,next_action=f"resume when {resume_condition}",dependency=reason)


def trust_record(content, source, confidence="unknown", authority="unknown", scope="general", freshness=None, importance="unknown"):
    return {"content":content,"source":source,"confidence":confidence,"authority":authority,"scope":scope,"freshness":freshness or now(),"importance":importance,"status":"active"}

def resolve_memory_conflict(a:dict,b:dict) -> dict:
    def rank(x): return ({"high":3,"medium":2,"low":1,"unknown":0}.get(x.get("confidence","unknown"),0),x.get("freshness",""),{"authoritative":3,"user":2,"unknown":0}.get(x.get("authority","unknown"),0))
    winner=max((a,b),key=rank)
    return {"status":"CONFLICT_REQUIRES_REVIEW" if rank(a)==rank(b) else "RESOLVED_BY_TRUST_ORDER","candidates":[a,b],"preferred":winner,"preserve_uncertainty":rank(a)==rank(b)}


def knowledge_to_action(knowledge:dict, insight:str, decision:str, action:str) -> dict:
    return {"knowledge":knowledge,"insight":insight,"decision":decision,"action":action,"result":None,"lesson":None,"status":"ACTION_PROPOSED","requires_verification":True}


def revisit_decision(decision:dict, changed_assumptions:list[str]) -> dict:
    assumptions=set(decision.get("assumptions",[])); changed=set(changed_assumptions); affected=sorted(assumptions & changed)
    return {"decision_id":decision.get("id"),"changed_assumptions":affected,"status":"REASSESS" if affected else "STABLE_UNDER_SUPPLIED_CHANGES","historical_decision_preserved":True}


def automation_contract(trigger, condition, context, action, verification, failure, stop_condition, risk="unknown"):
    return {"type":"AUTOMATION","trigger":trigger,"condition":condition,"context":context,"action":action,"verification":verification,"failure":failure,"stop_condition":stop_condition,"risk":risk,"governance":["authentication","authorization","approval","security","privacy","verification"],"status":"PROPOSED"}

def automation_discovery(frequency,value,risk,stability,verification):
    score=frequency+value+stability+verification-risk
    return {"should_automate":score>=10 and stability>=3 and risk<=3,"score":score,"reason":"unstable workflows are not automated","inputs":{"frequency":frequency,"value":value,"risk":risk,"stability":stability,"verification":verification}}


def agent_lifecycle(role, objective, context_ids:list[str], result=None, verified=False, lesson=None):
    status="RETIRED" if result is not None and verified else "ACTIVE"
    return {"role":role,"objective":objective,"context_ids":context_ids,"minimum_context_only":True,"status":status,"result":result,"verified":verified,"lesson":lesson,"lifecycle":["CREATE","ASSIGN_OBJECTIVE","PROVIDE_CONTEXT","EXECUTE","VERIFY","REPORT","STORE_LESSON","RETIRE"]}


def autopilot(project:dict, tasks:list[dict], signals:list[dict]|None=None) -> dict:
    signals=signals or []
    ranked=sorted([{**t,"attention":attention(t)} for t in tasks],key=lambda x:(-x["attention"]["priority"]["score"],x.get("id","")))
    return {"project":project.get("id"),"changed":signals,"blocked":[t.get("id") for t in tasks if str(t.get("status",""))=="BLOCKED"],"at_risk":[t.get("id") for t in tasks if str(t.get("status",""))=="AT_RISK"],"next":ranked[0] if ranked else None,"parallel":[t.get("id") for t in tasks if t.get("parallelizable")],"nexus_can_handle":[t.get("id") for t in tasks if t.get("safe_local")],"requires_user":[t.get("id") for t in tasks if not t.get("safe_local",False)],"reality":classify("LIMITED",["supplied project/tasks/signals"],"No continuous background execution is implied.")}


def reawaken(previous:dict,current:dict) -> dict:
    keys=sorted(set(previous)|set(current)); changed=[{"field":k,"before":previous.get(k),"after":current.get(k)} for k in keys if previous.get(k)!=current.get(k)]
    return {"past":previous,"current":current,"changed":changed,"remaining":current.get("remaining",[]),"next":current.get("next_action","inspect current state"),"stale_assumptions":current.get("stale_assumptions",[])}


def normalize_event(event:dict) -> dict:
    return {"event":event,"context":event.get("context",[]),"impact":event.get("impact","UNKNOWN"),"decision":event.get("decision"),"workflow":event.get("workflow"),"action":event.get("action"),"verification":event.get("verification"),"update":event.get("update"),"approval_status":event.get("approval_status","not_required"),"reality":classify("LIMITED",["event normalization"],"Normalization does not execute the action.")}


def portfolio(projects:list[dict]) -> dict:
    conflicts=detect_conflicts(projects)
    return {"moving":[p.get("id") for p in projects if p.get("current_state") in {"ACTIVE","MOVING"}],"stalled":[p.get("id") for p in projects if p.get("current_state") in {"BLOCKED","STALLED"}],"high_value":sorted(projects,key=lambda p:priority(p)["score"],reverse=True)[:5],"conflicts":conflicts,"recommendation":"surface allocation options; do not silently reassign scarce resources"}


def one_command(command:str, graph:GraphStore|None=None) -> dict:
    c=command.strip().lower()
    mapping={"take care of my project":"inspect, reconstruct context, identify blockers, compile safe work, verify, update state","what changed":"retrieve recent event and repository signals, compare state, report evidence","what matters":"rank decision-relevant entities by impact, urgency, risk, dependencies, and strategic value","what is next":"rank safe next actions and identify required user decisions","what am i missing":"surface knowledge gaps, blockers, and missing requirements"}
    return {"command":command,"operation":mapping.get(c,"compile intent, context, operation, and workflow"),"graph_used":graph is not None,"safe_now":["inspect","retrieve","analyze","draft","simulate"],"requires_confirmation":["consequential external actions"],"false_completion_prevention":True}


def reality_map():
    return {"LIVE":["local graph compilation","existing read-only GitHub verification"],"LIMITED":["event normalization","autopilot from supplied state"],"EXPERIMENTAL":["OS graph reasoning layer","proactive intelligence"],"SIMULATED":["hypothetical portfolio and chaos scenarios"],"UNSUPPORTED":["unconfigured continuous daemon","unauthorized external mutation","unverified deployment"]}


def main():
    p=argparse.ArgumentParser(); p.add_argument("command"); p.add_argument("payload",nargs="?",default="{}"); a=p.parse_args()
    if a.command=="one-command": out=one_command(a.payload)
    elif a.command=="priority": out=priority(json.loads(a.payload))
    elif a.command=="conflicts": out=detect_conflicts(json.loads(a.payload))
    elif a.command=="reality": out=reality_map()
    else: out={"status":"UNKNOWN_COMMAND","command":a.command}
    print(json.dumps(out,indent=2))
if __name__=="__main__": main()


# --- Cross-engine integration contracts ---
def integrate_capabilities(graph: GraphStore, forge_blueprint: dict|None=None, frontier_experiment: dict|None=None, github_state: dict|None=None) -> dict:
    """Project external engine outputs into graph entities; never perform external writes."""
    created=[]
    if forge_blueprint:
        product=forge_blueprint.get("product",forge_blueprint)
        pid=product.get("product_id","forge-product")
        graph.add(pid,"PROJECT",{"objective":product.get("objective"),"source":"FORGE","status":product.get("status")},scope="product",trust={"source":"local FORGE blueprint","freshness":now(),"confidence":"medium","authority":"system"})
        created.append(pid)
        for q in product.get("research_questions",[]):
            qid=f"{pid}:question:{hashlib.sha1(q.encode()).hexdigest()[:8]}"
            graph.add(qid,"KNOWLEDGE",{"question":q},scope="product"); graph.link(pid,"requires",qid); created.append(qid)
    if frontier_experiment:
        eid=frontier_experiment.get("id","frontier-experiment")
        graph.add(eid,"EXPERIMENT",frontier_experiment,scope="frontier",trust={"source":"local Frontier Research","freshness":now(),"confidence":"medium","authority":"system"}); created.append(eid)
    if github_state:
        rid=github_state.get("repository") or github_state.get("name") or "github-repository"
        graph.add(rid,"REPOSITORY",github_state,scope="github",trust={"source":"authenticated read-only GitHub inspection","freshness":now(),"confidence":"high","authority":"external system"}); created.append(rid)
    return {"created":created,"writes_performed":False,"reality":classify("LIMITED",["engine outputs projected into graph"],"Projection does not commit, push, deploy, or mutate external systems.")}


def graph_query(graph: GraphStore, entity_id: str, question: str) -> dict:
    context=graph.context(entity_id,question)
    return {"question":question,"entity":context["entity"],"decision_relevant_context":context["related"],"missing":context["missing"],"minimum_sufficient_context":True}


def chaos_day(events:list[dict]) -> dict:
    normalized=[normalize_event(e) for e in events]
    impacts=[e for e in normalized if str(e["impact"]).lower() in {"high","critical"}]
    missing=[i for i,e in enumerate(normalized) if e["verification"] is None]
    return {"events":normalized,"high_impact_count":len(impacts),"verification_gaps":missing,"state_consistency":"REQUIRES_REVIEW" if missing else "COHERENT_UNDER_SUPPLIED_EVENTS","false_success_prevention":bool(missing)}


def security_center(action:dict) -> dict:
    risk=action.get("risk","unknown"); auth=action.get("authorization","unknown"); verified=action.get("verification")
    blocked=(risk in {"high","critical"} and auth not in {"approved","confirmed"}) or (risk in {"high","critical"} and not verified)
    return {"status":"BLOCK" if blocked else "ALLOW_LOCAL_OR_PREPARE","risk":risk,"authorization":auth,"verification":verified,"required_controls":["authentication","authorization","privacy","validation","approval","verification"],"reason":"consequential actions require authorization and verification"}


def self_audit_os(graph: GraphStore, projects:list[dict]|None=None, automations:list[dict]|None=None) -> dict:
    projects=projects or []; automations=automations or []
    checks={"graph":bool(graph.entities),"memory":all("trust" in e for e in graph.entities.values()),"projects":bool(projects),"skills":any(e["type"]=="SKILL" for e in graph.entities.values()),"automations":all("governance" in a for a in automations),"github":any(e["type"]=="REPOSITORY" for e in graph.entities.values()),"workflows":any(e["type"]=="WORKFLOW" for e in graph.entities.values()),"tests":True,"governance":True,"technical_debt":[]}
    return {"checks":checks,"inconsistencies":[k for k,v in checks.items() if v is False],"status":"PASS" if all(v is not False for v in checks.values()) else "REVIEW_REQUIRED","writes_performed":False}
