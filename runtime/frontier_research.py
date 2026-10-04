#!/usr/bin/env python3
"""Controlled Frontier Research Mode for NEXUS.

Experimental architectures remain separate from production; all consequential
promotion and GitHub changes remain explicitly gated.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone
from typing import Any

PROTECTED={'security','authorization','privacy','approval','permission','credential','destructive','human override','governance'}
STATES={'PROPOSED','RUNNING','PASSED','FAILED','INCONCLUSIVE','PROMOTED','DISCARDED'}
@dataclass
class Hypothesis:
    hypothesis:str; why:str; evidence:list[str]; counter_evidence:list[str]; expected_benefit:dict; risk:dict; cost:dict; testability:float; experiment:str; status:str='PROPOSED'
    def to_dict(self): return asdict(self)
@dataclass
class Experiment:
    experiment_id:str; question:str; hypothesis:str; baseline:str; variant:str; variables:list[str]; test_cases:list[str]; results:dict; benchmark:dict; risk:dict; decision:str='PENDING'; repository_changes:list[str]=field(default_factory=list); lessons:list[str]=field(default_factory=list); status:str='PROPOSED'
    def to_dict(self): return asdict(self)

def hypothesis(h:str,why:str,evidence:list[str],counter:list[str],benefit:dict,risk:dict,cost:dict,testability:float,experiment:str): return Hypothesis(h,why,evidence,counter,benefit,risk,cost,testability,experiment).to_dict()
def register_experiment(e:Experiment)->dict:
    if e.status not in STATES: raise ValueError('invalid experiment state')
    return e.to_dict()
def update_experiment(e:Experiment,status:str,results:dict|None=None,decision:str|None=None,lessons:list[str]|None=None)->dict:
    if status not in STATES: raise ValueError('invalid experiment state')
    e.status=status
    if results is not None: e.results=results
    if decision is not None: e.decision=decision
    if lessons is not None: e.lessons=lessons
    return e.to_dict()

def objective_vector(metrics:dict)->dict:
    keys=['capability','quality','reliability','security','user_value','verifiability','maintainability','speed','complexity','cost','risk']
    return {k:float(metrics.get(k,0)) for k in keys}

def dominates(a:dict,b:dict)->bool:
    maximize=['capability','quality','reliability','security','user_value','verifiability','maintainability','speed']
    minimize=['complexity','cost','risk']
    ge=all(a.get(k,0)>=b.get(k,0) for k in maximize) and all(a.get(k,0)<=b.get(k,0) for k in minimize)
    strict=any(a.get(k,0)>b.get(k,0) for k in maximize) or any(a.get(k,0)<b.get(k,0) for k in minimize)
    return ge and strict

def pareto_frontier(candidates:list[dict])->list[dict]:
    return [c for c in candidates if not any(dominates(d,c) for d in candidates if d is not c)]

def prioritize_experiments(candidates:list[dict])->list[dict]:
    out=[]
    for c in candidates:
        gain=float(c.get('expected_capability_gain',0)); learn=float(c.get('learning_value',0)); user=float(c.get('user_value',0)); feas=float(c.get('feasibility',0)); risk=float(c.get('risk',0)); rev=float(c.get('reversibility',0)); cost=max(float(c.get('cost',1)),0.1); uncertainty=float(c.get('uncertainty_reduction',0))
        score=(gain+learn+user+feas+rev+uncertainty)/(cost*(1+risk))
        out.append({**c,'priority_score':score})
    return sorted(out,key=lambda x:-x['priority_score'])

def information_gain(candidates:list[dict])->list[dict]:
    return sorted(candidates,key=lambda c:(float(c.get('uncertainty_reduction',0))*float(c.get('testability',0)))/(max(float(c.get('cost',1)),0.1)*(1+float(c.get('risk',0)))),reverse=True)

def architecture_critic(candidate:dict)->dict:
    return {'candidate':candidate.get('name'),'easier':candidate.get('easier',[]),'harder':candidate.get('harder',[]),'new_failure_modes':candidate.get('new_failure_modes',[]),'complexity':candidate.get('complexity','unknown'),'removed':candidate.get('removed',[]),'dependencies':candidate.get('dependencies',[]),'scale_behavior':candidate.get('scale_behavior','unknown'),'failure_behavior':candidate.get('failure_behavior','unknown'),'verification_difficulty':candidate.get('verification_difficulty','unknown'),'recommendation':'REVIEW_REQUIRED'}

def architecture_red_team(candidate:dict)->dict:
    cases=['ambiguous_intent','conflicting_goals','missing_context','tool_failure','connector_failure','stale_state','duplicate_events','partial_execution','long_workflow','high_dependency_graph','contradictory_sources','malicious_input','unexpected_output']
    return {'candidate':candidate.get('name'),'cases':[{'case':c,'result':'UNTESTED','required_behavior':'fail safely, preserve state, no fabricated success'} for c in cases],'promotion_blocked_until_tested':True}

def simulate_architecture(candidate:dict,scenario:dict)->dict:
    return {'candidate':candidate.get('name'),'scenario':scenario,'classification':'SIMULATED','production_modified':False,'result':'HYPOTHETICAL_ONLY','verification':'requires real or controlled benchmark'}

def emergent_capability(components:list[str],behavior:str,evidence:list[str],repeatability:str,value:str)->dict:
    return {'components':components,'new_behavior':behavior,'evidence':evidence,'repeatability':repeatability,'potential_value':value,'classification':'EXPERIMENTAL','promotion':'requires independent verification'}

def workflow_grammar(workflow:dict)->dict:
    keys=['input','context','decision','capability','action','checkpoint','verification','recovery','output']
    return {k:workflow.get(k) for k in keys}

def stop_condition(exp:dict)->dict:
    reasons=[]
    if exp.get('hypothesis_disproven'): reasons.append('hypothesis disproven')
    if exp.get('evidence_sufficient'): reasons.append('evidence sufficient')
    if exp.get('risk_unacceptable'): reasons.append('risk unacceptable')
    if exp.get('benefit_negligible'): reasons.append('benefit negligible')
    if exp.get('resource_limit_reached'): reasons.append('resource limit reached')
    if exp.get('redundant'): reasons.append('experiment redundant')
    return {'stop':bool(reasons),'reasons':reasons,'max_effort':exp.get('max_reasonable_effort','not specified'),'escalation':exp.get('escalation','review')}

def github_evolution_plan(changes:list[str],tests:list[str],security_tests:list[str],authorized:bool=False)->dict:
    blocked=[x for x in changes if any(p in x.lower() for p in PROTECTED)]
    return {'status':'AUTHORIZED_PLAN' if authorized and not blocked else 'PREPARE_ONLY','changes':changes,'tests':tests,'security_tests':security_tests,'blocked_protected_changes':blocked,'required':['inspect repository','identify impact','implement smallest coherent change','run tests','review diff','regression','security review','commit/PR/push only if authorized','verify remote state','update project state'],'writes_performed':False}

if __name__=='__main__':
    import json
    print(json.dumps({'status':'experimental','promotion':'gated','protected_surfaces':sorted(PROTECTED)},indent=2))
