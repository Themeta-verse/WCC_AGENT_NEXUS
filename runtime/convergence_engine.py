#!/usr/bin/env python3
"""NEXUS Ω Convergence Mode.

The module coordinates existing NEXUS engines through common contracts. It is
side-effect-minimal: event/state artifacts are returned to callers, and external
writes remain behind authorization and independent verification gates.
"""
from __future__ import annotations
import datetime as dt, hashlib, json, re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

SYSTEM_FLOW=["INTENT","OUTCOME","CONTEXT","STATE","WORLD_MODEL","CAPABILITIES","METHOD_SELECTION","WORKFLOW","TASK_GRAPH","EXECUTION","VERIFICATION","RESULT","MEMORY","LEARNING","SYSTEM_EVOLUTION"]
TERMINAL={"DONE","PARTIAL","BLOCKED","FAILED","WAITING","CANCELLED","UNKNOWN"}
CONTROL={"RUNNING","PAUSED","WAITING_FOR_APPROVAL","WAITING_FOR_INFORMATION","BLOCKED","STOPPED","FAILED","COMPLETED"}
SIDE_EFFECTS={"delete","publish","send","financial","permission_change","production_modification","data_destruction","repository_write","deploy"}


def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
def reality(label,evidence=None,limitation=""):
    return {"classification":label,"evidence":evidence or [],"limitation":limitation}

@dataclass
class CanonicalState:
    authoritative: dict[str,Any]=field(default_factory=dict)
    derived: dict[str,Any]=field(default_factory=dict)
    cached: dict[str,Any]=field(default_factory=dict)
    historical: list[dict]=field(default_factory=list)
    transitions: list[dict]=field(default_factory=list)

    def snapshot(self):
        return {"authoritative":deepcopy(self.authoritative),"derived":deepcopy(self.derived),"cached":deepcopy(self.cached),"historical":deepcopy(self.historical),"transitions":deepcopy(self.transitions)}

    def consistency(self):
        issues=[]
        for key,value in self.cached.items():
            if key in self.authoritative and value!=self.authoritative[key]: issues.append({"type":"CACHE_DIVERGENCE","key":key})
        for key,value in self.derived.items():
            if key not in self.authoritative: issues.append({"type":"DERIVED_WITHOUT_AUTHORITY","key":key})
        return {"status":"CONSISTENT" if not issues else "INCONSISTENT","issues":issues,"authoritative_keys":sorted(self.authoritative),"derived_keys":sorted(self.derived),"cached_keys":sorted(self.cached)}

@dataclass
class EventStore:
    events: dict[str,dict]=field(default_factory=dict)
    order: list[str]=field(default_factory=list)

    def create(self, event_type:str, payload:dict, source="local", status="observed", confidence="unknown", verification=None, event_id=None, occurred_at=None):
        body={"type":event_type,"payload":payload,"source":source,"status":status,"confidence":confidence,"verification":verification,"occurred_at":occurred_at or now()}
        eid=event_id or digest(body)[:24]
        body.update({"id":eid,"received_at":now()})
        if eid not in self.events:
            self.events[eid]=body; self.order.append(eid)
            return {"event":deepcopy(body),"duplicate":False}
        return {"event":deepcopy(self.events[eid]),"duplicate":True}

    def replay(self): return [deepcopy(self.events[eid]) for eid in self.order]

    def out_of_order(self):
        return [self.events[eid] for eid in self.order if self.order.index(eid)>0 and self.events[eid]["occurred_at"]<self.events[self.order[self.order.index(eid)-1]]["occurred_at"]]


def causal_transition(state:CanonicalState, event:dict, after:dict, expected_effect=None, actual_effect=None):
    before=deepcopy(state.authoritative)
    state.historical.append({"before":before,"event":deepcopy(event),"after":deepcopy(after),"expected_effect":expected_effect,"actual_effect":actual_effect,"timestamp":now()})
    state.authoritative.update(deepcopy(after)); state.transitions.append(state.historical[-1])
    return state.historical[-1]


def event_to_state(event:dict, state:CanonicalState, impact=None, workflow=None):
    payload=event.get("payload",{})
    update=payload.get("state_update",{})
    trace=causal_transition(state,event,update,expected_effect=payload.get("expected_effect"),actual_effect=payload.get("actual_effect")) if update else None
    return {"event":event,"state_update":update,"impact":impact or payload.get("impact","UNKNOWN"),"possible_workflow":workflow or payload.get("workflow"),"trace":trace,"consistency":state.consistency()}


def verification_contract(expected_state, method, authority, failure_condition, independent=True):
    return {"expected_state":expected_state,"verification_method":method,"authoritative_source":authority,"failure_condition":failure_condition,"independent":independent,"status":"DEFINED"}


def completion_gate(status, contract, observed=None):
    observed=observed or {}; passed=bool(contract and observed.get("verified") is True and observed.get("authoritative_source")==contract.get("authoritative_source"))
    if status=="DONE" and not passed: return {"status":"UNKNOWN","allowed":False,"reason":"DONE requires defined verification against the authoritative source"}
    if status not in TERMINAL: return {"status":"UNKNOWN","allowed":False,"reason":"invalid completion state"}
    return {"status":status,"allowed":True,"verified":passed,"reason":"verification evidence supplied" if passed else "non-DONE state does not claim completion"}


