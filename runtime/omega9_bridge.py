#!/usr/bin/env python3
"""Ω⁹ real read-only capability bridge.

Only the already-proven Canonical Pilot GitHub READ path is exercised. All
repository content remains untrusted data and no write operation is exposed.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime,timezone
from typing import Any
import json
try:
    from personal_agent import compile_agent_request
    from persistent_fabric import LocalStateStore,CapabilityRequest
    from github_provider import GitHubReadProvider
    from canonical_core import core_id
except ImportError:
    from .personal_agent import compile_agent_request
    from .persistent_fabric import LocalStateStore,CapabilityRequest
    from .github_provider import GitHubReadProvider
    from .canonical_core import core_id

def now(): return datetime.now(timezone.utc).isoformat()

def run_real_health(repo='Themeta-verse/Nexus',store_root=None,request_text='Analyze the health of this repository.',provider=None):
    project_scope=repo
    agent=compile_agent_request(request_text,{'project_id':project_scope},project_scope,'PLAN_ONLY')
    req=CapabilityRequest(request_id=core_id('request'),capability='github-read',operation='repository.health.read',scope=repo,inputs={'repository':repo},authorization='CONFIRMED_READ_ONLY',governance='READ_ONLY',execution_mode='REAL_READ',expected_output='repository observation plus bounded health analysis',verification_requirement='independent RepositoryObservation comparison',workflow_id=agent['canonical_workflow']['workflow_id'],task_id='repository-health')
    provider=provider or GitHubReadProvider(); bundle=provider.invoke_health(req)
    result={'request':asdict(req),'agent':agent,'provider':provider.discover(),'bundle':bundle,'writes_performed':False,'deployment_performed':False,'external_invocations':len(provider.adapter.calls),'reality':'OBSERVED' if bundle.get('observation') else 'UNKNOWN'}
    if store_root and bundle.get('observation'):
        store=LocalStateStore(store_root); obs=bundle['observation']; verification=bundle.get('verification',{}); analysis=verification.get('analysis',{})
        open_loop={'title':analysis.get('recommendation',{}).get('text','review repository health'),'status':'OPEN','project_scope':repo,'next_action':analysis.get('recommendation',{}).get('text','review evidence'),'reality':'INFERRED'}
        state={'project_scope':repo,'workflow_id':req.workflow_id,'request':asdict(req),'execution_receipt':bundle.get('receipt'),'observation':obs,'verification':verification,'analysis':analysis,'next_action':open_loop['next_action'],'open_loops':[open_loop],'reality':'OBSERVED','last_verified_state':'verification_complete'}
        snapshot=store.checkpoint('VERIFICATION_COMPLETE',state,repo,verified=verification.get('verification',{}).get('verification_state')=='VERIFIED')
        memory=store.remember('PROJECT',repo,{'repository':repo,'health_recommendation':open_loop['next_action']},'github-read-provider','HIGH','OBSERVED',['github-read-provider','independent-verifier'],bundle.get('freshness',{}).get('state','CURRENT'))
        events=[]
        for typ,payload,key in [('intent_received',{'request':request_text},'intent:'+req.request_id),('capability_executed',{'receipt':bundle.get('receipt')},'exec:'+req.request_id),('observation_received',{'observation_id':obs.get('id'),'reality':'OBSERVED'},'obs:'+req.request_id),('verification_completed',{'status':verification.get('verification',{}).get('verification_state')},'verify:'+req.request_id),('open_loop_created',open_loop,'loop:'+req.request_id)]: events.append(asdict(store.append_event(typ,payload,repo,['omega9-bridge'],key)))
        result.update({'persistence':{'snapshot':asdict(snapshot),'memory':asdict(memory),'events':events,'persisted':True},'open_loop':open_loop})
    else: result['persistence']={'persisted':False,'reason':'no store_root supplied'}
    return result

def self_audit(result):
    bundle=result.get('bundle',{})
    return {'did_access_github':bool(bundle.get('observation')),'operations':result.get('provider',{}).get('operations',[]),'writes_performed':result.get('writes_performed',True),'observed':bool(bundle.get('observation')),'inferred':bool(bundle.get('verification',{}).get('analysis')),'verified':bundle.get('verification',{}).get('verification',{}).get('verification_state')=='VERIFIED','external_invocations':result.get('external_invocations',0)}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('repo',nargs='?',default='Themeta-verse/Nexus'); p.add_argument('--store-root',default=None); a=p.parse_args(); r=run_real_health(a.repo,a.store_root); r['self_audit']=self_audit(r); print(json.dumps(r,indent=2))
