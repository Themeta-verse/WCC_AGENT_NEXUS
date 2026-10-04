#!/usr/bin/env python3
"""NEXUS Ω⁸ local persistence and capability-fabric boundary.

The store is deliberately local, JSON-based, atomic, versioned, and ephemeral
in deployment terms. It does not invoke connectors or perform external effects.

CONTRACT BOUNDARY (canonical-architecture pass):
  The ``CapabilityRequest`` / ``CapabilityResponse`` / ``ExecutionReceipt``
  dataclasses in THIS module are the LEGACY MissionComposer-era,
  provider-bound contract (used by GitHubReadProvider, BrowserReadProvider,
  FilesystemReadProvider and MissionComposer missions). They are NOT the
  canonical connector contract.

  The CANONICAL connector contract lives in ``runtime.capability_fabric``:
  CapabilityRequest / CapabilityResponse / CapabilityResolutionError /
  resolve_connector / execute_capability / verify_capability_response /
  make_canonical_receipt / normalize_receipt. All NEW connector-based
  execution MUST use that module. Do not mix the two contracts.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict,field
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import hashlib,json,os,tempfile
try:
    from canonical_core import core_id
except ImportError:
    from .canonical_core import core_id

SCHEMA_VERSION='1.0'
CAPABILITY_STATES=('DISCOVERED','AVAILABLE','AUTHORIZED','PREPARED','EXECUTING','EXECUTED','OBSERVED','VERIFIED','FAILED','UNAVAILABLE','UNAUTHORIZED','EXPIRED')
EXECUTION_STATES=('NOT_STARTED','IN_PROGRESS','COMPLETED','PARTIAL','FAILED','UNKNOWN')


def now(): return datetime.now(timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()

@dataclass
class StateSnapshot:
    state_id:str
    version:int
    timestamp:str
    schema_version:str
    scope:str
    provenance:list[str]
    parent_state:str|None
    changes:dict
    state:dict
    status:str='current'
    checksum:str=''

@dataclass
class Event:
    event_id:str
    event_type:str
    timestamp:str
    scope:str
    payload:dict
    provenance:list[str]
    parent_event:str|None=None
    idempotency_key:str|None=None

@dataclass
class MemoryItem:
    memory_id:str
    category:str
    scope:str
    content:dict
    source:str
    created_at:str
    updated_at:str
    confidence:str
    reality:str
    provenance:list[str]
    freshness:str
    supersedes:str|None
    status:str='current'

@dataclass
class CapabilityRequest:
    """LEGACY provider-bound request (MissionComposer era).

    Canonical connector requests live in runtime.capability_fabric.
    Retained for GitHubReadProvider/BrowserReadProvider/FilesystemReadProvider
    and benchmark compatibility. Do not use for new connector execution.
    """
    request_id:str
    capability:str
    operation:str
    scope:str
    inputs:dict
    authorization:str
    governance:str
    execution_mode:str
    expected_output:str
    verification_requirement:str
    workflow_id:str=''
    task_id:str=''

@dataclass
class CapabilityResponse:
    """LEGACY provider-bound response (MissionComposer era).

    Canonical connector responses live in runtime.capability_fabric.
    Retained for legacy provider compatibility only.
    """
    request_id:str
    status:str
    reality:str
    outputs:dict
    observations:list[dict]
    verification:str
    provider:str
    reason:str

@dataclass
class ExecutionReceipt:
    execution_id:str
    request_id:str
    provider:str
    operation:str
    start_time:str
    end_time:str
    status:str
    side_effects:bool
    outputs:dict
    observations:list[dict]
    verification:str
    authorization:str
    provenance:list[str]
    capability:str=''
    scope:str=''
    inputs_hash:str=''
    output_reference:str|None=None
    reality:str='UNKNOWN'
    failure_state:str|None=None

@dataclass
class ApprovalRecord:
    approval_id:str
    scope:str
    action:str
    requested_at:str
    requested_by:str
    status:str
    expires_at:str|None
    reason:str
    constraints:list[str]

class LocalStateStore:
    """Atomic JSON snapshot/event store, suitable for local continuity tests."""
    def __init__(self,root=None):
        root=root or os.getenv('NEXUS_STATE_ROOT') or str(Path.home()/'.local'/'share'/'nexus'/'state')
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.snapshot_path=self.root/'current.json'; self.events_path=self.root/'events.jsonl'; self.memory_path=self.root/'memory.jsonl'
    def _atomic(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='.tmp-',dir=path.parent); os.close(fd)
        try:
            Path(tmp).write_text(data); os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    def save(self,state:dict,scope='nexus-local',provenance=None,changes=None,parent_state=None,status='current'):
        old=self.load(); version=(old.version+1 if old else 1); payload={'state_id':core_id('state'),'version':version,'timestamp':now(),'schema_version':SCHEMA_VERSION,'scope':scope,'provenance':provenance or ['local-store'],'parent_state':parent_state or (old.state_id if old else None),'changes':changes or {},'state':state,'status':status}
        payload['checksum']=digest(payload); self._atomic(self.snapshot_path,json.dumps(payload,indent=2)); return StateSnapshot(**payload)
    def load(self):
        if not self.snapshot_path.exists(): return None
        try:
            raw=json.loads(self.snapshot_path.read_text()); checksum=raw.get('checksum'); check=dict(raw); check.pop('checksum',None)
            if checksum!=digest(check): raise ValueError('STATE_CHECKSUM_MISMATCH')
            if raw.get('schema_version')!=SCHEMA_VERSION: raise ValueError('STATE_SCHEMA_MISMATCH')
            return StateSnapshot(**raw)
        except (json.JSONDecodeError,TypeError,KeyError) as e: raise ValueError('STATE_CORRUPT') from e
    def append_event(self,event_type,payload,scope='nexus-local',provenance=None,idempotency_key=None):
        existing=self.events()
        if idempotency_key and any(e.idempotency_key==idempotency_key for e in existing): return next(e for e in existing if e.idempotency_key==idempotency_key)
        ev=Event(core_id('event'),event_type,now(),scope,payload,provenance or ['local-event-store'],existing[-1].event_id if existing else None,idempotency_key)
        with self.events_path.open('a') as f: f.write(json.dumps(asdict(ev))+"\n")
        return ev
    def events(self):
        if not self.events_path.exists(): return []
        result=[]
        for line in self.events_path.read_text().splitlines():
            if line.strip(): result.append(Event(**json.loads(line)))
        return result
    def reconstruct(self,initial:dict|None=None,scope='nexus-local'):
        state=dict(initial or {}); applied=[]
        for ev in self.events():
            if ev.scope!=scope: continue
            if ev.event_type=='state_changed': state.update(ev.payload.get('changes',{}))
            elif ev.event_type=='workflow_created': state.setdefault('active_workflows',[]).append(ev.payload.get('workflow_id'))
            elif ev.event_type=='open_loop_created': state.setdefault('open_loops',[]).append(ev.payload)
            elif ev.event_type=='approval_requested': state.setdefault('pending_approvals',[]).append(ev.payload)
            applied.append(ev.event_id)
        return {'state':state,'events_applied':applied,'scope':scope,'reality':'OBSERVED','reconstructed':True}
    def checkpoint(self,name,state,scope='nexus-local',verified=False):
        status='verified' if verified else 'current'; return self.save(state,scope,['checkpoint:'+name],{'checkpoint':name,'verified':verified},status=status)
    def remember(self,category,scope,content,source='local',confidence='MEDIUM',reality='INFERRED',provenance=None,freshness='current',supersedes=None):
        item=MemoryItem(core_id('memory'),category,scope,content,source,now(),now(),confidence,reality,provenance or ['local-memory'],freshness,supersedes)
        with self.memory_path.open('a') as f: f.write(json.dumps(asdict(item))+"\n")
        return item
    def memories(self,scope=None):
        if not self.memory_path.exists(): return []
        out=[]
        for line in self.memory_path.read_text().splitlines():
            if line.strip():
                m=MemoryItem(**json.loads(line))
                if scope is None or m.scope==scope: out.append(m)
        return out
    def retrieve(self,query='',scope=None,limit=10):
        q=query.lower(); items=self.memories(scope); scored=[]
        for m in items:
            text=json.dumps(m.content).lower(); relevance=sum(1 for token in q.split() if token in text) if q else 0
            scored.append((relevance,m.confidence=='HIGH',m.freshness=='current',m))
        return [asdict(x[3]) for x in sorted(scored,key=lambda x:(x[0],x[1],x[2]),reverse=True)[:limit]]
    def reconcile_memory(self,scope,content_key,preferred_id=None):
        items=[m for m in self.memories(scope) if content_key in json.dumps(m.content)]
        if len(items)<2: return {'status':'NO_CONFLICT','items':[asdict(x) for x in items]}
        winner=next((m for m in items if m.memory_id==preferred_id),items[-1]); changed=[]
        for m in items:
            if m.memory_id!=winner.memory_id: m.status='superseded'; changed.append(m.memory_id)
        return {'status':'RESOLVED','winner':asdict(winner),'superseded':changed,'preserved_conflict':True}

class CapabilityProvider:
    name='abstract'
    def discover(self,request): return {'status':'DISCOVERED','provider':self.name}
    def validate(self,request): return {'valid':False,'reason':'abstract provider'}
    def prepare(self,request): return CapabilityResponse(request.request_id,'PREPARED','UNKNOWN',{},[],'UNKNOWN',self.name,'not executable')
    def execute(self,request): return CapabilityResponse(request.request_id,'UNAVAILABLE','UNKNOWN',{},[],'UNKNOWN',self.name,'provider execution not enabled')
    def observe(self,request): return CapabilityResponse(request.request_id,'UNAVAILABLE','UNKNOWN',{},[],'UNKNOWN',self.name,'observation unavailable')
    def verify(self,request,response): return {'status':'UNKNOWN','independent':False,'reason':'no provider evidence'}

class SimulationProvider(CapabilityProvider):
    name='simulation'
    def validate(self,request): return {'valid':request.execution_mode in {'SIMULATION','DRY_RUN'},'reason':'simulation mode required'}
    def execute(self,request):
        start=now(); return CapabilityResponse(request.request_id,'EXECUTED','SIMULATED',{'planned_output':request.expected_output},[{'source':'simulation','reality':'SIMULATED'}],'UNVERIFIED',self.name,'simulation only; no external effect')
    def receipt(self,request,response):
        return asdict(ExecutionReceipt(core_id('execution'),request.request_id,self.name,request.operation,now(),now(),response.status,False,response.outputs,response.observations,response.verification,request.authorization,['simulation']))

class LocalReadOnlyProvider(CapabilityProvider):
    name='local-read-only'
    def validate(self,request): return {'valid':request.operation in {'READ','ANALYZE'},'reason':'only local read/analyze operations are allowed'}
    def execute(self,request): return CapabilityResponse(request.request_id,'UNAVAILABLE','UNKNOWN',{},[],'UNKNOWN',self.name,'real provider invocation is disabled in Ω⁸ local mode')

def capability_readiness(name,registry_entry=None):
    if not registry_entry: return {'capability':name,'state':'UNAVAILABLE','authorized':False,'executable':False,'reason':'no evidence-backed registry entry'}
    return {'capability':name,'state':registry_entry.get('status','UNKNOWN'),'health':registry_entry.get('health','UNKNOWN'),'authorized':False,'executable':False,'reason':'registry metadata is not execution authorization'}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('--root',default=None); a=p.parse_args(); s=LocalStateStore(a.root); print(json.dumps({'snapshot':asdict(s.load()) if s.load() else None,'events':len(s.events()),'memories':len(s.memories())},indent=2))
