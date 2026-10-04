#!/usr/bin/env python3
"""Evidence-bounded foresight for NEXUS.

This module produces qualitative forecasts only when evidence supports them.
It labels facts, inferences, trends, scenarios, and forecasts explicitly.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any
import json

HORIZON_ORDER={'IMMEDIATE':1,'SHORT_TERM':2,'MEDIUM_TERM':3,'LONG_TERM':4}

@dataclass
class Prediction:
    prediction: str
    basis: list[str]
    supporting_observations: list[str]
    assumptions: list[str]
    confidence: str
    time_horizon: str
    expected_conditions: list[str]
    alternative_outcomes: list[str]
    risk: str
    recommended_preparation: list[str]
    verification_condition: str
    actual_outcome: str|None=None
    calibration_result: str|None=None
    classification: str='FORECAST'
    generated_at: str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
    def to_dict(self): return asdict(self)

def evidence_level(observations: list[Any]) -> str:
    n=len([x for x in observations if x not in (None,'',[],{})])
    return 'INSUFFICIENT_EVIDENCE' if n==0 else 'LOW_CONFIDENCE' if n<2 else 'MODERATE_CONFIDENCE' if n<4 else 'HIGH_CONFIDENCE'

def trend(history: list[dict]) -> dict:
    if not history or len(history)<2:
        return {'classification':'UNKNOWN','direction':'unknown','velocity':'unknown','basis':'insufficient historical observations','observations':len(history)}
    changes=[h.get('meaningful',False) for h in history]
    active=sum(bool(x) for x in changes)
    direction='increasing_activity' if active>=len(changes)*0.7 else 'decreasing_activity' if active<=len(changes)*0.2 else 'stable_or_mixed'
    velocity='accelerating' if active>=3 and changes[-1] else 'decelerating' if active==0 else 'slowly_changing'
    return {'classification':'TREND','direction':direction,'velocity':velocity,'basis':'observed snapshot history only','observations':len(history),'meaningful_changes':active}

def momentum(current: dict, history: list[dict]|None=None) -> dict:
    history=history or []
    t=trend(history)
    counts=current.get('counts',{})
    open_work=counts.get('open_issues',0)+counts.get('open_pull_requests',0)
    if not history:
        return {'momentum':'UNKNOWN','direction':'UNKNOWN','trajectory':'UNKNOWN','blockers':[],'accelerators':[],'risk':'UNKNOWN','basis':['one snapshot cannot establish momentum or progress']}
    return {'momentum':'LOW' if t['direction']=='decreasing_activity' else 'STABLE' if t['direction']=='stable_or_mixed' else 'UNKNOWN','direction':t['direction'],'trajectory':'UNCERTAIN','blockers':['open external work'] if open_work else [],'accelerators':[],'risk':'UNKNOWN','basis':['activity is not treated as progress','historical evidence remains limited']}

def scenarios(state: dict, observations: list[str]|None=None) -> list[dict]:
    observations=observations or []
    if not observations:
        return [{'name':'INSUFFICIENT_EVIDENCE','classification':'SCENARIO','conditions':['no verified trend or dependency evidence'],'drivers':[],'risks':['forecasting would fabricate certainty'],'indicators':['new verified snapshots or project dependencies'],'preparation':['establish a baseline and collect another observation']}]
    return [
      {'name':'BASE_CASE','classification':'SCENARIO','conditions':['current observed conditions persist'],'drivers':observations,'risks':['unknown future changes'],'indicators':['same state on next comparison'],'preparation':['continue low-cost observation']},
      {'name':'DOWNSIDE','classification':'SCENARIO','conditions':['open work or blockers increase'],'drivers':['new unresolved work','stale dependencies'],'risks':['execution delay'],'indicators':['open issues or PRs rise'],'preparation':['review the specific blocker before escalation']},
      {'name':'UPSIDE','classification':'SCENARIO','conditions':['verified work closes or momentum evidence appears'],'drivers':['completed work','meaningful positive changes'],'risks':['none established'],'indicators':['closed work plus supporting project evidence'],'preparation':['preserve the conditions that enabled progress']}
    ]

def forecast_next(current: dict, history: list[dict]|None=None) -> dict:
    history=history or []
    ev=[current.get('facts',{}).get('updated_at'),current.get('facts',{}).get('pushed_at'),current.get('counts')]
    conf=evidence_level(history+[current])
    if len(history)<2:
        p=Prediction('No specific next state is supportable yet',['single current snapshot','no sufficient history'],['repository metadata observed'],['future activity is unknown'], 'LOW_CONFIDENCE','SHORT_TERM',['a later verified comparison becomes available'],['meaningful change','no change','external interruption'],'unknown',['establish another baseline comparison'],'compare a later snapshot against the current state')
    else:
        p=Prediction('The next verified state is most likely to be either unchanged or to show a new repository event; the direction is not determinable from current evidence',['snapshot comparison history'],['historical repository observations'],['observations remain representative'],'LOW_CONFIDENCE','SHORT_TERM',['a new observation is collected'],['new work','no change','repository access failure'],'unknown',['continue read-only monitoring and inspect only meaningful changes'],'next snapshot comparison')
    return {'command':'WHAT HAPPENS NEXT?','forecast':p.to_dict(),'scenarios':scenarios(current,['repository snapshot']), 'uncertainty':'explicit; this is not a deterministic prediction'}

def prepare_for(current: dict, history: list[dict]|None=None) -> dict:
    history=history or []
    open_work=current.get('counts',{}).get('open_issues',0)+current.get('counts',{}).get('open_pull_requests',0)
    prepare=[]; watch=[]; ignore=[]
    if open_work: prepare.append('review open repository work and map it to the active project')
    else: watch.append('next verified snapshot for meaningful changes')
    if len(history)<2: watch.append('collect enough history before asserting momentum or trajectory')
    ignore.append('raw activity without evidence of project impact')
    return {'command':'WHAT SHOULD I PREPARE FOR?','prepare_now':prepare,'watch':watch,'ignore':ignore,'evidence_boundary':'no deadlines, dependencies, or progress data were supplied'}

def what_miss(current: dict, context: dict|None=None) -> dict:
    context=context or {}
    concerns=[]
    if current.get('counts',{}).get('open_issues',0): concerns.append('open issues may require project mapping')
    if current.get('counts',{}).get('open_pull_requests',0): concerns.append('open pull requests may require review')
    if not context.get('deadlines'): concerns.append('deadline risk cannot be assessed without deadline context')
    return {'command':'WHAT AM I ABOUT TO MISS?','evidence_backed_concerns':concerns,'unknowns':['unprovided deadlines','unprovided task dependencies','unprovided user objective'],'confidence':'LOW_CONFIDENCE' if not concerns else 'MODERATE_CONFIDENCE'}

def dependency_forecast(dependencies: list[dict]) -> dict:
    if not dependencies: return {'status':'UNKNOWN','reason':'no dependency graph supplied','affected':[],'preparation':['identify critical dependencies']}
    affected=[]
    for d in dependencies:
        if d.get('status') in {'delayed','blocked','at_risk'}:
            affected.append({'blocked_by':d.get('id'),'downstream':d.get('downstream',[]),'risk':'POSSIBLE','basis':'dependency state supplied by user/context'})
    return {'status':'AT_RISK' if affected else 'ON_TRACK','affected':affected,'preparation':['review affected downstream work'] if affected else []}

def deadline_forecast(deadline: str|None, progress: str|None, remaining: str|None, dependencies: list[dict]|None=None) -> dict:
    if not all([deadline,progress,remaining]): return {'status':'UNKNOWN','reason':'deadline, progress, and remaining work are not all available','why':'insufficient evidence','preparation':['supply missing deadline/project state']}
    risk='AT_RISK' if dependencies and any(d.get('status') in {'delayed','blocked'} for d in dependencies) else 'UNKNOWN'
    return {'status':risk,'why':'qualitative dependency evidence only','preparation':['review remaining work against deadline']}

def resource_forecast(resources: dict|None=None) -> dict:
    resources=resources or {}
    missing=[k for k,v in resources.items() if v in (None,'missing',False)]
    return {'status':'AT_RISK' if missing else 'UNKNOWN','missing_resources':missing,'basis':'only explicitly supplied resource requirements are evaluated'}

def calibrate(prediction: dict, actual: str|None) -> dict:
    if not actual: return {'status':'UNKNOWN','reason':'actual outcome not supplied'}
    expected=prediction.get('prediction','').lower(); actual_l=actual.lower()
    result='correct' if actual_l in expected or expected in actual_l else 'incorrect'
    return {'status':result,'prediction':prediction.get('prediction'),'actual_outcome':actual,'error':'qualitative comparison only'}

def decay(prediction: dict, current_timestamp: str|None=None) -> dict:
    return {'status':'RE_EVALUATE','reason':'predictions never remain valid solely because they were previously generated','requires_new_evidence':True,'current_timestamp':current_timestamp or datetime.now(timezone.utc).isoformat()}

def command(command: str, current: dict, history: list[dict]|None=None, context: dict|None=None) -> dict:
    c=command.strip().lower()
    if 'what happens next' in c: return forecast_next(current,history)
    if 'what should i prepare' in c or 'prepare for' in c: return prepare_for(current,history)
    if 'about to miss' in c: return what_miss(current,context)
    return {'command':command,'status':'UNSUPPORTED_COMMAND','available':['WHAT HAPPENS NEXT?','WHAT SHOULD I PREPARE FOR?','WHAT AM I ABOUT TO MISS?']}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('command',nargs='?',default='What happens next?'); a=p.parse_args()
    print(json.dumps(command(a.command,{},[]),indent=2))
