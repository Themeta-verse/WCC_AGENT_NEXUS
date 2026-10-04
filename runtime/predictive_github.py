#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
from github_live_loop import run
from predictive_engine import forecast_next,prepare_for,what_miss,momentum,trend,dependency_forecast

def analyze(repo: str, history: list[dict]|None=None) -> dict:
    report=run(repo)
    if report.get('status')=='failed_safely': return {'status':'failed_safely','report':report}
    history=history or []
    current=report['snapshot']
    return {'status':'ok','repository':repo,'current_state':{'facts':current.get('facts',{}),'counts':current.get('counts',{})},'trend':trend(history),'momentum':momentum(current,history),'what_happens_next':forecast_next(current,history),'prepare_for':prepare_for(current,history),'what_might_be_missed':what_miss(current,{}),'dependency_forecast':dependency_forecast([]),'governance':{'forecasts_authorize_no_external_action':True,'writes_performed':report['verification']['writes_performed']},'source_verification':report['verification']}

def main():
 p=argparse.ArgumentParser(); p.add_argument('repo'); p.add_argument('--history-file'); a=p.parse_args(); h=json.loads(Path(a.history_file).read_text()) if a.history_file else []; print(json.dumps(analyze(a.repo,h),indent=2))
if __name__=='__main__': main()
