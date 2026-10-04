#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from transcendence_engine import *

e=TranscendenceEngine(); audit={'local_file_count':201,'remote_tracked_file_count':1,'sync_status':'NOT_SYNCHRONIZED'}
state=e.system_state(audit); assert state['repository']['sync_status']=='NOT_SYNCHRONIZED'; assert 'remote repository not synchronized with local implementation' in state['known_weaknesses']
h=e.repository_health(audit); assert h['health']=='INCOMPLETE_SOURCE_OF_TRUTH'; assert h['risks']
assert e.drift(['runtime/execution_engine.py','tests/test.py'],['runtime/execution_engine.py'])['status']=='DRIFT_DETECTED'
debt=e.technical_debt([{'name':'remote divergence','impact':4,'frequency':4,'risk':4,'maintenance_cost':3,'architectural_consequence':4}]); assert debt[0]['class']=='critical'
mem=e.benchmark_memory({'verification':0.8,'user_effort':5},{'verification':0.9,'user_effort':4}); assert mem['comparison']['verification']['classification']=='IMPROVED'
r=e.r_and_d('Can a curated repository export reduce divergence?',['local-only implementation','remote README-only state'],'A reviewed export plus CI will reduce source-of-truth divergence'); assert r['classification']=='EXPERIMENTAL' and not r['production_change']
fg=e.future_state_graph('README-only remote',[{'name':'reviewed export','hypothetical':True}]); assert fg['classification']=='EXPERIMENTAL' and fg['hypothetical']
ce=e.capability_ceiling(); assert 'always-on daemon without deployment' in ce['UNSUPPORTED']; assert 'repository writes' in ce['REQUIRES_USER_AUTHORIZATION']
sp=e.sync_plan(audit); assert sp['status']=='PREPARE_ONLY' and sp['writes_performed'] is False
cc=e.command_center(audit); assert 'what_matters' in cc and 'what_waiting' in cc
print(json.dumps({'status':'passed','living_state':'passed','repository_health':'passed','drift':'passed','technical_debt':'passed','benchmark_memory':'passed','rd_lab':'passed','future_state_labeling':'passed','capability_ceiling':'passed','sync_honesty':'passed','command_center':'passed'},indent=2))
