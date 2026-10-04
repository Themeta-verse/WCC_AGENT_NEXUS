#!/usr/bin/env python3
"""NEXUS Ω⁵ canonical integration runtime.

This is connective tissue, not another engine. It composes Ω³ planning and Ω⁴
reconciliation into one local result envelope. It never invokes connectors or
performs external writes.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict,field
from typing import Any
import json
try:
    from omega3_transcendence import compile_omega3
    from omega4_reality import reconcile
    from canonical_core import core_id,utc_now
    from engine_registry import default_registry
except ImportError:
    from .omega3_transcendence import compile_omega3
    from .omega4_reality import reconcile
    from .canonical_core import core_id,utc_now
    from .engine_registry import default_registry

@dataclass
class CanonicalWorkflowResult:
    workflow_id:str
    request:str
    intent:dict
    outcome:dict
    plan:dict
    preparation:dict
    execution:dict
    observation:dict
    verification:dict
    reality_reconciliation:dict
    governance:dict
    status:str
    reality:str
    action_state:str
    external_invocations:int=0
    writes_performed:bool=False
    persisted:bool=False
    next_action:str=""
    conflicts:list[dict]=field(default_factory=list)
    provenance:list[str]=field(default_factory=list)


def _snapshot(plan:dict, mode:str):
    selected=plan.get('omega2',{}).get('capability_selection',{}).get('selected',[])
    return {
      'capability_status':'AVAILABLE' if selected else 'UNAVAILABLE',
      'workflow_status':'PLANNED',
      'execution_status':'NOT_EXECUTED',
      'verification_status':'UNKNOWN',
      'reality':'SIMULATED' if mode in {'SIMULATION','DRY_RUN'} else 'PLANNED',
      'final_status':'PREPARED' if mode in {'SIMULATION','DRY_RUN'} else 'PLANNED',
      'authorized':False,
      'verification_target':False,
      'workflow_operation':'READ',
      'governance_approved':False,
      'external_observation':False,
      'source':'local-plan',
      'scope':plan.get('omega2',{}).get('project_state',{}).get('project_id','unscoped'),
      'cross_project_context':False,
      'external_content_instruction':False,
    }


def compile_request(request:str,context:dict|None=None,mode:str='PLAN_ONLY')->dict:
    if mode not in {'PLAN_ONLY','DRY_RUN','SIMULATION'}:
        raise ValueError('canonical local runtime permits PLAN_ONLY, DRY_RUN, or SIMULATION only')
    plan=compile_omega3(request,context=context or {},mode=mode)
    snap=_snapshot(plan,mode); rr=reconcile(snap); conflicts=rr['consistency']['conflicts']
    status='BLOCKED' if conflicts else ('PREPARED' if mode in {'DRY_RUN','SIMULATION'} else 'PLANNED')
    reality='SIMULATED' if mode in {'DRY_RUN','SIMULATION'} else 'PLANNED'
    action='resolve knowledge gaps before authorization or execution'
    registry=default_registry(); selection=registry.select(request)
    return asdict(CanonicalWorkflowResult(
      workflow_id=core_id('workflow'),request=request,
      intent=plan['intent_amplification'],outcome=plan['omega2']['outcome'],plan=plan,
      preparation={'status':'PREPARED' if mode!='PLAN_ONLY' else 'PLANNED','mode':mode,'side_effects':False},
      execution={'status':'NOT_EXECUTED','authorized':False,'external_invocations':0,'writes_performed':False},
      observation={'status':'NOT_OBSERVED','source':None,'reality':'UNKNOWN'},
      verification={'status':'UNKNOWN','independent':False,'target':None},
      reality_reconciliation=rr,governance=plan['governance'],status=status,reality=reality,
      action_state='PREPARED' if mode in {'DRY_RUN','SIMULATION'} else 'PLANNED',
      next_action=action,conflicts=conflicts,provenance=['omega3_transcendence','omega2_intelligence','omega4_reality','canonical_core'])) | {'engine_selection':selection,'engine_graph':registry.graph(),'engine_health':registry.health()}


def validate_envelope(result:dict)->dict:
    errors=[]
    if result.get('execution',{}).get('status')!='NOT_EXECUTED' and result.get('execution',{}).get('external_invocations',0)==0: errors.append('execution status lacks invocation evidence')
    if result.get('reality')=='SIMULATED' and result.get('status')=='COMPLETED': errors.append('simulation cannot be completed reality')
    if result.get('status') in {'COMPLETED','SUCCEEDED'} and result.get('verification',{}).get('status')!='VERIFIED': errors.append('completion lacks verification')
    if result.get('writes_performed'): errors.append('canonical local runtime must not write')
    return {'valid':not errors,'errors':errors}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('request'); p.add_argument('--mode',default='PLAN_ONLY',choices=['PLAN_ONLY','DRY_RUN','SIMULATION']); a=p.parse_args(); print(json.dumps(compile_request(a.request,mode=a.mode),indent=2))
