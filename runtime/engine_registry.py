#!/usr/bin/env python3
"""NEXUS Ω⁶ Engine Registry.

Describes and selects local engines without invoking them. The registry is a
planning/diagnostic boundary; execution remains governed by canonical_runtime.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
from pathlib import Path
from typing import Any
import json,os
try:
    from canonical_core import core_id
except ImportError:
    from .canonical_core import core_id

@dataclass(frozen=True)
class EngineContract:
    name:str
    purpose:str
    module:str
    inputs:list[str]
    outputs:list[str]
    capabilities:list[str]
    dependencies:list[str]
    risk:str
    governance_requirements:list[str]
    execution_modes:list[str]
    verification_requirements:list[str]
    status:str
    health:str
    limitations:list[str]
    canonical:bool=False

class EngineRegistry:
    def __init__(self,engines:list[EngineContract]|None=None): self.engines={e.name:e for e in (engines or [])}
    def register(self,e:EngineContract): self.engines[e.name]=e
    def get(self,name): return self.engines.get(name)
    def all(self): return list(self.engines.values())
    def health(self): return {e.name:{'status':e.status,'health':e.health,'limitations':e.limitations} for e in self.all()}
    def graph(self):
        nodes=[]; edges=[]
        for e in self.all():
            nodes.append({'id':e.name,'type':'engine','status':e.status,'health':e.health,'canonical':e.canonical})
            for cap in e.capabilities: edges.append({'from':cap,'to':e.name,'type':'capability_to_engine'})
            edges.append({'from':e.name,'to':f'contract:{e.name}','type':'engine_to_contract'})
            edges.append({'from':f'contract:{e.name}','to':f'workflow:{e.name}','type':'contract_to_workflow'})
            edges.append({'from':f'workflow:{e.name}','to':f'task:{e.name}','type':'workflow_to_task'})
            edges.append({'from':f'task:{e.name}','to':f'verification:{e.name}','type':'task_to_verification'})
        return {'nodes':nodes,'edges':edges,'relationships_invented':False}
    def select(self,objective:str):
        text=objective.lower(); chosen=[]; legacy=[]
        def add(n,target=chosen):
            if n in self.engines and n not in target: target.append(n)
        for name in ('canonical-runtime','capability-registry','canonical-core','mission-composer','action-ready','outcome-intelligence','living-loop'):
            add(name)
        if any(k in text for k in ['research','compare','evidence','investigate']): add('frontier-research',legacy); add('external-intelligence',legacy)
        if any(k in text for k in ['build','create','launch','fix','engineer','product']): add('forge',legacy)
        if any(k in text for k in ['strategy','decide','improve','next','opportunity']): add('omega3-transcendence',legacy)
        if any(k in text for k in ['audit','verify','reality','consistent','what changed','trust']): add('omega4-reality',legacy)
        if any(k in text for k in ['project','context','personal','continue','recover']): add('os',legacy)
        selected=chosen + [x for x in legacy if x not in chosen]
        return {'objective':objective,'selected':selected,'legacy_reference':legacy,'minimum_sufficient':True,'invocation_performed':False,'external_invocations':0,'selection_reality':'PLANNED','canonical_path':['canonical-runtime','capability-registry','mission-composer','action-ready','outcome-intelligence','living-loop'],'limitations':['selection does not execute engines','legacy references are diagnostic only and never invoked by this registry','availability is based on local registry evidence only']}

def _contract(name,purpose,module,capabilities,status='EXPERIMENTAL',health='healthy',risk='MEDIUM',limitations=None,inputs=None,outputs=None,canonical=False):
    return EngineContract(name,purpose,module,inputs or ['Canonical request'],outputs or ['Structured result'],capabilities,['canonical-core'],risk,['canonical-governance','independent-verification'],['PLAN_ONLY','DRY_RUN','SIMULATION'],['explicit target','provenance','state consistency'],status,health,limitations or ['No external side effects in local mode'],canonical)

def default_registry(root=None):
    root=root or os.getenv('NEXUS_PRODUCT_ROOT') or str(Path(__file__).resolve().parents[1])
    r=EngineRegistry(); p=Path(root)/'runtime'
    specs=[
      ('canonical-runtime','Canonical local orchestration boundary','canonical_runtime.py',['orchestration','workflow-compilation'],'INTEGRATED'),
      ('canonical-core','Typed contracts and governance','canonical_core.py',['contracts','governance'],'INTEGRATED'),
      ('capability-registry','Capability discovery and safe selection','capability_registry.py',['capability-intelligence'],'INTEGRATED'),
      ('omega2-intelligence','Intent, outcome, knowledge gaps, capability planning','omega2_intelligence.py',['intent','outcome','planning'],'EXPERIMENTAL'),
      ('omega3-transcendence','Meta-cognition and strategy search','omega3_transcendence.py',['strategy','opportunity','critique'],'EXPERIMENTAL'),
      ('omega4-reality','Reality reconciliation and invariants','omega4_reality.py',['reality','consistency','verification'],'EXPERIMENTAL'),
      ('convergence','Canonical event/state convergence','convergence_engine.py',['state','events','recovery'],'EXPERIMENTAL'),
      ('forge','Product compilation and build planning','forge_engine.py',['product','architecture','security'],'EXPERIMENTAL'),
      ('frontier-research','Hypothesis and architecture research','frontier_research.py',['research','experiments'],'EXPERIMENTAL'),
      ('os','Personal operating graph and project state','os_engine.py',['project','context','memory'],'EXPERIMENTAL'),
      ('closed-loop','Plan-act-observe-verify-replan contracts','closed_loop.py',['execution','recovery'],'EXPERIMENTAL'),
      ('adaptive','Evidence-based adaptive methods','adaptive_intelligence.py',['learning','method-selection'],'EXPERIMENTAL'),
      ('predictive','Prediction and foresight heuristics','predictive_engine.py',['prediction','foresight'],'EXPERIMENTAL'),
      ('external-intelligence','External evidence interfaces','external_intelligence.py',['external-evidence','repository-observation'],'PARTIAL'),
      ('mission-composer','Capability-first real provider mission execution','mission_composer.py',['mission-execution','provider-composition','verification'],'INTEGRATED'),
      ('living-loop','Persisted operating loop and continuity checkpoints','living_loop.py',['continuity','recovery','project-state'],'INTEGRATED'),
      ('action-ready','Evidence normalization, reconciliation, decision, and action preparation','action_ready.py',['evidence','decision','action-preparation'],'INTEGRATED'),
      ('outcome-intelligence','Outcome graph, state, trajectory, opportunity, and learning projections','outcome_intelligence.py',['outcome-continuity','trajectory','opportunity','learning'],'INTEGRATED'),
      ('personal-agent-adapter','Compatibility adapter for command classification and bounded specialist descriptors','personal_agent.py',['intent-adapter','specialist-contract'],'ADAPTER'),
    ]
    canonical_names={'canonical-runtime','canonical-core','capability-registry','mission-composer','living-loop','action-ready','outcome-intelligence'}
    for n,purpose,module,caps,status in specs:
        exists=(p/module).exists(); health='healthy' if exists else 'unavailable'; st=status if exists else 'UNAVAILABLE'
        r.register(_contract(n,purpose,module,caps,st,health,limitations=['No connector invocation from registry','Local tests do not prove production readiness'],canonical=n in canonical_names))
    return r

def export_registry(path=None,root=None):
    root=root or os.getenv('NEXUS_PRODUCT_ROOT') or str(Path(__file__).resolve().parents[1])
    path=path or str(Path(os.getenv('NEXUS_ARTIFACT_ROOT', Path.home()/'.local'/'share'/'nexus'/'artifacts'))/'nexus-engine-registry.json')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    r=default_registry(root); Path(path).write_text(json.dumps([asdict(e) for e in r.all()],indent=2)); return r

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('objective',nargs='?',default='audit this project'); a=p.parse_args(); r=default_registry(); print(json.dumps({'selection':r.select(a.objective),'health':r.health(),'graph':r.graph()},indent=2))
