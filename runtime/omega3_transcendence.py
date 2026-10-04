#!/usr/bin/env python3
"""NEXUS Ω³ bounded Transcendence Engine.

Local planning and critique only. It composes Ω² and the Capability Registry;
it never invokes connectors, activates automations, changes permissions, or
claims simulated/external work as real.
"""
from __future__ import annotations
import json
from dataclasses import dataclass,asdict,field
from typing import Any
try:
    from omega2_intelligence import compile_omega2, parse_intent
    from capability_registry import discover_actual_registry
    from canonical_core import core_id, governance_for, utc_now
except ImportError:
    from .omega2_intelligence import compile_omega2, parse_intent
    from .capability_registry import discover_actual_registry
    from .canonical_core import core_id, governance_for, utc_now

REALITY={"OBSERVED","INFERRED","HYPOTHESIS","SIMULATED","PLANNED","EXECUTED","VERIFIED","UNKNOWN"}

@dataclass
class Strategy:
    id:str
    name:str
    description:str
    quality:float
    effort:float
    risk:float
    reversibility:float
    dependency_load:float
    time:float
    availability:float
    verification_difficulty:float
    reality:str="HYPOTHESIS"
    selected:bool=False
    rationale:str=""

@dataclass
class Critique:
    strategy_id:str
    weaknesses:list[str]
    fragile_assumptions:list[str]
    hidden_dependencies:list[str]
    unnecessary_steps:list[str]
    missing_controls:list[str]
    simplifications:list[str]
    severity:str
    revision:str
    reality:str="INFERRED"

@dataclass
class Opportunity:
    id:str
    title:str
    evidence:list[str]
    confidence:str
    why_now:str
    expected_value:str
    effort:str
    risk:str
    next_experiment:str
    status:str="OPTIONAL_OPPORTUNITY"
    reality:str="INFERRED"


def amplify_intent(text:str,context=None):
    context=context or {}; intent=parse_intent(text,context)
    interpretations=[text.strip()]
    if intent.mode=="BUILD":
        interpretations += ["define the desired product outcome and a verified smallest path","identify target user, constraints, and definition of done"]
    elif intent.mode in {"RESEARCH","ANALYZE"}:
        interpretations += ["produce evidence-backed findings and an explicit uncertainty map"]
    ambiguities=list(intent.ambiguities)
    questions=[]
    if ambiguities: questions.append("Which missing fact would materially change the plan?")
    return {"user_request":text,"likely_outcome":text.strip(),"interpretations":interpretations,"ambiguities":ambiguities,"questions":questions,"optional_opportunities":[],"intent_boundary":"suggestions cannot silently change the user objective","reality":"INFERRED"}


def strategy_candidates(outcome,available_count=0):
    base=[
      ("minimal","Use current evidence and the smallest safe local plan",0.65,0.20,0.15,0.90,0.10,0.35,0.80,0.20),
      ("balanced","Combine local analysis, targeted evidence, and staged verification",0.80,0.45,0.30,0.75,0.35,0.55,0.70,0.35),
      ("maximum-capability","Use every relevant authorized capability if available",0.95,0.90,0.60,0.45,0.80,0.90,0.25,0.70),
      ("risk-minimized","Resolve critical unknowns before any consequential step",0.72,0.35,0.08,0.95,0.25,0.60,0.90,0.25),
      ("unconventional","Reframe the problem and search for a lower-complexity path",0.75,0.40,0.35,0.70,0.30,0.50,0.55,0.45),
    ]
    return [Strategy(id=core_id("strategy"),name=n,description=d,quality=q,effort=e,risk=r,reversibility=rv,dependency_load=dep,time=t,availability=(1.0 if available_count else av),verification_difficulty=vd) for n,d,q,e,r,rv,dep,t,av,vd in base]


def score_strategy(s:Strategy):
    # bounded utility: quality + availability + reversibility, discounted by risk/cost/verification burden
    return round(0.40*s.quality+0.20*s.availability+0.15*s.reversibility-0.10*s.effort-0.08*s.risk-0.07*s.dependency_load-0.05*s.verification_difficulty,4)


def search_strategies(outcome,available_count=0):
    candidates=strategy_candidates(outcome,available_count)
    scored=[]
    for s in candidates:
        score=score_strategy(s); scored.append((score,s))
    scored.sort(key=lambda x:x[0],reverse=True); best_score,best=scored[0]; best.selected=True; best.rationale=f"highest bounded utility {best_score}; availability={best.availability}; no external execution"
    return {"candidates":[{**asdict(s),"score":score_strategy(s)} for _,s in scored],"selected":asdict(best),"selection_reason":best.rationale,"optimization":"quality/risk/effort/reversibility/dependencies/verification","reality":"HYPOTHESIS","action_state":"PLANNED"}


