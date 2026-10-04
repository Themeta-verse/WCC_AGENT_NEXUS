#!/usr/bin/env python3
"""GitHub -> External Intelligence adapter.

The existing github_live_loop remains the source-of-truth reader. This adapter
normalizes its report and applies the shared world-model, impact, priority,
and memory pipeline.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from github_live_loop import run
from external_intelligence import SignalBus, WorldModel, normalize_github_health, state_bridge

def process(repo: str, project: str='nexus-v3') -> dict:
    report=run(repo)
    if report.get('status')=='failed_safely':
        return {'status':'failed_safely','report':report,'world_model':None,'signals':[],'state_updates':[]}
    signals=normalize_github_health(report,project)
    bus=SignalBus(project); world=WorldModel(); updates=[]
    accepted=[]
    for signal in signals:
        ing=bus.ingest(signal)
        if ing['accepted']:
            accepted.append(signal.to_dict())
            updates.append(state_bridge(signal,world))
    return {'status':'ok','repository':repo,'signals':accepted,'state_updates':updates,'what_changed':bus.what_changed(),'world_model':world.snapshot(),'source_verification':report.get('verification',{}),'raw_report':report}

def main():
    p=argparse.ArgumentParser(); p.add_argument('repo'); p.add_argument('--project',default='nexus-v3'); a=p.parse_args(); print(json.dumps(process(a.repo,a.project),indent=2))
if __name__=='__main__': main()
