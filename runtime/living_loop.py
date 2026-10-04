#!/usr/bin/env python3
"""Living Personal Operating System loop for NEXUS.

This is an integration layer over existing Mission Composer, Ω⁸ persistence,
provider contracts, and reality controls. It is not a second orchestrator or
persistence engine.
"""
from __future__ import annotations
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import hashlib,json,time
try:
    from canonical_core import core_id
    from persistent_fabric import LocalStateStore
    from mission_composer import MissionComposer
    from local_control import capability_ceiling,provider_health
except ImportError:
    from .canonical_core import core_id
    from .persistent_fabric import LocalStateStore
    from .mission_composer import MissionComposer
    from .local_control import capability_ceiling,provider_health

def now(): return datetime.now(timezone.utc).isoformat()
def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()

def _safe_load(store_root,scope):
    if not store_root: return None
    try:
        s=LocalStateStore(store_root); snap=s.load()
        return asdict(snap) if snap and snap.scope==scope else None
    except ValueError:
        return {'status':'CORRUPT','scope':scope,'state':{},'error':'state integrity failure'}

def context_package(scope,store_root=None,mission=None):
    snap=_safe_load(store_root,scope); state=(snap or {}).get('state',{}); store=LocalStateStore(store_root) if store_root else None
    memories=[]; events=[]
    if store:
        try: memories=[asdict(x) for x in store.memories(scope)][-20:]; events=[asdict(x) for x in store.events() if x.scope==scope][-20:]
        except ValueError: pass
    m=mission or state.get('mission',{})
    return {'scope':scope,'project':{'scope':scope,'mission_id':m.get('mission_id'),'state':m.get('state'),'purpose':m.get('objective'),'next_action':m.get('next_action')},'mission':m,'decisions':m.get('decisions',[]),'memories':memories,'constraints':m.get('constraints',[]),'deadlines':m.get('deadline'),'open_loops':m.get('open_loops',[]),'recent_observations':m.get('observations',[]),'previous_failures':state.get('failures',[]),'previous_successes':[e for e in events if e.get('event_type') in {'mission_state_changed','verification_completed'}],'recent_events':events,'known':bool(snap),'reality':'OBSERVED' if snap else 'UNKNOWN','provenance':['omega8-local-state','omega10-mission-state'] if snap else ['no-persisted-state']}

def project_state(context):
    m=context.get('mission',{}); return {'scope':context.get('scope'),'purpose':m.get('objective'),'current_state':m.get('state','UNKNOWN'),'goals':m.get('success_criteria',[]),'decisions':context.get('decisions',[]),'active_missions':[m.get('mission_id')] if m.get('state') not in {None,'COMPLETED','CANCELLED'} else [],'open_loops':context.get('open_loops',[]),'risks':m.get('risks',[]),'dependencies':m.get('dependencies',[]),'important_memory':context.get('memories',[]),'recent_changes':context.get('recent_events',[]),'next_action':m.get('next_action',{}),'provenance':context.get('provenance',[])}

def knowledge_graph(context,package=None):
    m=(package or {}).get('mission',context.get('mission',{})); scope=context.get('scope'); nodes=[]; edges=[]
    def node(i,t,v=None): nodes.append({'id':i,'type':t,'value':v,'scope':scope,'provenance':['living-loop']})
    node(scope,'PROJECT',scope); node(m.get('mission_id'),'MISSION',m.get('objective')) if m.get('mission_id') else None
    for r in m.get('capability_requirements',[]): node(r,'CAPABILITY',r); edges.append({'from':m.get('mission_id'),'type':'requires','to':r,'provenance':['mission']})
    for o in m.get('observations',[]): node(o.get('id'),'OBSERVATION',o); edges.append({'from':o.get('id'),'type':'supports','to':m.get('mission_id'),'provenance':['provider-receipt']})
    if m.get('mission_id'): edges.append({'from':scope,'type':'contains','to':m.get('mission_id'),'provenance':['scope']})
    return {'nodes':[x for x in nodes if x.get('id')],'edges':edges,'scope':scope}

def memory_audit(memories,now_value=None,max_age_seconds=86400):
    current=datetime.fromisoformat((now_value or now()).replace('Z','+00:00')); out=[]
    for item in memories:
        d=asdict(item) if hasattr(item,'__dataclass_fields__') else item; freshness=d.get('freshness','unknown'); created=d.get('created_at')
        try:
            age=(current-datetime.fromisoformat(created.replace('Z','+00:00'))).total_seconds(); freshness='EXPIRED' if age>max_age_seconds*7 else 'STALE' if age>max_age_seconds else freshness
        except Exception: age=None
        out.append({'memory_id':d.get('memory_id'),'category':d.get('category'),'scope':d.get('scope'),'freshness':freshness,'age_seconds':age,'status':d.get('status','current'),'provenance':d.get('provenance',[])})
    return {'items':out,'stale_count':sum(x['freshness']=='STALE' for x in out),'expired_count':sum(x['freshness']=='EXPIRED' for x in out),'history_preserved':True}

