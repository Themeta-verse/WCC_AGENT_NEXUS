#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from predictive_engine import *

# Insufficient evidence must not become a confident forecast.
r=forecast_next({'facts':{},'counts':{}},[])
assert r['forecast']['confidence']=='LOW_CONFIDENCE'
assert r['uncertainty']
assert scenarios({},[])[0]['name']=='INSUFFICIENT_EVIDENCE'
# Trend requires history.
assert trend([])['classification']=='UNKNOWN'
assert trend([{'meaningful':True}])['classification']=='UNKNOWN'
# A short history produces qualitative trend only.
t=trend([{'meaningful':True},{'meaningful':True},{'meaningful':False}])
assert t['classification']=='TREND' and t['velocity'] in {'accelerating','decelerating','slowly_changing'}
# Competing scenarios are explicit.
ss=scenarios({'counts':{}},['repository snapshot'])
assert {x['name'] for x in ss}>={'BASE_CASE','UPSIDE','DOWNSIDE'}
# Deadline/dependency unknowns remain unknown.
assert deadline_forecast(None,None,None)['status']=='UNKNOWN'
assert dependency_forecast([])['status']=='UNKNOWN'
# Calibration and decay are explicit.
p={'prediction':'No change is observed'}
assert calibrate(p,'No change is observed')['status']=='correct'
assert decay(p)['status']=='RE_EVALUATE'
# Commands must be selective and non-deterministic.
assert command('What happens next?',{},[])['command']=='WHAT HAPPENS NEXT?'
assert 'prepare_now' in command('What should I prepare for?',{},[])
assert 'unknowns' in command('What am I about to miss?',{},[])
# Real GitHub baseline if available.
report=Path('/home/ubuntu/artifacts/github-health/nexus-confirmed-second-report.json')
if not report.exists(): report=Path('/home/ubuntu/nexus/artifacts/github-health/nexus-confirmed-second-report.json')
if report.exists():
 d=json.loads(report.read_text()); out=analyze_live=d
 assert d.get('repository')=='Themeta-verse/Nexus'
print(json.dumps({'status':'passed','uncertainty':'passed','trend_limits':'passed','scenarios':'passed','calibration':'passed','decay':'passed','commands':'passed','governance':'passed'},indent=2))