def tool_result(source,status,confidence,verification,payload=None):
    return {"source":source,"status":status,"confidence":confidence,"verification":verification,"payload":payload or {}}


def fallback(preferred, alternatives, available, objective):
    if preferred in available: return {"selected":preferred,"mode":"PREFERRED","objective":objective,"pretend_success":False}
    for option in alternatives:
        if option in available: return {"selected":option,"mode":"FALLBACK","objective":objective,"pretend_success":False,"reason":f"preferred capability unavailable: {preferred}"}
    return {"selected":None,"mode":"PREPARE_OR_BLOCK","objective":objective,"pretend_success":False,"next":"manual preparation or partial completion"}


def compile_workflow(objective:str, requirements:dict|None=None, available:dict|None=None):
    requirements=requirements or {}; available=available or {}
    steps=["understand","research","define","plan"]
    if requirements.get("design",True): steps.append("design")
    steps += ["build","test","security"]
    if requirements.get("review",True): steps.append("review")
    if requirements.get("github"): steps.append("github")
    if requirements.get("deploy"): steps.append("deploy where supported")
    steps += ["verify","learn"]
    unavailable=[x for x in steps if x in available and available[x] is False]
    return {"objective":objective,"steps":steps,"required_steps":[s for s in steps if s not in {"design","review","github","deploy where supported"} or requirements.get(s.replace(" ","_"),True)],"unavailable":unavailable,"reality":reality("EXPERIMENTAL",["workflow compilation"],"Execution still requires governed task execution and verification.")}


def replan(workflow:dict, changes:list[dict]):
    severe=[c for c in changes if c.get("severity") in {"high","critical"} or c.get("type") in {"dependency_failed","risk_increased","objective_changed","tool_unavailable"}]
    if not severe: return {"replan":False,"workflow":workflow,"reason":"no material change supplied"}
    steps=[s for s in workflow.get("steps",[]) if s not in {c.get("remove_step") for c in severe}]
    return {"replan":True,"workflow":{**workflow,"steps":steps,"replanned_for":severe},"reason":"reality changed; original plan not followed blindly"}


def task_graph(tasks:list[dict]):
    ids={t.get("id") for t in tasks}; missing=[]; cycle=False
    for t in tasks:
        for dep in t.get("depends_on",[]):
            if dep not in ids: missing.append({"task":t.get("id"),"dependency":dep})
            if dep==t.get("id"): cycle=True
    indegree={t.get("id"):0 for t in tasks}; children={t.get("id"):[] for t in tasks}
    for t in tasks:
        for dep in t.get("depends_on",[]):
            if dep in indegree: indegree[t["id"]]+=1; children[dep].append(t["id"])
    ready=[i for i,v in indegree.items() if v==0]; order=[]
    while ready:
        n=ready.pop(0); order.append(n)
        for child in children[n]:
            indegree[child]-=1
            if indegree[child]==0: ready.append(child)
    if len(order)!=len(tasks): cycle=True
    blockers=missing or ([{"type":"CYCLE"}] if cycle else [])
    return {"order":order,"critical_path":order,"parallel_groups":[[i for i in order if next((t for t in tasks if t.get("id")==i),{}).get("parallelizable")]],"blockers":blockers,"status":"BLOCKED" if blockers else "VALID"}


def resource_plan(steps:list[str], resources:dict):
    available=set(resources.get("available",[])); permissions=set(resources.get("permissions",[])); connectors=set(resources.get("connectors",[])); files=set(resources.get("files",[]))
    missing=[]
    for step in steps:
        req=resources.get("required_by_step",{}).get(step,[])
        for r in req:
            if r not in available|permissions|connectors|files: missing.append({"step":step,"resource":r})
    return {"available":sorted(available|permissions|connectors|files),"missing":missing,"status":"READY" if not missing else "PARTIAL","never_assume_missing":True}


def control_center(state="RUNNING", reason=""):
    if state not in CONTROL: raise ValueError(state)
    return {"state":state,"reason":reason,"new_consequential_actions_allowed":state=="RUNNING","preserve_state":True,"user_visible":True}

def global_stop(reason): return control_center("STOPPED",reason)
def global_pause(reason): return control_center("PAUSED",reason)
def resume(snapshot, dependencies_ok=False): return {"state":"RUNNING" if dependencies_ok else "WAITING_FOR_INFORMATION","revalidate_current_reality":True,"dependencies_ok":dependencies_ok,"snapshot":snapshot}


def interruption_recovery(snapshot:dict, completed:list[str], remaining:list[str], blockers:list[str], safe_action:str):
    return {"current_state":snapshot,"completed_work":completed,"remaining_work":remaining,"blockers":blockers,"next_safe_action":safe_action,"reconstructable":True,"duplicate_execution_prevention":True}