def memory_conflicts(memories):
    groups={}
    for item in memories:
        d=asdict(item) if hasattr(item,'__dataclass_fields__') else item; key=(d.get('category'),d.get('scope')); groups.setdefault(key,[]).append(d)
    conflicts=[]
    for key,items in groups.items():
        distinct={json.dumps(x.get('content'),sort_keys=True,default=str) for x in items}
        if len(distinct)>1: conflicts.append({'key':key,'items':items,'resolution':'preserve until newer authoritative or verified evidence','winner':None})
    return {'conflicts':conflicts,'preserved':True}

def explain(package):
    m=package.get('mission',{}); loop=package.get('operating_loop',{}); return {'what_did_nexus_do':['compiled mission','resolved capability','executed bounded provider' if loop.get('external_invocations') else 'simulated unavailable provider','verified evidence','persisted state'],'why':m.get('objective'),'what_not_done':['GitHub writes','deployment','unavailable connectors'],'what_failed':m.get('risks',[]) if m.get('state') in {'FAILED','PARTIAL','BLOCKED'} else [],'what_changed':loop.get('delta',{}),'what_simulated':'SIMULATED' if loop.get('reality')=='SIMULATED' else None,'what_verified':m.get('verification',{}),'what_remains':m.get('open_loops',[]),'what_next':m.get('next_action',{}),'reality':loop.get('reality','UNKNOWN')}

def current_reality(context,provider_status=None):
    m=context.get('mission',{}); obs=m.get('observations',[]); state=m.get('state','UNKNOWN');
    return {'known':['scope','mission_state']+(['observation'] if obs else []),'current':state not in {'UNKNOWN','STALE'},'stale':[],'changed':[],'unknown':['deadline'] if not context.get('deadlines') else [],'completed':state=='COMPLETED','remaining':m.get('open_loops',[]),'blocked':m.get('risks',[]) if state in {'BLOCKED','FAILED','PARTIAL'} else [],'capabilities':provider_status or capability_ceiling(),'approvals':m.get('approvals',[]),'risks':m.get('risks',[]),'next_action':m.get('next_action',{}),'reality':context.get('reality','UNKNOWN')}

def delta(before:dict|None,after:dict|None):
    if not before and not after: return {'status':'UNKNOWN','changes':[],'before_hash':None,'after_hash':None}
    if not before: return {'status':'NEW','changes':[{'field':k,'kind':'NEW','after':v} for k,v in (after or {}).items()],'before_hash':None,'after_hash':digest(after)}
    if not after: return {'status':'REMOVED','changes':[{'field':k,'kind':'REMOVED','before':v} for k,v in before.items()],'before_hash':digest(before),'after_hash':None}
    changes=[]
    for k in sorted(set(before)|set(after)):
        if k not in after: changes.append({'field':k,'kind':'REMOVED','before':before[k]})
        elif k not in before: changes.append({'field':k,'kind':'NEW','after':after[k]})
        elif before[k]!=after[k]: changes.append({'field':k,'kind':'CHANGED','before':before[k],'after':after[k]})
    return {'status':'UNCHANGED' if not changes else 'CHANGED','changes':changes,'before_hash':digest(before),'after_hash':digest(after)}

def _stable_observation(value):
    volatile={'id','execution_id','request_id','timestamp','start_time','end_time','created_at','updated_at','fetched_at'}
    if isinstance(value,dict): return {k:_stable_observation(v) for k,v in sorted(value.items()) if k not in volatile}
    if isinstance(value,list): return [_stable_observation(v) for v in value]
    return value

def observation_delta(before:dict|None,after:dict|None):
    before=_stable_observation(before) if before else before; after=_stable_observation(after) if after else after
    result=delta(before,after)
    if result['status']=='UNCHANGED': result['status']='STALE' if after and after.get('freshness',{}).get('state')=='STALE' else 'UNCHANGED'
    result['human_status']='NO_MATERIAL_CHANGE' if result['status']=='UNCHANGED' else result['status']
    return result

def mission_reaction(d,mission):
    changed=bool(d.get('changes')); state=mission.get('state','UNKNOWN')
    return {'affected':changed and state not in {'COMPLETED','CANCELLED'},'unblocked':any(x.get('field')=='blockers' and x.get('after')==[] for x in d.get('changes',[])),'invalidated':any(x.get('field') in {'observation','capability_resolution','verification'} for x in d.get('changes',[])),'new_risks':[],'opportunities':[{'type':'reverify','reason':'meaningful state delta'}] if changed else [],'requires_reverification':changed,'action':'REPLAN' if changed and state not in {'COMPLETED','CANCELLED'} else 'NO_ACTION'}

