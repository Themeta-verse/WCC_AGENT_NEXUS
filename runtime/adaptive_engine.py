#!/usr/bin/env python3
"""Controlled adaptive utilities; all learning is observable and non-self-modifying."""
from __future__ import annotations
import datetime as dt
import json


def impact_propagation(change: dict):
    kind = change.get('kind', 'unknown')
    mapping = {
        'deadline': ['project risk', 'task priority', 'dependencies', 'command center'],
        'decision': ['project plan', 'next actions', 'risks', 'memory'],
        'connector': ['available capabilities', 'automation health', 'approval boundary'],
        'failure': ['workflow status', 'fallback path', 'lesson', 'verification'],
    }
    return {'change': change, 'affected_dimensions': mapping.get(kind, ['relevant linked records']), 'propagate_only_if_relevant': True}


def decision_record(decision, context, options, reasoning, choice, expected_result):
    return {'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(), 'decision': decision, 'context': context, 'options': options, 'reasoning': reasoning, 'choice': choice, 'expected_result': expected_result, 'actual_result': None, 'lesson': None}


def outcome_learning(record: dict, actual_result: str):
    expected = str(record.get('expected_result', '')).lower()
    actual = actual_result.lower()
    aligned = expected == actual or expected in actual or actual in expected
    return {**record, 'actual_result': actual_result, 'lesson': 'method appears calibrated' if aligned else 'inspect assumptions, method choice, and missing context', 'alignment': aligned}


def calibration(records: list[dict]):
    scored=[]
    for r in records:
        if r.get('actual_result') is None: continue
        expected=str(r.get('expected_result','')).lower(); actual=str(r.get('actual_result','')).lower()
        scored.append(expected == actual or expected in actual or actual in expected)
    return {'observations': len(scored), 'accuracy': round(sum(scored)/len(scored), 3) if scored else None, 'warning': 'not enough observations for calibration' if len(scored)<5 else None}


def best_method(methods: list[dict]):
    def score(m):
        return float(m.get('quality',0))*0.35 + float(m.get('reliability',0))*0.30 + float(m.get('speed',0))*0.10 - float(m.get('risk',0))*0.15 - float(m.get('complexity',0))*0.10
    ranked=sorted([{**m, 'selection_score': round(score(m),3)} for m in methods], key=lambda x: -x['selection_score'])
    return {'selected': ranked[0] if ranked else None, 'ranked': ranked, 'basis': ['quality','reliability','speed','risk','available tools','complexity']}


def fallback_chain(primary, fallback, second_fallback, manual):
    return {'primary': primary, 'fallback': fallback, 'second_fallback': second_fallback, 'safe_manual_preparation': manual, 'rule': 'do not repeat a known failed method without new evidence'}


def architecture_review(inventory: dict):
    skills=inventory.get('skills',[]); names=[s.get('name') for s in skills]
    duplicates=sorted({n for n in names if names.count(n)>1})
    return {'skill_count': len(names), 'duplicate_names': duplicates, 'questions': ['Are Skills overlapping?','Are workflows reliable?','Are automations valuable?','Is memory useful?','Are connectors used?','What should be removed?','What should be added?'], 'status':'review-ready'}


def main():
    import argparse
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd', required=True)
    x=sub.add_parser('impact'); x.add_argument('change')
    x=sub.add_parser('record'); x.add_argument('payload')
    x=sub.add_parser('learn'); x.add_argument('record'); x.add_argument('actual')
    x=sub.add_parser('calibrate'); x.add_argument('records')
    x=sub.add_parser('method'); x.add_argument('methods')
    x=sub.add_parser('fallback'); x.add_argument('primary'); x.add_argument('fallback'); x.add_argument('second'); x.add_argument('manual')
    x=sub.add_parser('review'); x.add_argument('inventory')
    a=p.parse_args()
    if a.cmd=='impact': out=impact_propagation(json.loads(a.change))
    elif a.cmd=='record': out=decision_record(**json.loads(a.payload))
    elif a.cmd=='learn': out=outcome_learning(json.loads(a.record), a.actual)
    elif a.cmd=='calibrate': out=calibration(json.loads(a.records))
    elif a.cmd=='method': out=best_method(json.loads(a.methods))
    elif a.cmd=='fallback': out=fallback_chain(a.primary,a.fallback,a.second,a.manual)
    else: out=architecture_review(json.loads(a.inventory))
    print(json.dumps(out,indent=2))

if __name__=='__main__': main()