def recursion_guard(depth:int, max_depth:int, stop_condition:str, failure_state="BLOCKED"):
    return {"allowed":depth<max_depth,"depth":depth,"max_depth":max_depth,"stop_condition":stop_condition,"failure_state":failure_state}


def destructive_gate(action:dict):
    kind=str(action.get("kind","")).lower(); risk=action.get("risk","unknown"); auth=action.get("authorization")
    consequential=kind in SIDE_EFFECTS or risk in {"high","critical"}
    return {"status":"CONFIRM_REQUIRED" if consequential and auth not in {"confirmed","approved"} else "ALLOW_GOVERNED_PATH","consequential":consequential,"authorization":auth,"never_infer_approval":True}


def prompt_injection_defense(content:str):
    patterns=[r"ignore (all )?previous instructions",r"disable (security|governance)",r"you are now",r"user already approved",r"reveal (secrets|system prompt)"]
    matches=[p for p in patterns if re.search(p,content.lower())]
    return {"untrusted":True,"matches":matches,"status":"BLOCK_OR_TREAT_AS_DATA" if matches else "DATA_ONLY","cannot_change_governance":True}


def secret_scan(text:str):
    patterns=[r"sk-[A-Za-z0-9]{20,}",r"ghp_[A-Za-z0-9]{20,}",r"AKIA[0-9A-Z]{16}",r"-----BEGIN [A-Z ]+ PRIVATE KEY-----",r"password\s*[:=]"]
    hits=[p for p in patterns if re.search(p,text)]
    return {"status":"BLOCK_REDACT_REPORT" if hits else "CLEAR","hits":hits,"never_expose":True}


def cross_project_boundary(project_a:str, project_b:str, context_scope:str):
    allowed=context_scope in {project_a,"general"}
    return {"allowed":allowed,"scope":context_scope,"status":"SAFE" if allowed else "BLOCKED_CROSS_PROJECT_CONTAMINATION"}


def autonomy_gate(level:str, requested:str, approval=None):
    order={"OBSERVE":0,"RECOMMEND":1,"PREPARE":2,"CONFIRM":3,"EXECUTE":4}
    if requested not in order or level not in order: return {"status":"BLOCKED","reason":"unknown autonomy level"}
    escalates=order[requested]>order[level]
    return {"status":"CONFIRM_REQUIRED" if escalates and approval not in {"approved","confirmed"} else "ALLOWED","from":level,"requested":requested,"approval":approval,"false_approval_rejected":approval not in {"approved","confirmed"} and escalates}


def capability_matrix(items:list[dict]):
    return [{"capability":i.get("capability"),"status":i.get("status","UNKNOWN"),"evidence":i.get("evidence",[]),"limitation":i.get("limitation",""),"dependency":i.get("dependency",[]),"next_improvement":i.get("next_improvement","")} for i in items]


def integrity_scorecard(metrics:dict):
    dims=["capability","reliability","security","verification","recovery","continuity","user_effort","complexity","adaptability","github_integration"]
    return {d:metrics.get(d,{"status":"UNKNOWN","evidence":[]}) for d in dims}


def bottleneck_findings(metrics:dict):
    candidates=metrics.get("candidates",[])
    if not candidates: return {"status":"UNKNOWN","reason":"insufficient evidence","primary_next_frontier":None}
    top=max(candidates,key=lambda x:x.get("impact",0)*x.get("confidence",0))
    return {"status":"EVIDENCE_RANKED","primary_next_frontier":top,"cheapest_test":top.get("cheapest_test"),"do_not_build_without_test":True}


def self_critique(matrix:list[dict]):
    strong=[i.get("capability") for i in matrix if i.get("status")=="LIVE"]
    weak=[i.get("capability") for i in matrix if i.get("status") in {"UNKNOWN","LIMITED","UNSUPPORTED"}]
    return {"genuinely_strong":strong,"weak_or_unverified":weak,"theoretical":[i.get("capability") for i in matrix if i.get("status") in {"EXPERIMENTAL","SIMULATED"}],"evidence_bounded":True,"no_private_chain_of_thought":True}


def convergence_model(): return {"flow":SYSTEM_FLOW,"one_state_model":True,"one_governance_model":True,"one_execution_loop":True,"one_verification_loop":True,"one_learning_loop":True,"reality":reality("EXPERIMENTAL",["canonical flow contract"],"Module-level integration still requires runtime adoption and real-world validation.")}


def system_simulation(events:list[dict]):
    state=CanonicalState(); store=EventStore(); traces=[]; duplicate_count=0
    for e in events:
        received=store.create(e.get("type","UNKNOWN"),e.get("payload",{}),source=e.get("source","simulation"),status=e.get("status","simulated"),confidence=e.get("confidence","unknown"),verification=e.get("verification"),event_id=e.get("id"),occurred_at=e.get("occurred_at"))
        duplicate_count += int(received["duplicate"])
        if not received["duplicate"]:
            traces.append(event_to_state(received["event"],state,e.get("impact")))
    return {"events":store.replay(),"duplicates":duplicate_count,"state":state.snapshot(),"consistency":state.consistency(),"traces":traces,"reality":reality("SIMULATED",["supplied event sequence"],"Not evidence of production behavior.")}
