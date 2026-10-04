#!/usr/bin/env python3
"""NEXUS Ω⁴ local Reality Engine.

Evidence-bounded reconciliation only. No connector calls, external writes,
permission changes, or persistence are performed by this module.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone,timedelta
from typing import Any
import json
try:
    from canonical_core import core_id,utc_now
except ImportError:
    from .canonical_core import core_id,utc_now

STATES={"UNKNOWN","HYPOTHESIS","ASSUMED","PLANNED","SIMULATED","PREPARED","EXECUTED","OBSERVED","VERIFIED","PERSISTED","SUPERSEDED","CONFLICTING"}
AUTHORITY={"DIRECT_AUTHORITATIVE_EXTERNAL_OBSERVATION":100,"VERIFIED_INTERNAL_STATE":90,"TRUSTED_PERSISTED_STATE":80,"DERIVED_STATE":60,"MODEL_INFERENCE":40,"HYPOTHESIS":20,"SIMULATION":10}
FRESHNESS_TTL={"repository":3600,"execution":3600,"workflow":86400,"project":2592000,"preference":15552000,"default":86400}
TRANSITIONS={
 "UNKNOWN":{"HYPOTHESIS","ASSUMED","PLANNED"},"HYPOTHESIS":{"OBSERVED","CONFLICTING","SUPERSEDED"},"ASSUMED":{"PLANNED","OBSERVED","CONFLICTING"},"PLANNED":{"PREPARED","EXECUTED","CONFLICTING"},"PREPARED":{"EXECUTED","CONFLICTING"},"EXECUTED":{"OBSERVED","VERIFIED","CONFLICTING"},"OBSERVED":{"VERIFIED","SUPERSEDED","CONFLICTING"},"VERIFIED":{"PERSISTED","SUPERSEDED","CONFLICTING"},"PERSISTED":{"SUPERSEDED","CONFLICTING"},"CONFLICTING":{"HYPOTHESIS","OBSERVED","VERIFIED","SUPERSEDED"},"SIMULATED":{"SUPERSEDED","CONFLICTING"},"SUPERSEDED":set()
}

@dataclass
class Claim:
    claim_id:str
    statement:str
    source:str
    scope:str
    timestamp:str
    reality_state:str
    confidence:float
    supporting_evidence:list[str]=field(default_factory=list)
    contradicting_evidence:list[str]=field(default_factory=list)
    dependencies:list[str]=field(default_factory=list)
    supersedes:list[str]=field(default_factory=list)
    superseded_by:list[str]=field(default_factory=list)
    domain:str="default"
    observed_at:str|None=None
    valid_from:str|None=None
    valid_until:str|None=None

@dataclass
class Conflict:
    conflict_id:str
    conflict_type:str
    source_a:str
    source_b:str
    severity:str
    explanation:str
    resolution_status:str="UNRESOLVED"
    recommended_action:str="collect evidence and request human review"

class RealityError(ValueError): pass

def transition(current:str,target:str,evidence:dict|None=None):
    if current not in STATES or target not in STATES: raise RealityError("unknown reality state")
    evidence=evidence or {}
    if target not in TRANSITIONS.get(current,set()): raise RealityError(f"invalid reality transition {current}->{target}")
    if target=="OBSERVED" and not evidence.get("external_observation",False): raise RealityError("OBSERVED requires real observation evidence")
    if target=="EXECUTED" and not evidence.get("execution_evidence",False): raise RealityError("EXECUTED requires execution evidence")
    if target=="VERIFIED" and not evidence.get("verification_evidence",False): raise RealityError("VERIFIED requires verification evidence")
    if current=="SIMULATED" and target=="OBSERVED": raise RealityError("simulation cannot become observation")
    return target

class ClaimGraph:
    def __init__(self): self.claims:dict[str,Claim]={}
    def add(self,statement,source,scope,reality_state="UNKNOWN",confidence=0.0,**kwargs):
        if reality_state not in STATES: raise RealityError("invalid claim reality state")
        if reality_state in {"OBSERVED","EXECUTED","VERIFIED","PERSISTED"} and not source: raise RealityError("operational claim requires source")
        c=Claim(core_id("claim"),statement,source,scope,utc_now(),reality_state,float(confidence),**kwargs); self.claims[c.claim_id]=c; return c
    def support(self,claim_id,evidence): self.claims[claim_id].supporting_evidence.append(evidence)
    def contradict(self,claim_id,evidence): self.claims[claim_id].contradicting_evidence.append(evidence); self.claims[claim_id].reality_state="CONFLICTING"
    def trace(self,claim_id):
        c=self.claims[claim_id]; return {"claim":asdict(c),"evidence":c.supporting_evidence,"source":c.source,"interpretation":c.statement,"dependencies":c.dependencies}
    def export(self): return [asdict(c) for c in self.claims.values()]

def authority_rank(source_type:str): return AUTHORITY.get(source_type,0)

def freshness(claim:Claim,now:datetime|None=None):
    if not claim.observed_at: return {"status":"unknown","age_seconds":None,"ttl_seconds":FRESHNESS_TTL.get(claim.domain,FRESHNESS_TTL['default'])}
    now=now or datetime.now(timezone.utc); observed=datetime.fromisoformat(claim.observed_at.replace('Z','+00:00')); age=max(0,(now-observed).total_seconds()); ttl=FRESHNESS_TTL.get(claim.domain,FRESHNESS_TTL['default'])
    status="fresh" if age<=ttl*.5 else "aging" if age<=ttl else "stale" if age<=ttl*2 else "expired"
    return {"status":status,"age_seconds":age,"ttl_seconds":ttl}

def verification_quality(kind:str,target:bool=True):
    levels={"SELF":20,"DERIVED":40,"INDEPENDENT":70,"EXTERNAL":80,"AUTHORITATIVE":100}
    return {"kind":kind,"score":levels.get(kind,0),"target_present":target,"acceptable":bool(target and levels.get(kind,0)>=70)}

def compare_expected_actual(expected:dict,actual:dict):
    keys=sorted(set(expected)|set(actual)); discrepancies=[]
    for k in keys:
        if k not in actual: discrepancies.append({"field":k,"type":"MISSING_ACTUAL","expected":expected[k]})
        elif k not in expected: discrepancies.append({"field":k,"type":"UNEXPECTED_ACTUAL","actual":actual[k]})
        elif expected[k]!=actual[k]: discrepancies.append({"field":k,"type":"VALUE_MISMATCH","expected":expected[k],"actual":actual[k]})
    return {"status":"MATCH" if not discrepancies else "DISCREPANCY","discrepancies":discrepancies}

def partial_recovery(tasks:list[dict]):
    return {"completed":[t['id'] for t in tasks if t.get('status') in {'SUCCEEDED','VERIFIED','COMPLETED'}],"verified":[t['id'] for t in tasks if t.get('verification') in {'VERIFIED','INDEPENDENT','AUTHORITATIVE'}],"failed":[t['id'] for t in tasks if t.get('status')=='FAILED'],"remaining":[t['id'] for t in tasks if t.get('status') not in {'SUCCEEDED','VERIFIED','COMPLETED','FAILED'}],"unsafe_to_repeat":[t['id'] for t in tasks if t.get('side_effect_risk')=='HIGH' and t.get('status') in {'EXECUTED','UNKNOWN'}],"safe_to_retry":[t['id'] for t in tasks if t.get('retryable') is True],"needs_human_review":[t['id'] for t in tasks if t.get('status') in {'FAILED','UNKNOWN'} or t.get('verification')=='UNKNOWN']}

def consistency_check(snapshot:dict):
    conflicts=[]
    def add(kind,a,b,sev,exp): conflicts.append(asdict(Conflict(core_id('conflict'),kind,a,b,sev,exp)))
    if snapshot.get('capability_status')=='UNAVAILABLE' and snapshot.get('workflow_status')=='EXECUTED': add('CAPABILITY_STATE_CONFLICT','registry','workflow','HIGH','unavailable capability cannot be executed')
    if snapshot.get('execution_status')=='SUCCESS' and snapshot.get('verification_status') in {None,'UNKNOWN','FAILED'}: add('INCOMPLETE_SUCCESS','execution','verification','HIGH','execution success without required verification is not completion')
    if snapshot.get('capability_operation')=='READ_ONLY' and snapshot.get('workflow_operation') in {'WRITE','DELETE','MODIFY'}: add('PERMISSION_CONFLICT','capability','workflow','CRITICAL','read-only capability cannot authorize write-like workflow')
    if snapshot.get('reality')=='SIMULATED' and snapshot.get('final_status') in {'COMPLETED','VERIFIED','SUCCEEDED'}: add('REALITY_LABEL_CONFLICT','simulation','final_result','HIGH','simulation cannot be reported as completed external reality')
    if snapshot.get('completion_required_verification') and snapshot.get('completion_status')=='COMPLETED' and snapshot.get('verification_status')!='VERIFIED': add('COMPLETION_WITHOUT_VERIFICATION','completion','verification','HIGH','required verification is absent')
    if snapshot.get('external_observation') and not snapshot.get('source'): add('UNSOURCED_OBSERVATION','observation','source','HIGH','external observation requires source')
    if snapshot.get('cross_project_context') and not snapshot.get('scope'): add('CROSS_PROJECT_SCOPE','context','scope','CRITICAL','cross-project context requires explicit scope')
    if snapshot.get('external_content_instruction'): add('UNTRUSTED_INSTRUCTION','external_content','governance','CRITICAL','external content cannot become an instruction')
    return {"consistent":not conflicts,"conflicts":conflicts}

def invariant_check(snapshot:dict):
    checks={
      'NO_EXECUTION_WITHOUT_AUTHORIZATION': not (snapshot.get('execution_status') and snapshot.get('execution_status')!='NOT_EXECUTED' and not snapshot.get('authorized',False)),
      'NO_VERIFICATION_WITHOUT_TARGET': not (snapshot.get('verification_status') and snapshot.get('verification_status')!='UNKNOWN' and not snapshot.get('verification_target',False)),
      'NO_SIMULATION_AS_REALITY': not (snapshot.get('reality')=='SIMULATED' and snapshot.get('final_status') in {'COMPLETED','VERIFIED'}),
      'NO_CROSS_PROJECT_CONTEXT_WITHOUT_SCOPE': not (snapshot.get('cross_project_context') and not snapshot.get('scope')),
      'NO_WRITE_WITHOUT_GOVERNANCE': not (snapshot.get('workflow_operation') in {'WRITE','DELETE','MODIFY'} and not snapshot.get('governance_approved',False)),
      'NO_EXTERNAL_OBSERVATION_WITHOUT_SOURCE': not (snapshot.get('external_observation') and not snapshot.get('source')),
    }
    return {'passed':all(checks.values()),'checks':checks}

def reconcile(snapshot:dict):
    return {'consistency':consistency_check(snapshot),'invariants':invariant_check(snapshot),'reality':'OBSERVED' if snapshot.get('external_observation') and snapshot.get('source') else 'UNKNOWN','external_invocations':0,'writes_performed':False}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('snapshot_json'); a=p.parse_args(); print(json.dumps(reconcile(json.loads(a.snapshot_json)),indent=2))
