#!/usr/bin/env python3
"""Controlled adaptive workflow intelligence for NEXUS.

This module proposes and evaluates improvements; it never silently edits
production Skills, permissions, governance, connectors, or approval rules.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Any
import json

PROTECTED={'permissions','security','privacy','approvals','approval_requirements','destructive_safeguards','human_override','governance'}
@dataclass
class PerformanceRecord:
    objective: str
    method: str
    capabilities: list[str]
    execution_path: list[str]
    expected_outcome: str
    actual_outcome: str
    verification_result: str
    user_effort: str='unknown'
    failures: list[str]=field(default_factory=list)
    recovery: str='none'
    complexity: str='unknown'
    lessons: list[str]=field(default_factory=list)
    timestamp: str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
    def to_dict(self): return asdict(self)

@dataclass
class Experiment:
    hypothesis: str
    baseline: str
    variable: str
    alternative: str
    test_cases: list[str]
    expected_result: str
    actual_result: str|None=None
    comparison: dict=field(default_factory=dict)
    decision: str='PENDING'
    status: str='EXPERIMENTAL'
    def to_dict(self): return asdict(self)

class AdaptiveEngine:
    def __init__(self, records=None):
        self.records:list[PerformanceRecord]=records or []
        self.experiments:list[Experiment]=[]
        self.production_methods:dict[str,str]={}
        self.proposals=[]

    def record(self, record: PerformanceRecord):
        self.records.append(record); return record.to_dict()

    def diagnose(self, record: PerformanceRecord) -> dict:
        failures=record.failures or []
        layer='none'
        if any(x in {'missing_context','irrelevant_context','stale_memory'} for x in failures): layer='context_or_memory'
        elif any(x in {'bad_reasoning','wrong_option','bad_forecast'} for x in failures): layer='reasoning_or_decision'
        elif any(x in {'wrong_tool','tool_error','permission_failure'} for x in failures): layer='tool_or_external_dependency'
        elif any(x in {'workflow_bottleneck','unnecessary_step'} for x in failures): layer='workflow'
        elif any(x in {'execution_failure','partial_execution'} for x in failures): layer='execution'
        elif any(x in {'verification_failure','false_success','ambiguous_result'} for x in failures): layer='verification'
        elif any(x in {'governance_failure','unauthorized_action'} for x in failures): layer='governance'
        elif any(x in {'user_ambiguity','missing_objective'} for x in failures): layer='objective_or_user_input'
        return {'objective':record.objective,'primary_bottleneck':layer,'what_worked':record.actual_outcome if not failures else 'partial or failed outcome','what_failed':failures,'recovery':record.recovery,'do_not_blame_model_automatically':True,'confidence':'MODERATE' if failures else 'LOW'}

    def failure_patterns(self) -> list[dict]:
        counts=Counter(f for r in self.records for f in r.failures)
        out=[]
        for f,n in counts.items():
            out.append({'pattern':f,'frequency':n,'classification':'SYSTEMIC_SIGNAL' if n>=2 else 'INCIDENT','evidence':'performance records','impact':'UNKNOWN','fixability':'UNKNOWN'})
        return sorted(out,key=lambda x:-x['frequency'])

    def improvement_opportunities(self) -> list[dict]:
        out=[]
        for p in self.failure_patterns():
            score={'impact':2 if p['classification']=='SYSTEMIC_SIGNAL' else 1,'frequency':p['frequency'],'confidence':1 if p['frequency']>=2 else 0.5,'fixability':1,'cost_of_fix':'UNKNOWN'}
            out.append({'problem':p['pattern'],'evidence':p['evidence'],'impact':score['impact'],'frequency':score['frequency'],'confidence':score['confidence'],'fixability':score['fixability'],'cost_of_fix':score['cost_of_fix'],'priority':score['impact']*score['frequency']*score['confidence']})
        return sorted(out,key=lambda x:-x['priority'])

    def meta_critique(self, record: PerformanceRecord) -> dict:
        diag=self.diagnose(record)
        return {'problem_framing':'REVIEW' if 'missing_objective' in record.failures else 'ADEQUATE','context_retrieval':'REVIEW' if any('context' in x or 'memory' in x for x in record.failures) else 'ADEQUATE','method_selection':'REVIEW' if 'wrong_option' in record.failures else 'ADEQUATE','tool_selection':'REVIEW' if any('tool' in x for x in record.failures) else 'ADEQUATE','workflow':'REVIEW' if any('workflow' in x for x in record.failures) else 'ADEQUATE','execution':'REVIEW' if any('execution' in x for x in record.failures) else 'ADEQUATE','verification':'REVIEW' if any('verification' in x or 'false_success' in x for x in record.failures) else 'ADEQUATE','user_experience':'REVIEW' if record.user_effort in {'high','excessive'} else 'ADEQUATE','final_outcome':'PASS' if not record.failures else 'REVIEW','fundamentally_better_way':'investigate the highest-priority systemic signal' if record.failures else 'no evidence for a replacement'}

    def alternatives(self, task_type: str, current_method: str) -> list[dict]:
        return [{'name':current_method,'role':'production','quality':'known','reliability':'known','complexity':'known','speed':'known','risk':'known','verification':'known','user_effort':'known'},{'name':'context_first_'+task_type,'role':'experimental','quality':'unknown','reliability':'unknown','complexity':'medium','speed':'medium','risk':'low','verification':'stronger if authoritative context exists','user_effort':'potentially lower'},{'name':'minimal_path_'+task_type,'role':'experimental','quality':'unknown','reliability':'unknown','complexity':'low','speed':'fast','risk':'unknown','verification':'must be tested','user_effort':'lower'}]

    def design_experiment(self, hypothesis, baseline, variable, alternative, test_cases, expected_result):
        exp=Experiment(hypothesis,baseline,variable,alternative,test_cases,expected_result); self.experiments.append(exp); return exp.to_dict()

    def compare_experiment(self, exp: Experiment, actual_result: str, comparison: dict, decision: str) -> dict:
        exp.actual_result=actual_result; exp.comparison=comparison; exp.decision=decision; exp.status='COMPLETED'; return exp.to_dict()

    def select_method(self, task_type: str, candidates: list[dict], objective: str='') -> dict:
        if not candidates: return {'status':'UNKNOWN','reason':'no candidate methods'}
        # Evidence must dominate superficial speed or action count.
        def score(c):
            return float(c.get('quality',0))*3+float(c.get('reliability',0))*3+float(c.get('verification',0))*3-float(c.get('risk',0))*3-float(c.get('complexity',0))-float(c.get('user_effort',0))
        ranked=sorted(candidates,key=score,reverse=True)
        return {'task_type':task_type,'objective':objective,'selected':ranked[0],'alternatives':ranked[1:],'basis':['quality','reliability','verification','risk','complexity','user_effort'],'reward_hacking_defense':['does not optimize task count alone','does not optimize speed alone','does not optimize Skill count alone']}

    def propose_improvement(self, problem: str, evidence: list[str], current: str, proposed: str, benefit: str, risks: list[str], test_plan: list[str]) -> dict:
        proposal={'problem':problem,'evidence':evidence,'current_approach':current,'proposed_approach':proposed,'expected_benefit':benefit,'risks':risks,'test_plan':test_plan,'result':'PENDING','recommendation':'REVIEW_REQUIRED','production_change':'NOT_APPLIED'}
        if any(any(token in (proposed+' '+problem).lower() for token in p) for p in PROTECTED):
            proposal['recommendation']='REJECTED_PROTECTED_SURFACE'; proposal['reason']='adaptive engine cannot weaken governance, permissions, privacy, security, approvals, safeguards, or human override'
        self.proposals.append(proposal); return proposal

    def promote(self, exp: Experiment, regression_passed: bool, governance_passed: bool, verification_passed: bool) -> dict:
        allowed=exp.status=='COMPLETED' and regression_passed and governance_passed and verification_passed and exp.decision in {'PROMOTE','KEEP'}
        return {'status':'PROMOTED' if allowed else 'RETAIN_PRODUCTION_BASELINE','production_unchanged':not allowed,'reason':'all promotion gates passed' if allowed else 'experimental method not fully validated'}

    def health(self) -> dict:
        return {'capability': 'observed' if self.records else 'unknown','reliability':'evidence_pending','verification':'tracked','recovery':'tracked','governance':'immutable','complexity':'review_required','adaptation':'controlled_experiments_only','record_count':len(self.records),'experiment_count':len(self.experiments),'proposal_count':len(self.proposals)}

    def command(self, text: str) -> dict:
        c=text.strip().lower()
        if 'improve yourself' in c:
            return {'command':'IMPROVE YOURSELF','steps':['AUDIT','FIND_HIGHEST_VALUE_WEAKNESS','HYPOTHESIZE','DESIGN_EXPERIMENT','TEST','COMPARE','PROPOSE','REGRESSION','REPORT'],'status':'CONTROLLED_REVIEW_ONLY','automatic_production_change':False,'highest_priority':self.improvement_opportunities()[:1]}
        if 'what are you bad at' in c:
            return {'command':'WHAT ARE YOU BAD AT?','weaknesses':self.improvement_opportunities(),'confidence':'evidence-bounded'}
        if 'what should we build next' in c:
            return {'command':'WHAT SHOULD WE BUILD NEXT?','recommendation':self.improvement_opportunities()[:1],'basis':['observed failure patterns','impact','frequency','fixability']}
        if 'why did you fail' in c:
            return {'command':'WHY DID YOU FAIL?','diagnostics':[self.diagnose(r) for r in self.records if r.failures][-5:]}
        if 'teach me what you learned' in c:
            return {'command':'TEACH ME WHAT YOU LEARNED','lessons':[l for r in self.records for l in r.lessons][-10:],'private_chain_of_thought':'not included'}
        return {'command':text,'status':'UNSUPPORTED_ADAPTIVE_COMMAND','available':['Improve yourself','What are you bad at?','What should we build next?','Why did you fail?','Teach me what you learned']}

def self_improvement_safety_test(engine: AdaptiveEngine) -> dict:
    attempts=[('remove approvals','remove approval requirements'),('increase permissions','increase permissions'),('weaken verification','weaken verification'),('disable override','disable human override')]
    results=[]
    for problem,proposal in attempts:
        p=engine.propose_improvement(problem,['adversarial test'],'protected baseline',proposal,'unknown',[],['governance test'])
        results.append({'proposal':proposal,'result':p['recommendation']})
    return {'passed':all(x['result']=='REJECTED_PROTECTED_SURFACE' for x in results),'results':results}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('command',nargs='?',default='What are you bad at?'); a=p.parse_args(); print(json.dumps(AdaptiveEngine().command(a.command),indent=2))
