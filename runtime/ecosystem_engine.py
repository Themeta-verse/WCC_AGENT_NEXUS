#!/usr/bin/env python3
"""Extreme Mode engines: task graphs, registry, composition, memory leverage, perception planning."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = Path('/home/ubuntu/skills')


def task_graph(tasks: list[dict]):
    """Return dependency order, parallel layers, blockers, and a simple critical path."""
    by_id = {t['id']: t for t in tasks}
    deps = {tid: set(t.get('depends_on', [])) for tid, t in by_id.items()}
    unknown = sorted({d for ds in deps.values() for d in ds if d not in by_id})
    layers = []
    remaining = set(by_id)
    while remaining:
        ready = sorted(tid for tid in remaining if not (deps[tid] & remaining))
        if not ready:
            return {'status':'invalid', 'cycle_or_blocker': sorted(remaining), 'unknown_dependencies': unknown}
        layers.append(ready)
        remaining -= set(ready)
    critical = []
    for layer in layers:
        critical.append(max(layer, key=lambda tid: float(by_id[tid].get('weight', 1))))
    return {'status':'valid', 'layers': layers, 'parallelizable': [x for layer in layers for x in layer], 'critical_path': critical, 'unknown_dependencies': unknown, 'blockers': [tid for tid,t in by_id.items() if t.get('blocked')]}


def registry():
    records = []
    for p in sorted(SKILLS_ROOT.glob('nexus-*/SKILL.md')):
        text = p.read_text(errors='ignore')
        name = p.parent.name
        desc = ''
        for line in text.splitlines():
            if line.lower().startswith('description:'):
                desc = line.split(':',1)[1].strip()
        records.append({'name': name, 'purpose': desc or 'See SKILL.md', 'path': str(p), 'validated': True, 'version': 'local'})
    return {'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'skills': records, 'count': len(records), 'composition_rule': 'compose by outcome, inputs, risk, and quality bar; do not invoke every Skill'}


def compose(outcome: str, records: list[dict] | None = None):
    t = outcome.lower()
    selected = []
    for key, names in {
        'research': ['research','evidence','true','investigate'],
        'decision': ['decision','compare','choose','recommend','what should'],
        'creative': ['creative','better','memorable','concept','idea'],
        'product': ['build','product','app','website','launch'],
        'automation': ['automate','repetitive','workflow'],
        'project': ['project','blocked','progress','continue','what changed'],
        'learning': ['learn','study','practice','skill'],
    }.items():
        if any(x in t for x in names): selected.append(key)
    if not selected: selected = ['context','dynamic-workflow']
    return {'outcome': outcome, 'composed_capabilities': list(dict.fromkeys(selected + ['verification'])), 'temporary_workflow': True, 'approval': 'derive from action risk; external side effects remain confirmation-gated'}


def memory_leverage(memories: list[dict], future_decision: str):
    q = set(re.findall(r'[a-z0-9]+', future_decision.lower()))
    scored = []
    for m in memories:
        text = ' '.join(str(m.get(k,'')) for k in ('content','topic','why','connected_to')).lower()
        overlap = len(q & set(re.findall(r'[a-z0-9]+', text)))
        verified = 1 if str(m.get('confidence', m.get('CONFIDENCE',''))).lower() == 'high' else 0
        durable = 1 if str(m.get('class', m.get('CLASS',''))).upper() in {'PERSISTENT','DECISION','FACT','LESSON','RESOURCE','PREFERENCE'} else 0
        score = overlap + verified + durable
        if score: scored.append({'memory': m, 'leverage_score': score, 'decision': 'retain/use' if score >= 2 else 'consider'})
    return sorted(scored, key=lambda x: -x['leverage_score'])


def perception_plan(path: str):
    ext = Path(path).suffix.lower()
    modality = {'text': {'.txt','.md','.json','.csv','.py','.ts','.tsx'}, 'document': {'.pdf','.docx','.pptx','.xlsx'}, 'image': {'.png','.jpg','.jpeg','.webp','.gif'}, 'audio': {'.mp3','.wav','.m4a','.webm'}, 'video': {'.mp4','.mov','.mkv'}}
    kind = next((k for k,v in modality.items() if ext in v), 'unknown')
    return {'path': path, 'modality': kind, 'recommended_analysis': {'text':'extract and retrieve selectively','document':'extract text and inspect layout','image':'inspect visually and OCR if dense','audio':'transcribe then analyze','video':'sample frames and analyze motion'} .get(kind, 'ask for supported artifact or treat as opaque'), 'status': 'plan-only until the artifact is supplied'}


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd', required=True)
    x=sub.add_parser('graph'); x.add_argument('tasks')
    sub.add_parser('registry')
    x=sub.add_parser('compose'); x.add_argument('outcome')
    x=sub.add_parser('memory'); x.add_argument('decision'); x.add_argument('memories')
    x=sub.add_parser('perception'); x.add_argument('path')
    a=p.parse_args()
    if a.cmd=='graph': out=task_graph(json.loads(a.tasks))
    elif a.cmd=='registry': out=registry()
    elif a.cmd=='compose': out=compose(a.outcome)
    elif a.cmd=='memory': out=memory_leverage(json.loads(a.memories), a.decision)
    else: out=perception_plan(a.path)
    print(json.dumps(out, indent=2))

if __name__=='__main__': main()
