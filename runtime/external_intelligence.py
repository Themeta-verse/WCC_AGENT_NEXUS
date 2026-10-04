#!/usr/bin/env python3
"""Universal external intelligence layer for NEXUS.

All source adapters emit normalized signals. This module deliberately performs
local reasoning only; consequential external actions remain approval-gated.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]

@dataclass
class Provenance:
    source: str
    observed_at: str
    exact_observation: str
    fact: str
    inference: str | None = None
    hypothesis: str | None = None
    raw_reference: str | None = None

@dataclass
class Signal:
    source: str
    source_type: str
    event_type: str
    entity: str
    entity_id: str
    timestamp: str
    previous_state: Any = None
    current_state: Any = None
    change: Any = None
    confidence: str = 'unknown'
    importance: str = 'unknown'
    relevance: str = 'unknown'
    actionability: str = 'unknown'
    project: str | None = None
    goal: str | None = None
    dependencies: list[str] = field(default_factory=list)
    risk: str | None = None
    recommended_action: str | None = None
    approval_requirement: str = 'SAFE'
    provenance: Provenance | None = None
    signal_id: str = ''

    def __post_init__(self):
        if not self.signal_id:
            basis = '|'.join([self.source,self.source_type,self.event_type,self.entity,self.entity_id,self.timestamp,json.dumps(self.change,sort_keys=True,default=str)])
            self.signal_id = sha256(basis.encode()).hexdigest()[:20]

    def to_dict(self):
        d=asdict(self)
        return d

class ConnectorAdapter:
    name = 'abstract'
    capabilities = {'DISCOVER':False,'READ':False,'OBSERVE':False,'SEARCH':False,'CREATE':False,'MODIFY':False,'TRIGGER':False,'VERIFY':False}
    authorization = 'unknown'
    risk = 'unknown'
    status = 'unavailable'

    def capability_matrix(self):
        return {'connector':self.name,'available':self.status=='verified','capabilities':self.capabilities,'authorization':self.authorization,'risk':self.risk,'status':self.status}

class GitHubAdapter(ConnectorAdapter):
    name='GitHub'
    capabilities={'DISCOVER':True,'READ':True,'OBSERVE':True,'SEARCH':True,'CREATE':True,'MODIFY':True,'TRIGGER':'not_tested','VERIFY':True}
    authorization='authenticated session; repository permissions are scope-dependent'
    risk='external writes are consequential and require confirmation'
    status='verified_read_only'

class SignalBus:
    def __init__(self, project: str | None = None):
        self.project=project
        self.signals: list[Signal]=[]
        self.seen: dict[str, Signal]={}

    def ingest(self, signal: Signal) -> dict:
        duplicate_kind=self.classify_duplicate(signal)
        if duplicate_kind in ('duplicate','near_duplicate'):
            return {'accepted':False,'duplicate_kind':duplicate_kind,'signal':signal.to_dict()}
        self.signals.append(signal); self.seen[signal.signal_id]=signal
        return {'accepted':True,'duplicate_kind':'new','signal':signal.to_dict()}

    def classify_duplicate(self, signal: Signal) -> str:
        for old in self.signals:
            if old.signal_id==signal.signal_id: return 'duplicate'
            if (old.source,old.entity,old.entity_id,old.event_type)==(signal.source,signal.entity,signal.entity_id,signal.event_type):
                if old.current_state==signal.current_state and old.change==signal.change: return 'near_duplicate'
        return 'new'

    def what_changed(self) -> dict:
        meaningful=[s.to_dict() for s in self.signals if priority(s)['classification'] in ('SURFACE','ACT','ASK')]
        return {'important_changes':meaningful,'all_signal_count':len(self.signals),'suppressed_count':len(self.signals)-len(meaningful)}

class WorldModel:
    def __init__(self):
        self.entities={}; self.events={}; self.relationships=[]; self.timeline=[]

    def apply(self, signal: Signal) -> dict:
        self.entities.setdefault(signal.entity_id, {'entity':signal.entity,'source':signal.source,'last_state':None,'confidence':signal.confidence})
        item=self.entities[signal.entity_id]
        item.update({'last_state':signal.current_state,'last_event':signal.signal_id,'last_observed':signal.timestamp,'confidence':signal.confidence})
        self.events[signal.signal_id]=signal.to_dict()
        self.timeline.append({'signal_id':signal.signal_id,'timestamp':signal.timestamp,'entity_id':signal.entity_id,'event_type':signal.event_type})
        return {'entity_id':signal.entity_id,'state':item,'event_id':signal.signal_id}

    def snapshot(self):
        return {'entities':self.entities,'events':self.events,'relationships':self.relationships,'timeline':self.timeline}

def normalize_github_health(report: dict, project: str = 'nexus-v3') -> list[Signal]:
    snap=report.get('snapshot',{}); health=report.get('health',{}); comp=report.get('comparison',{})
    repo=snap.get('repository','unknown'); facts=snap.get('facts',{}); counts=snap.get('counts',{})
    observed=snap.get('observed_at',datetime.now(timezone.utc).isoformat())
    provenance=Provenance(source='GitHub via authenticated gh CLI',observed_at=observed,exact_observation=json.dumps({'facts':facts,'counts':counts,'comparison':comp},sort_keys=True),fact='Repository metadata, counts, and snapshot comparison were returned by GitHub API.',inference=health.get('evidence_boundary'),raw_reference=f'https://github.com/{repo}')
    signals=[]
    signals.append(Signal(source='GitHub',source_type='repository',event_type='repository_snapshot',entity='repository',entity_id=repo,timestamp=observed,current_state={'facts':facts,'counts':counts},change=comp.get('changes',[]),confidence='high',importance='medium',relevance='high',actionability='record',project=project,recommended_action=health.get('recommended_next_action'),approval_requirement='SAFE',provenance=provenance))
    for issue in snap.get('issues',[]):
        signals.append(Signal(source='GitHub',source_type='repository',event_type='issue',entity='issue',entity_id=f'{repo}#issue-{issue.get("number")}',timestamp=issue.get('updated_at',observed),current_state=issue,change='open_or_updated_issue',confidence='high',importance='medium',relevance='unknown',actionability='surface',project=project,approval_requirement='PREPARE',provenance=provenance))
    for pr in snap.get('pull_requests',[]):
        signals.append(Signal(source='GitHub',source_type='repository',event_type='pull_request',entity='pull_request',entity_id=f'{repo}#pr-{pr.get("number")}',timestamp=pr.get('updated_at',observed),current_state=pr,change='pull_request_state',confidence='high',importance='high',relevance='unknown',actionability='ask',project=project,approval_requirement='CONFIRM',provenance=provenance))
    return signals

def qualitative_rank(value: str) -> int:
    return {'unknown':0,'low':1,'medium':2,'high':3,'critical':4,'record':1,'surface':3,'act':4,'ask':3}.get(str(value).lower(),0)

def priority(signal: Signal) -> dict:
    i,r,a,c=[qualitative_rank(x) for x in (signal.importance,signal.relevance,signal.actionability,signal.confidence)]
    if signal.event_type in {'duplicate','heartbeat'} or (i<=1 and r<=1): classification='IGNORE'
    elif signal.approval_requirement=='CONFIRM' and a>=3: classification='ASK'
    elif a>=4 and r>=2 and c>=2: classification='ACT'
    elif max(i,r,a)>=3: classification='SURFACE'
    else: classification='RECORD'
    return {'signal_id':signal.signal_id,'classification':classification,'importance':signal.importance,'relevance':signal.relevance,'actionability':signal.actionability,'confidence':signal.confidence,'reason':'qualitative ranking; no fake numerical precision'}

def impact(signal: Signal, project_state: dict | None = None) -> dict:
    project_state=project_state or {}
    affected=[]
    if signal.project: affected.append({'type':'project','id':signal.project,'reason':'signal explicitly mapped to project'})
    if signal.event_type in {'issue','pull_request'}: affected += [{'type':'tasks','reason':'open repository work may affect execution'}, {'type':'risks','reason':'unresolved external work may create risk'}]
    if signal.event_type=='repository_snapshot' and signal.change: affected.append({'type':'memory','reason':'store only meaningful observed change'})
    return {'signal_id':signal.signal_id,'affected':affected,'unknowns':['deadline impact','goal impact'] if not signal.goal else []}

def action_bridge(objective: str, adapter: ConnectorAdapter) -> dict:
    return {'objective':objective,'required_external_action':'unknown until objective is compiled','connector':adapter.name,'available_capabilities':adapter.capabilities,'next_step':'prepare only; do not execute consequential action without confirmation'}

def state_bridge(signal: Signal, world: WorldModel) -> dict:
    p=priority(signal); im=impact(signal); applied=world.apply(signal)
    return {'state_update':applied,'priority':p,'impact':im,'memory_decision':'record' if p['classification'] in ('RECORD','SURFACE','ASK','ACT') else 'suppress','follow_up':signal.recommended_action if p['classification']!='IGNORE' else None}

def connector_matrix() -> list[dict]:
    return [GitHubAdapter().capability_matrix(), {'connector':'Gmail','available':False,'capabilities':{},'authorization':'not enabled','risk':'unknown','status':'unavailable'}, {'connector':'Calendar','available':False,'capabilities':{},'authorization':'not enabled','risk':'unknown','status':'unavailable'}, {'connector':'Drive','available':False,'capabilities':{},'authorization':'not enabled','risk':'unknown','status':'unavailable'}, {'connector':'Slack','available':False,'capabilities':{},'authorization':'not enabled','risk':'unknown','status':'unavailable'}]

if __name__=='__main__':
    print(json.dumps({'connector_matrix':connector_matrix(),'schema':'SOURCE → SIGNAL → NORMALIZATION → WORLD MODEL → IMPACT → PERSONAL STATE → REASONING → ACTION → VERIFICATION → MEMORY'},indent=2))
