#!/usr/bin/env python3
"""BEYOND engines: workflow compilation, state, time, causality, counterfactuals, and recovery."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
from pathlib import Path

STATES = {'DISCOVERING','PLANNING','RESEARCHING','BUILDING','WAITING','BLOCKED','VERIFYING','COMPLETED','FAILED','PAUSED','ABANDONED'}


def compile_workflow(intent: str, context: dict | None = None):
    t = intent.lower()
    if any(x in t for x in ('understand','research','learn','what is true')):
        stages = ['intent','objective','success criteria','current state','context','required research','task graph','capabilities','analysis','artifact','verification','memory','follow-up']
    elif any(x in t for x in ('build','launch','create','product')):
        stages = ['intent','objective','success criteria','current state','context','research','task graph','capabilities','resources','design','architecture','prototype','test','verification','memory','follow-up']
    elif any(x in t for x in ('automate','repetitive','workflow')):
        stages = ['intent','objective','trigger','inputs','condition','reasoning','action','output','failure path','approval','simulation','verification','monitoring']
    else:
        stages = ['intent','objective','current state','relevant context','required information','capability discovery','workflow generation','safe execution','verification','memory','follow-up']
    return {'intent': intent, 'stages': stages, 'definition_of_done': 'intended outcome achieved and verified, or explicitly blocked with next action', 'assumptions': [], 'missing_information': ['user-specific objective/context only if it would materially alter the result']}


def state_estimate(evidence: dict):
    return {
        'what_matters': evidence.get('priorities', []),
        'active': evidence.get('active_projects', []),
        'blocked': evidence.get('blocked', []),
        'waiting': evidence.get('waiting', []),
        'changed': evidence.get('changed', []),
        'at_risk': evidence.get('at_risk', []),
        'next': evidence.get('next', []),
        'nexus_can_handle': ['retrieve', 'analyze', 'draft', 'simulate', 'prepare local artifacts', 'verify local outputs'],
        'user_should_handle': ['values', 'direction', 'high-impact approvals', 'private context not supplied', 'irreversible external actions'],
        'evidence_policy': 'unknown fields remain unknown; no personal state is inferred'
    }


def temporal_reasoning(past: list[dict], current: dict, future: list[dict]):
    deadlines = sorted(future, key=lambda x: x.get('date',''))
    return {'past_decisions': past, 'current_state': current, 'upcoming': deadlines, 'unfinished_loops': current.get('open_loops', []), 'sequence': 'past → present → future', 'uncertainty': 'dates and effort are conditional unless verified'}


def causal_analysis(observation: str, causes: list[dict]):
    ranked = sorted(causes, key=lambda x: float(x.get('evidence', 0)), reverse=True)
    return {'observation': observation, 'candidate_causes': ranked, 'leading_hypothesis': ranked[0] if ranked else None, 'evidence_vs_hypothesis': 'candidates are hypotheses until supported by evidence', 'test': 'change or isolate the leading cause with the cheapest safe experiment'}


def counterfactual(options: list[str]):
    return {'scenarios': [{'option': x, 'first_order': 'estimate direct outcome', 'second_order': 'inspect incentives, dependencies, risks, and opportunities', 'reversibility': 'classify reversible versus irreversible', 'uncertainty': 'label as hypothetical'} for x in ['DO NOTHING'] + options], 'decision_rule': 'prefer the cheapest reversible test that reduces uncertainty'}


def opportunity_graph(goals, skills, projects, opportunities):
    edges=[]
    for g in goals:
        for p in projects: edges.append({'from':g,'to':p,'relation':'goal_to_project'})
    for p in projects:
        for s in skills: edges.append({'from':p,'to':s,'relation':'project_can_build_skill'})
    for s in skills:
        for o in opportunities: edges.append({'from':s,'to':o,'relation':'skill_unlocks_opportunity'})
    return {'nodes': list(dict.fromkeys(goals+skills+projects+opportunities)), 'edges': edges, 'filter':'surface only relevant, timely, feasible, strategically aligned opportunities'}


def blind_spots(objective: str, known: list[str]):
    prompts=['assumptions','unknowns','invalidating evidence','hidden dependencies','unconsidered alternatives','adversary view','user view','engineer view','business view']
    return {'objective': objective, 'known': known, 'questions': prompts, 'rule':'surface only blind spots that could materially change the outcome'}


def recovery(last_state: str, completed: list[str], unfinished: list[str], dependencies: list[str]):
    return {'recoverable': bool(last_state or completed or unfinished), 'last_state': last_state, 'completed': completed, 'unfinished': unfinished, 'pending_dependencies': dependencies, 'next_action': unfinished[0] if unfinished else ('resolve '+dependencies[0] if dependencies else None), 'honesty':'do not claim continuity when state is missing'}


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd', required=True)
    x=sub.add_parser('compile'); x.add_argument('intent')
    x=sub.add_parser('state'); x.add_argument('evidence')
    x=sub.add_parser('temporal'); x.add_argument('past'); x.add_argument('current'); x.add_argument('future')
    x=sub.add_parser('causal'); x.add_argument('observation'); x.add_argument('causes')
    x=sub.add_parser('counterfactual'); x.add_argument('options')
    x=sub.add_parser('opportunity'); x.add_argument('payload')
    x=sub.add_parser('blindspots'); x.add_argument('objective'); x.add_argument('known')
    x=sub.add_parser('recover'); x.add_argument('state'); x.add_argument('completed'); x.add_argument('unfinished'); x.add_argument('dependencies')
    a=p.parse_args()
    if a.cmd=='compile': out=compile_workflow(a.intent)
    elif a.cmd=='state': out=state_estimate(json.loads(a.evidence))
    elif a.cmd=='temporal': out=temporal_reasoning(json.loads(a.past),json.loads(a.current),json.loads(a.future))
    elif a.cmd=='causal': out=causal_analysis(a.observation,json.loads(a.causes))
    elif a.cmd=='counterfactual': out=counterfactual(json.loads(a.options))
    elif a.cmd=='opportunity': out=opportunity_graph(**json.loads(a.payload))
    elif a.cmd=='blindspots': out=blind_spots(a.objective,json.loads(a.known))
    else: out=recovery(a.state,json.loads(a.completed),json.loads(a.unfinished),json.loads(a.dependencies))
    print(json.dumps(out,indent=2))

if __name__=='__main__': main()
