#!/usr/bin/env python3
"""Controlled R&D and temporary-role composition for NEXUS."""
from __future__ import annotations
from dataclasses import dataclass,asdict
from typing import Any

ROLES=['RESEARCHER','ARCHITECT','ENGINEER','ANALYST','STRATEGIST','CREATIVE_DIRECTOR','CRITIC','RED_TEAM','RISK_REVIEWER','VERIFIER']
PROTECTED={'permissions','security','privacy','approval','destructive','human override','governance'}
@dataclass
class LabExperiment:
    question:str; hypothesis:str; design:list[str]; expected_value:str; cost:str; reversibility:str; learning_value:str; classification:str='EXPERIMENTAL'; status:str='PROPOSED'
    def to_dict(self): return asdict(self)

def compose_roles(objective:str, roles:list[str]|None=None)->dict:
    selected=[r for r in (roles or ['RESEARCHER','ARCHITECT','CRITIC','VERIFIER']) if r in ROLES]
    return {'objective':objective,'temporary_roles':selected,'permanent_personalities_created':False,'synthesis':'one NEXUS result','governance':'roles cannot approve their own consequential actions'}

def adversarial_council(proposal:str)->dict:
    return {'proposal':proposal,'proposer':{'case':'state objective, evidence, expected value'},'critic':{'questions':['what assumption could invalidate this?','what evidence is missing?']},'red_team':{'questions':['what is the simplest counterexample?','what breaks at scale?']},'risk_reviewer':{'questions':['what side effect or governance risk exists?','is rollback available?']},'synthesis':'requires explicit comparison before recommendation'}

def prioritize_experiments(experiments:list[dict])->list[dict]:
    out=[]
    for e in experiments:
        score=float(e.get('expected_impact',0))*float(e.get('confidence',0.5))*float(e.get('learning_value',1))*float(e.get('reversibility',1))/(max(float(e.get('cost',1)),0.1))
        out.append({**e,'priority_score':score})
    return sorted(out,key=lambda x:-x['priority_score'])

def fuse_capabilities(a:str,b:str,signals:list[str])->dict:
    return {'inputs':[a,b],'signals':signals,'hypothesis':f'{a}+{b} may produce a new capability','classification':'EXPERIMENTAL','test_required':True,'production_change':False}

def discover_opportunities(signals:list[dict])->list[dict]:
    out=[]
    for s in signals:
        if s.get('evidence') and s.get('relevance',0)>=0.5:
            out.append({'signal':s,'opportunity':'investigate evidence-backed leverage','decision_options':['DO_NOTHING','RESEARCH','PREPARE','PURSUE','DEFER'],'confidence':'evidence-bounded'})
    return out

def complexity_audit(components:list[dict])->dict:
    duplicates=[]; unused=[]; overlapping=[]
    names=[c.get('name') for c in components]
    for n in set(names):
        if names.count(n)>1: duplicates.append(n)
    for c in components:
        if c.get('usage_count',0)==0: unused.append(c.get('name'))
        if len(c.get('capabilities',[]))>3: overlapping.append(c.get('name'))
    return {'duplicates':duplicates,'unused':unused,'overlapping_or_broad':overlapping,'recommendations':['review before removal','do not delete automatically'],'status':'SIMPLIFICATION_REVIEW' if duplicates or unused or overlapping else 'COHERENT'}

def self_red_team()->list[dict]:
    cases=['ambiguous request','contradictory context','stale data','missing information','failed connector','false signal','duplicate event','tool failure','permission failure','malicious-looking input','unexpected output','conflicting goals']
    return [{'case':c,'expected_behavior':['fail safely','preserve state','do not fabricate success','ask only when necessary']} for c in cases]

def safe_experiment(exp:LabExperiment)->dict:
    lower=(exp.question+' '+exp.hypothesis).lower()
    protected=next((p for p in PROTECTED if p in lower),None)
    if protected: return {'status':'REJECTED_PROTECTED_SURFACE','protected_surface':protected,'production_change':False}
    return {'status':'ELIGIBLE_FOR_SANDBOX','classification':exp.classification,'production_change':False,'promotion_requires':['benchmark','regression','governance','verification','human approval where consequential']}

if __name__=='__main__':
    import json
    print(json.dumps({'roles':compose_roles('improve repository health'),'red_team':self_red_team()},indent=2))
