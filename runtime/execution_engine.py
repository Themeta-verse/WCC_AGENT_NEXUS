#!/usr/bin/env python3
"""Governed closed-loop execution for NEXUS.

This engine can execute SAFE local/read-only actions and prepare or block
consequential external actions. Every action has a contract, status, evidence,
verification, and recovery decision.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable
import json

SAFE={'READ','ANALYZE'}
PREPARE={'CREATE','MODIFY'}
CONFIRM={'SEND','PUBLISH','DELETE','AUTHENTICATE','EXTERNAL_SIDE_EFFECT','HIGH_IMPACT','IRREVERSIBLE','FINANCIAL'}

@dataclass
class Option:
    name: str
    benefits: list[str]
    risks: list[str]
    cost: str
    dependencies: list[str]
    reversibility: str
    expected_outcome: str
    information_requirements: list[str]

@dataclass
class Decision:
    objective: str
    current_state: dict
    available_options: list[Option]
    constraints: list[str]
    evidence: list[str]
    assumptions: list[str]
    risks: list[str]
    opportunity_cost: list[str]
    reversibility: str
    expected_outcomes: list[str]
    recommended_option: str|None
    confidence: str
    approval_requirement: str
    verification_condition: str
    status: str
    def to_dict(self):
        d=asdict(self); d['available_options']=[asdict(x) for x in self.available_options]; return d

@dataclass
class ActionContract:
    action: str
    action_type: str
    inputs: dict
    expected_result: str
    risk: str
    side_effect: str
    authorization: str
    verification_method: str
    rollback_recovery: str
    idempotency_key: str=''
    def __post_init__(self):
        if not self.idempotency_key:
            self.idempotency_key=sha256(json.dumps({'action':self.action,'type':self.action_type,'inputs':self.inputs},sort_keys=True,default=str).encode()).hexdigest()[:20]
    def governance(self):
        t=self.action_type.upper()
        return 'SAFE' if t in SAFE else 'PREPARE' if t in PREPARE else 'CONFIRM' if t in CONFIRM else 'BLOCK'
    def to_dict(self): return asdict(self)|{'governance':self.governance()}

@dataclass
class ExecutionResult:
    status: str
    action: dict
    evidence: list[str]
    verification: dict
    error: str|None=None
    recovery: dict|None=None
    lesson: str|None=None
    decision_confidence: str='unknown'
    execution_confidence: str='unknown'
    verification_confidence: str='unknown'
    timestamp: str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
    def to_dict(self): return asdict(self)

class ExecutionJournal:
    def __init__(self): self.entries=[]
    def record(self, objective, decision, action, result):
        entry={'objective':objective,'decision':decision,'action':action,'timestamp':result.timestamp,'result':result.to_dict(),'authorization':action.get('authorization'),'verification':result.verification,'error':result.error,'recovery':result.recovery,'lesson':result.lesson}
        self.entries.append(entry); return entry

class DecisionEngine:
    def compile(self, objective: str, current_state: dict|None=None, evidence: list[str]|None=None, constraints: list[str]|None=None) -> Decision:
        current_state=current_state or {}; evidence=evidence or []; constraints=constraints or []
        opts=[Option('proceed_safely',['progress or information gain'],['unknowns may remain'],'low',[],'reversible','advance the objective',['verification']),Option('research_first',['reduce uncertainty'],['time cost'],'medium',['relevant source'],'reversible','improve decision quality',['source access']),Option('defer',['preserve optionality'],['delay opportunity'],'low',[],'reversible','wait for better evidence',['future signal']),Option('do_nothing',['no side effect'],['objective may stall'],'low',[],'reversible','no change',['none'])]
        rec='research_first' if len(evidence)<2 else 'proceed_safely'
        status='RESEARCH' if len(evidence)<2 else 'PREPARE'
        return Decision(objective,current_state,opts,constraints,evidence,['available context is accurate'],['unknowns remain'],['delay cost'], 'reversible',['verified next state'],rec,'LOW_CONFIDENCE' if len(evidence)<2 else 'MODERATE_CONFIDENCE','SAFE','independent read-back of authoritative state',status)

class ExecutionEngine:
    def __init__(self): self.journal=ExecutionJournal(); self.completed_keys=set(); self.max_retries=2
    def precheck(self, action: ActionContract, confirmation=False) -> dict:
        missing=[]
        for k in ('expected_result','verification_method','rollback_recovery'):
            if not getattr(action,k): missing.append(k)
        gov=action.governance()
        allowed=gov=='SAFE' or (gov in {'PREPARE','CONFIRM'} and confirmation)
        if not allowed: missing.append('explicit confirmation' if gov in {'PREPARE','CONFIRM'} else 'supported governance')
        return {'ok':not missing,'governance':gov,'missing':missing,'idempotency_key':action.idempotency_key}

    def execute(self, objective: str, decision: Decision, action: ActionContract, operation: Callable[[],Any], verify: Callable[[Any],dict], confirmation=False, simulate=False) -> ExecutionResult:
        pre=self.precheck(action,confirmation)
        if not pre['ok']:
            r=ExecutionResult('BLOCKED',action.to_dict(),['precheck completed'],{'status':'not_run','precheck':pre},error='pre-execution check blocked action',recovery={'next':'ask_or_prepare','reason':pre['missing']},lesson='missing authorization or verification contract',decision_confidence=decision.confidence,execution_confidence='none',verification_confidence='none')
            self.journal.record(objective,decision.to_dict(),action.to_dict(),r); return r
        if action.idempotency_key in self.completed_keys:
            r=ExecutionResult('CANCELLED',action.to_dict(),['idempotency check found prior execution'],{'status':'not_run','reason':'already_completed'},error='duplicate action prevented',recovery={'next':'continue_from_existing_state'},lesson='idempotency prevented duplicate work',decision_confidence=decision.confidence,execution_confidence='high',verification_confidence='high')
            self.journal.record(objective,decision.to_dict(),action.to_dict(),r); return r
        if simulate:
            output={'simulated':True,'expected':action.expected_result}
        else:
            try: output=operation()
            except Exception as e:
                r=ExecutionResult('FAILED',action.to_dict(),['operation raised an exception'],{'status':'not_run'},error=str(e),recovery={'next':'inspect_failure','retry_safe':False},lesson='execution failure must not be treated as success',decision_confidence=decision.confidence,execution_confidence='low',verification_confidence='none')
                self.journal.record(objective,decision.to_dict(),action.to_dict(),r); return r
        try: ver=verify(output)
        except Exception as e: ver={'status':'verification_failed','error':str(e)}
        ok=ver.get('status') in {'verified','success','passed'} or ver.get('verified') is True
        status='SUCCESS' if ok else 'UNKNOWN' if ver.get('status') in {'ambiguous','unknown'} else 'FAILED'
        self.completed_keys.add(action.idempotency_key) if ok else None
        r=ExecutionResult(status,action.to_dict(),['operation returned output'],ver,error=None if ok else 'independent verification did not confirm expected result',recovery={'next':'continue' if ok else 'inspect_or_rollback','retry_safe':False if not ok else True},lesson='verified success' if ok else 'false-success prevention engaged',decision_confidence=decision.confidence,execution_confidence='high',verification_confidence='high' if ok else 'low')
        self.journal.record(objective,decision.to_dict(),action.to_dict(),r); return r

def quality_score(result: ExecutionResult) -> dict:
    return {'outcome_quality':'high' if result.status=='SUCCESS' else 'unknown','execution_reliability':result.execution_confidence,'verification_quality':result.verification_confidence,'user_effort':'low' if result.status in {'SUCCESS','BLOCKED'} else 'medium','failure_recovery':'defined' if result.recovery else 'missing','unnecessary_actions':'none' if result.status in {'SUCCESS','BLOCKED','CANCELLED'} else 'unknown'}

def approval_request(action: ActionContract) -> dict:
    return {'state':'PENDING_APPROVAL','action':action.to_dict(),'why':action.expected_result,'risk':action.risk,'expected_effect':action.side_effect,'recommendation':'approve only after reviewing target and verification method','options':['APPROVE','REJECT','MODIFY']}

def autonomy_ceiling() -> dict:
    return {'level':3,'label':'PREPARE','can_execute':['local/read-only operations with independent verification'],'can_prepare':['GitHub writes, publishing, messaging, schedules, high-impact actions'],'requires_confirmation':['CREATE','MODIFY','SEND','PUBLISH','DELETE','AUTHENTICATE','HIGH_IMPACT','IRREVERSIBLE','FINANCIAL'],'impossible_or_unavailable':['unsupported connectors','always-on execution without deployment','unverified external mutation']}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('objective',nargs='?',default='Handle this'); a=p.parse_args()
    d=DecisionEngine().compile(a.objective,evidence=[]); print(json.dumps({'decision':d.to_dict(),'autonomy':autonomy_ceiling()},indent=2))
