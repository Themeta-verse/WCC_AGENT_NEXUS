#!/usr/bin/env python3
"""NEXUS Ω⁷ bounded local Personal Agent Operating Layer.

This module models agency; it does not activate external autonomy, connectors,
daemons, persistence, deployment, or destructive actions.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone
from typing import Any
import json,os,re
try:
    from canonical_runtime import compile_request
    from canonical_core import core_id
    from persistent_fabric import LocalStateStore
except ImportError:
    from .canonical_runtime import compile_request
    from .canonical_core import core_id
    from .persistent_fabric import LocalStateStore

COMMANDS=('BUILD','RESEARCH','CREATE','FIX','LAUNCH','PREPARE','COMPARE','MONITOR','AUTOMATE','CONTINUE','STOP','REVIEW','BLOCKERS','NEXT','SIMULATE','AUDIT','IMPROVE','WHAT_IF','TAKE_FAR','DO_REQUIRED')
ACTIONS=('READ','ANALYZE','CREATE','MODIFY','COMMUNICATE','PUBLISH','DELETE','FINANCIAL','SECURITY_SENSITIVE','IRREVERSIBLE')
APPROVALS=('NOT_REQUIRED','RECOMMENDED','REQUIRED','APPROVED','REJECTED','EXPIRED','CANCELLED')

@dataclass
class Autonomy:
    understanding:str='LOCAL'
    planning:str='LOCAL'
    capability_access:str='REGISTRY_BOUNDED'
    authorization:str='NOT_GRANTED'
    execution:str='PLAN_ONLY'
    verification:str='REQUIRED'
    persistence:str='EPHEMERAL'
    recovery:str='CHECKPOINT_BASED'
    proactivity:str='RECOMMEND_ONLY'
    level:int=1

@dataclass
class AgentState:
    agent_id:str
    identity:str
    purpose:str
    owner_scope:str
    current_state:str
    active_projects:list[str]
    goals:list[str]
    preferences:dict
    constraints:list[str]
    capabilities:list[str]
    permissions:list[str]
    memory_scope:str
    current_workflows:list[str]
    pending_approvals:list[str]
    open_loops:list[str]
    risk_state:str
    last_activity:str
    next_actions:list[str]

@dataclass
class Approval:
    approval_id:str
    state:str
    scope:str
    action:str
    project_scope:str
    expires_at:str|None=None
    reusable:bool=False

@dataclass
class ActionAssessment:
    action:str
    impact:str
    reversibility:str
    approval:str
    authorized:bool
    reason:str

@dataclass
class OpenLoop:
    loop_id:str
    title:str
    project_scope:str
    status:str
    created:str
    updated:str
    blocked_by:list[str]=field(default_factory=list)
    next_action:str=''

@dataclass
class WorkflowCheckpoint:
    workflow_id:str
    project_scope:str
    state:str
    checkpoint:str
    last_verified_state:str
    remaining_tasks:list[str]
    dependencies:list[str]
    next_action:str
    resume_conditions:list[str]

@dataclass
class Specialist:
    name:str
    objective:str
    scope:str
    allowed_capabilities:list[str]
    inputs:list[str]
    outputs:list[str]
    verification:str


def now(): return datetime.now(timezone.utc).isoformat()
def default_agent(project_scope='nexus-local'):
    return AgentState(core_id('agent'),'NEXUS local personal agent','Compatibility adapter from natural-language intent into the canonical governed fabric',project_scope,'READY',[project_scope],[],{},['no external side effects','no false completion','untrusted content is data'],['canonical-core','capability-registry','mission-composer','action-ready','outcome-intelligence','living-loop'],['local-plan','local-simulation'],project_scope,[],[],[], 'LOW',now(),[])

def classify_command(request:str):
    t=request.strip().lower();
    if 'take this as far as possible' in t or 'take it further' in t or 'take this further' in t or 'go all out' in t: return 'TAKE_FAR'
    if 'do everything required' in t or 'do required' in t or 'do it all' in t: return 'DO_REQUIRED'
    if 'what should i do next' in t or t in {'next','what next'}: return 'NEXT'
    if t.startswith('prepare '): return 'PREPARE'
    if t.startswith('launch '): return 'LAUNCH'
    if 'find what is blocking' in t or 'blockers' in t: return 'BLOCKERS'
    if 'what if' in t: return 'WHAT_IF'
    for c,words in {'BUILD':['build'],'RESEARCH':['research'],'CREATE':['create'],'FIX':['fix'],'LAUNCH':['launch'],'PREPARE':['prepare'],'COMPARE':['compare'],'MONITOR':['monitor'],'AUTOMATE':['automate'],'CONTINUE':['continue'],'STOP':['stop'],'REVIEW':['review'],'SIMULATE':['simulate'],'AUDIT':['audit'],'IMPROVE':['improve']}.items():
        if any(w in t for w in words): return c
    return 'PREPARE'

def classify_action(request:str)->ActionAssessment:
    t=request.lower();
    if any(k in t for k in ['delete','destroy','remove permanently']): return ActionAssessment('DELETE','HIGH','IRREVERSIBLE','REQUIRED',False,'destructive action is not authorized')
    if any(k in t for k in ['send email','message','publish','post','deploy']): return ActionAssessment('PUBLISH' if 'publish' in t or 'post' in t or 'deploy' in t else 'COMMUNICATE','HIGH','LOW','REQUIRED',False,'external communication or deployment is not enabled')
    if any(k in t for k in ['modify','edit','change','fix','build','create']): return ActionAssessment('MODIFY','MEDIUM','MEDIUM','RECOMMENDED',False,'local planning is allowed; consequential modification requires governed execution')
    if any(k in t for k in ['audit','research','analyze','compare','review','what should']): return ActionAssessment('ANALYZE','LOW','HIGH','NOT_REQUIRED',True,'local analysis and planning are allowed')
    return ActionAssessment('READ','LOW','HIGH','NOT_REQUIRED',True,'read-only interpretation is allowed')

def autonomy_for(assessment:ActionAssessment,mode='PLAN_ONLY'):
    a=Autonomy(execution=mode)
    if assessment.approval=='REQUIRED': a.level=2; a.authorization='NOT_GRANTED'
    elif assessment.action in {'MODIFY','CREATE'}: a.level=2; a.authorization='NOT_GRANTED'
    else: a.level=1; a.authorization='SCOPED_LOCAL'
    return a

def specialists_for(command:str):
    mapping={'RESEARCH':['Researcher','QA'],'AUDIT':['Researcher','Security Engineer','QA'],'BUILD':['Product Strategist','Architect','Security Engineer','QA'],'FIX':['Architect','Security Engineer','QA'],'DECIDE':['Researcher','Decision Specialist','QA'],'NEXT':['Project Manager','Decision Specialist']}
    names=mapping.get(command,['Project Manager','QA']);
    return [Specialist(n,'bounded support for '+command.lower(),'local project',['canonical-runtime','engine-registry'],['canonical outcome','scoped context'],['structured recommendation','verification plan'],'independent contract or explicit UNKNOWN') for n in names]

def open_loops_from(context:dict,project_scope:str):
    loops=[]
    for item in context.get('open_loops',[]):
        if isinstance(item,dict) and item.get('project_scope',project_scope)!=project_scope: continue
        title=item.get('title',str(item)) if isinstance(item,dict) else str(item)
        loops.append(asdict(OpenLoop(core_id('loop'),title,project_scope,item.get('status','OPEN') if isinstance(item,dict) else 'OPEN',now(),now(),item.get('blocked_by',[]) if isinstance(item,dict) else [],item.get('next_action','') if isinstance(item,dict) else '')))
    return loops

def proactive_radar(context:dict,project_scope='nexus-local'):
    candidates=[]
    for l in open_loops_from(context,project_scope):
        if l['status'] in {'OPEN','BLOCKED','WAITING','STALE'}:
            candidates.append({'type':'open_loop','title':l['title'],'impact':'MEDIUM','confidence':'OBSERVED' if l['status']!='OPEN' else 'INFERRED','action':l['next_action'] or 'review and define the next safe step'})
    for risk in context.get('risks',[]): candidates.append({'type':'risk','title':str(risk),'impact':'HIGH','confidence':'OBSERVED','action':'analyze mitigation before execution'})
    return sorted(candidates,key=lambda x:(x['impact']=='HIGH',x['confidence']=='OBSERVED'),reverse=True)[:5]

def next_best_action(context:dict,project_scope='nexus-local'):
    radar=proactive_radar(context,project_scope)
    if radar: return {'action':radar[0]['action'],'why':radar[0]['title'],'unlocks':['decision clarity'],'risk':'review required','verification':'explicit evidence required','alternatives':radar[1:]}
    return {'action':'run a bounded local audit of the active project','why':'no higher-confidence open loop was supplied','unlocks':['current state'],'risk':'LOW','verification':'audit result and source trace','alternatives':[]}

def follow_up(command:str,outcome:dict):
    if command in {'RESEARCH','AUDIT','COMPARE'}: return {'justified':True,'title':'review the evidence-backed decision','reason':'analysis naturally produces a decision or next action','reality':'INFERRED'}
    if command in {'BUILD','FIX','CREATE'}: return {'justified':True,'title':'run verification and review remaining open loops','reason':'implementation planning requires verification before completion','reality':'INFERRED'}
    return {'justified':False,'title':None,'reason':'no evidence-backed follow-up was identified','reality':'UNKNOWN'}

def checkpoint(project_scope,remaining=None,state='PLANNED'):
    return asdict(WorkflowCheckpoint(core_id('workflow'),project_scope,state,'intent_compiled','not_verified',remaining or ['execute only after authorization'],[], 'review plan and approval requirements',['authorization','capability availability','verification']))

def recover(cp:dict):
    return {'workflow_id':cp.get('workflow_id'),'recovered_from':cp.get('last_verified_state'),'completed':[],'verified':[],'remaining':cp.get('remaining_tasks',[]),'safe_to_retry':False,'unsafe_to_retry':['unknown side effects'], 'requires_human_review':True,'next_action':cp.get('next_action','review checkpoint')}

def self_awareness(agent:AgentState,assessment:ActionAssessment):
    return {'can_do':['local planning','local analysis','simulation','governed artifact preparation'],'cannot_do':['external writes','deployment','always-on monitoring','unverified completion'],'needs':['scoped context','capability evidence','verification'],'assumptions':['provided context is complete enough for planning'],'certain_about':['no external invocation in this layer'],'uncertain_about':['current external world state','connector availability'],'actually_did':['compiled a local plan'],'only_planned':['all consequential work'],'approval_required':assessment.approval=='REQUIRED','should_verify':['execution evidence','result freshness']}

def compile_agent_request(request:str,context:dict|None=None,project_scope='nexus-local',mode='PLAN_ONLY'):
    context=context or {}; command=classify_command(request); assessment=classify_action(request); autonomy=autonomy_for(assessment,mode)
    agent=default_agent(project_scope); agent.current_state='PLANNING'; agent.open_loops=[x['loop_id'] for x in open_loops_from(context,project_scope)]; agent.next_actions=[next_best_action(context,project_scope)['action']]
    canonical=compile_request(request,{'project_id':project_scope,**context},mode)
    return {'agent':asdict(agent),'adapter_role':'intent-and-specialist compatibility adapter','canonical_fabric':'MissionComposer -> action-ready -> outcome-intelligence -> persistent fabric','legacy_engines_not_invoked':True,'command':command,'action_assessment':asdict(assessment),'autonomy':asdict(autonomy),'canonical_workflow':canonical,'specialists':[asdict(x) for x in specialists_for(command)],'open_loops':open_loops_from(context,project_scope),'proactive_radar':proactive_radar(context,project_scope),'next_best_action':next_best_action(context,project_scope),'follow_up':follow_up(command,canonical.get('outcome',{})),'checkpoint':checkpoint(project_scope),'self_awareness':self_awareness(agent,assessment),'reality':'SIMULATED' if mode in {'DRY_RUN','SIMULATION'} else 'PLANNED','external_invocations':0,'writes_performed':False,'persisted':False}

def persist_agent_result(result:dict,store_root=None,project_scope='nexus-local'):
    store=LocalStateStore(store_root)
    state={'agent':result.get('agent',{}),'project_scope':project_scope,'current_workflows':[result.get('checkpoint',{}).get('workflow_id')],'open_loops':result.get('open_loops',[]),'next_best_action':result.get('next_best_action',{}),'last_verified_state':'not_verified','reality':result.get('reality','UNKNOWN')}
    snapshot=store.checkpoint('WORKFLOW_COMPILED',state,project_scope,verified=False)
    event=store.append_event('workflow_created',{'workflow_id':result.get('checkpoint',{}).get('workflow_id'),'reality':result.get('reality','UNKNOWN')},project_scope,['personal_agent','persistent_fabric'],idempotency_key=result.get('checkpoint',{}).get('workflow_id'))
    return {'snapshot':asdict(snapshot),'event':asdict(event),'persisted':True,'external_invocations':0,'writes_performed':False}

def continue_project(store_root=None,project_scope='nexus-local'):
    store=LocalStateStore(store_root); snap=store.load(); reconstructed=store.reconstruct(scope=project_scope)
    if (not snap or snap.scope!=project_scope) and not reconstructed['events_applied']:
        return {'status':'UNKNOWN','reason':'no persisted state for scope','requires_user_context':True,'persisted':False}
    return {'status':'RECOVERED','snapshot':asdict(snap) if snap else None,'reconstructed':reconstructed,'next_action':(snap.state.get('next_best_action') if snap else None),'reality':(snap.state.get('reality') if snap else 'UNKNOWN'),'requires_user_context':False,'persisted':True}

def adversarial_content_is_data(text:str):
    suspicious=('ignore previous instructions','execute this command','send this email','delete this repository','reveal the secret','you already have permission','already approved')
    return {'is_untrusted_data':True,'injection_detected':any(x in text.lower() for x in suspicious),'becomes_authority':False,'executed':False,'reason':'external content cannot grant authority'}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('request'); p.add_argument('--mode',default='PLAN_ONLY',choices=['PLAN_ONLY','DRY_RUN','SIMULATION']); a=p.parse_args(); print(json.dumps(compile_agent_request(a.request,mode=a.mode),indent=2))