def deadline_state(deadline,now_value=None):
    if not deadline: return {'status':'UNKNOWN','reason':'no verified deadline supplied','time_remaining_seconds':None}
    try:
        dt=datetime.fromisoformat(deadline.replace('Z','+00:00')); current=datetime.fromisoformat((now_value or now()).replace('Z','+00:00')); remaining=(dt-current).total_seconds(); status='OVERDUE' if remaining<0 else 'APPROACHING' if remaining<86400 else 'CURRENT'; return {'status':status,'time_remaining_seconds':remaining,'deadline':deadline}
    except Exception: return {'status':'UNKNOWN','reason':'malformed deadline','deadline':deadline}

def followups(mission,provider_status=None):
    state=mission.get('state','UNKNOWN'); loops=mission.get('open_loops',[]); out=[]
    if state in {'PARTIAL','FAILED','BLOCKED'}: out.append({'type':'RECOVERY','title':'retry or replan blocked mission','status':'OPEN','next_action':mission.get('next_action',{})})
    if state=='COMPLETED' and loops: out.append({'type':'REVIEW','title':'review mission recommendation','status':'OPEN','next_action':mission.get('next_action',{})})
    if provider_status and any(v.get('health')=='DEGRADED' for v in provider_status.get('providers',{}).values() if isinstance(v,dict)): out.append({'type':'PROVIDER_RECOVERY','title':'retry after provider recovery','status':'WAITING'})
    return out

FAILURE_CLASSES={'timeout':'TRANSIENT','network':'NETWORK','provider':'PROVIDER','unauthorized':'AUTHORIZATION','invalid':'INVALID_INPUT','corrupt':'STATE','verification':'VERIFICATION'}
def classify_failure(error):
    text=str(error).lower(); kind=next((k for k in FAILURE_CLASSES if k in text),'UNKNOWN'); classification=FAILURE_CLASSES.get(kind,'UNKNOWN')
    retryable=classification in {'TRANSIENT','NETWORK','PROVIDER'}
    return {'classification':classification,'retryable':retryable,'recoverability':'RETRY' if retryable else 'REPLAN_OR_BLOCK','lesson':'bounded retry only' if retryable else 'do not repeat without new evidence','prevention':'attempt and time budget'}

@dataclass
class RetryBudget:
    max_attempts:int=2
    time_budget_seconds:int=60
    risk_budget:str='LOW'
    verification_required:bool=True
    attempts:int=0
    def permit(self): return self.attempts<self.max_attempts
    def consume(self): self.attempts+=1; return self.permit()

@dataclass
class CircuitBreaker:
    provider:str
    failures:int=0
    threshold:int=3
    state:str='CLOSED'
    last_failure:str|None=None
    def record_failure(self):
        self.failures+=1; self.last_failure=now(); self.state='OPEN' if self.failures>=self.threshold else 'CLOSED'; return asdict(self)
    def record_success(self): self.failures=0; self.state='CLOSED'; return asdict(self)