def critique_strategy(strategy:dict):
    weak=[]; fragile=[]; hidden=[]; unnecessary=[]; missing=[]; simplify=[]
    if strategy.get('name')=='maximum-capability': weak.append('depends on unavailable capabilities'); hidden.append('authorization and connector readiness')
    if strategy.get('availability',0)<0.5: weak.append('capability availability is not sufficient for real execution')
    if strategy.get('verification_difficulty',0)>0.6: missing.append('independent verification plan')
    if strategy.get('effort',0)>0.7: unnecessary.append('broad capability fan-out before proving the minimum path'); simplify.append('start with a staged plan')
    if not weak: weak.append('local evidence may be incomplete')
    revision='Preserve plan-only status, resolve material knowledge gaps, and verify each boundary before promotion.'
    return asdict(Critique(strategy_id=strategy['id'],weaknesses=weak,fragile_assumptions=fragile,hidden_dependencies=hidden,unnecessary_steps=unnecessary,missing_controls=missing,simplifications=simplify,severity='MEDIUM' if weak else 'LOW',revision=revision))


def meta_cognition(outcome,omega2,strategies):
    selected=strategies['selected']; critique=critique_strategy(selected)
    return {"goal":outcome,"success_definition":omega2['outcome'],"known":['user supplied outcome','actual registry snapshot used locally'],"assumptions":omega2['intent'].get('ambiguities',[]),"unknowns":[g['question'] for g in omega2['knowledge_gaps']],"capabilities_available":len(omega2['capability_selection'].get('selected',[])),"capabilities_missing":omega2['capability_selection'].get('unavailable_requirements',[]),"simplest":"minimal strategy","most_powerful":"maximum-capability strategy (not currently executable)","safest":"risk-minimized strategy","highest_leverage":"resolve the highest-impact knowledge gap","selected_strategy":selected['name'],"critic_summary":critique,"verification_needed":["independent evidence","actual execution evidence if execution is ever authorized"],"reality":"INFERRED"}


def opportunities(outcome,omega2,critique):
    ops=[]
    if omega2['knowledge_gaps']:
        ops.append(asdict(Opportunity(id=core_id('opportunity'),title='Resolve the highest-impact knowledge gap',evidence=[g['question'] for g in omega2['knowledge_gaps'][:2]],confidence='bounded',why_now='the gap constrains reliable planning',expected_value='higher-quality next decision',effort='low',risk='low',next_experiment='ask or retrieve only the evidence that changes the plan')))
    ops.append(asdict(Opportunity(id=core_id('opportunity'),title='Use the smallest safe workflow first',evidence=['strategy search','strategy critique'],confidence='medium',why_now='reduces unnecessary tool calls and risk',expected_value='faster verified progress',effort='low',risk='low',next_experiment='run a local plan-only comparison')))
    return ops


def quality_loop(omega3):
    strengths=[]; weaknesses=[]
    if omega3['strategy_search']['selected']['name']=='minimal': strengths.append('minimum sufficient strategy favored')
    if omega3['meta_cognition']['unknowns']: weaknesses.append('material unknowns remain')
    if omega3['strategy_critique']['missing_controls']: weaknesses.append('verification controls need explicit future execution mapping')
    return {"stages":["GENERATE","CRITIQUE","IDENTIFY_WEAKNESS","IMPROVE","RECHECK","VERIFY","DELIVER"],"strengths":strengths,"weaknesses":weaknesses,"quality_status":"PLANNED_REVIEW","reality":"INFERRED"}


def compile_omega3(outcome,context=None,mode='PLAN_ONLY'):
    registry=discover_actual_registry(); omega2=compile_omega2(outcome,context=context,registry=registry,mode='PLAN_ONLY'); search=search_strategies(outcome,len(omega2['capability_selection'].get('selected',[]))); critique=critique_strategy(search['selected']); meta=meta_cognition(outcome,omega2,search); ops=opportunities(outcome,omega2,critique)
    result={"id":core_id('omega3'),"outcome":outcome,"mode":mode,"intent_amplification":amplify_intent(outcome,context),"omega2":omega2,"meta_cognition":meta,"strategy_search":search,"strategy_critique":critique,"opportunities":ops,"quality_loop":None,"reality":"PLANNED" if mode=='PLAN_ONLY' else 'SIMULATED',"action_state":"PLANNED","execution_performed":False,"external_invocations":0,"persisted":False,"governance":governance_for(outcome)}
    result['quality_loop']=quality_loop(result); return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('outcome'); a=p.parse_args(); print(json.dumps(compile_omega3(a.outcome),indent=2))