def provider_metrics(bundle,provider='github-read'):
    receipt=bundle.get('receipt',{}); start=receipt.get('start_time'); end=receipt.get('end_time'); latency=None
    try: latency=(datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()
    except Exception: pass
    ok=receipt.get('status') in {'EXECUTED','SUCCESS'} and receipt.get('side_effects') is False
    cb=CircuitBreaker(provider); cb.record_success() if ok else cb.record_failure()
    return {'provider':provider,'availability':'AVAILABLE' if receipt else 'UNKNOWN','calls':1 if receipt else 0,'failures':0 if ok else 1,'latency_seconds':latency,'verification_success':bool(bundle.get('verification',{}).get('verification',{}).get('verification_state')=='VERIFIED'),'last_success':receipt.get('end_time') if ok else None,'limitations':['single observed execution; no global quality ranking'],'circuit_breaker':asdict(cb)}

def lineage(intent,package):
    m=package.get('mission',{}); ex=package.get('execution',{}); pb=ex.get('provider_bundle',{}); rec=pb.get('receipt',{}); obs=pb.get('observation',{}); return [{'type':'intent','id':digest(intent),'value':intent},{'type':'mission','id':m.get('mission_id')},{'type':'workflow','id':(m.get('workflow_ids') or [None])[0]},{'type':'task','id':'observe-repository'},{'type':'capability','id':'cap-repository-read'},{'type':'provider','id':rec.get('provider')},{'type':'request','id':rec.get('request_id')},{'type':'execution','id':rec.get('execution_id')},{'type':'observation','id':obs.get('id')},{'type':'verification','id':(m.get('verification',{}).get('completion_verification',{}) or {}).get('verification_id')},{'type':'state','id':m.get('state')},{'type':'memory','id':None}]

def learning(intent,package,d):
    state=package.get('mission',{}).get('state'); expected='verified repository health mission'; actual='completed and persisted' if state=='COMPLETED' else state; return {'lesson_id':core_id('lesson'),'method':'Mission Composer plus real GitHub READ and independent verification','context':intent,'expected':expected,'actual':actual,'delta':'none' if state=='COMPLETED' else 'mission incomplete','lesson':'real bounded READ plus structured verification is the highest-confidence path','future_adjustment':'reuse the same evidence boundary; reverify after meaningful delta','reality':'INFERRED','evidence':[x.get('id') for x in lineage(intent,package) if x.get('id')]}

def run_operating_loop(intent,scope='Themeta-verse/Nexus',mode='REAL_READ',store_root=None,previous_state=None):
    before=previous_state or _safe_load(store_root,scope); context=context_package(scope,store_root); reality=current_reality(context,provider_health()); composer=MissionComposer(); package=composer.execute(composer.compose(intent,scope,mode,store_root=store_root),store_root,mode); after=package.get('mission',{}); d=delta((before or {}).get('state',{}).get('mission') if before else None,after); previous_obs=((before or {}).get('state',{}).get('operating_loop',{}).get('observation') if before else None) or (((before or {}).get('state',{}).get('mission',{}).get('observations') or [{}])[-1] if before else None); current_obs=package.get('execution',{}).get('provider_bundle',{}).get('observation') if package.get('execution') else None; od=observation_delta(previous_obs,current_obs); reaction=mission_reaction(d,after); dl=deadline_state(after.get('deadline')); pmetrics=provider_metrics(package.get('execution',{}).get('provider_bundle',{}), 'github-read') if package.get('execution') else {'provider':'none','availability':'UNKNOWN'}; follow=followups(after,provider_health()); lesson=learning(intent,package,d); line=lineage(intent,package)
    loop={'phases':['UNDERSTAND','CONTEXTUALIZE','PLAN','PREPARE','GOVERN','EXECUTE','OBSERVE','VERIFY','UPDATE','REMEMBER','LEARN','CONTINUE'],'completed_phases':['UNDERSTAND','CONTEXTUALIZE','PLAN','PREPARE','GOVERN','EXECUTE','OBSERVE','VERIFY','UPDATE','REMEMBER','LEARN'],'current_phase':'CONTINUE','context':context,'project_state':project_state({'scope':scope,'mission':after,'memories':context.get('memories',[]),'open_loops':after.get('open_loops',[]),'recent_events':context.get('recent_events',[]),'provenance':context.get('provenance',[])}),'knowledge_graph':knowledge_graph({'scope':scope,'mission':after},package),'memory_audit':memory_audit(context.get('memories',[])),'memory_conflicts':memory_conflicts(context.get('memories',[])),'before':before,'after':after,'observation':current_obs,'observation_delta':od,'current_reality':reality,'delta':d,'mission_reaction':reaction,'deadline':dl,'followups':follow,'provider_metrics':pmetrics,'lineage':line,'learning':lesson,'explanation':explain(package),'next_action':after.get('next_action',{}),'writes_performed':False,'external_invocations':package.get('external_invocations',0),'reality':after.get('reality','UNKNOWN')}
    if store_root:
        store=LocalStateStore(store_root); store.remember('PROCEDURAL_LESSON',scope,lesson,'living-loop','MEDIUM','INFERRED',['mission','delta','verification'],'current'); store.append_event('operating_loop_completed',{'mission_id':after.get('mission_id'),'delta':d,'reaction':reaction,'lineage':line},scope,['living-loop'],f"operating-loop:{after.get('mission_id')}"); store.save({'mission':after,'operating_loop':loop,'context':context,'learning':lesson,'followups':follow,'execution':package.get('execution',{}),'verification':package.get('verification',{}),'tasks':package.get('tasks',[]),'replay':package.get('replay',{}),'reality_audit':package.get('reality_audit',{})},scope,['living-loop','mission-composer'],{'delta':d['status'],'phase':'CONTINUE','observation_delta':od.get('status')},parent_state=(before or {}).get('state_id'))
    return {'status':'COMPLETED' if after.get('state')=='COMPLETED' else after.get('state','UNKNOWN'),'mission':package,'operating_loop':loop,'writes_performed':False,'connector_expansion':False,'observation_delta':od}
