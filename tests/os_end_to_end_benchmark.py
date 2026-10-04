#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from os_engine import *

# New project: minimal context produces a bounded plan rather than fabricated certainty.
g=GraphStore(); g.add('new-goal','GOAL',{'objective':'improve research leverage'},scope='research')
new=one_command('What am I missing',g); assert new['minimum_sufficient_context'] if 'minimum_sufficient_context' in new else new['false_completion_prevention']
# Old project: current state wins for continuation, history is preserved.
old=reawaken({'version':1,'state':'prototype'},{'version':2,'state':'changed','stale_assumptions':['old API'],'next_action':'inspect current repository'}); assert old['changed'] and old['stale_assumptions']
# Multi-project: conflicts are surfaced, not auto-resolved.
portfolio_out=portfolio([{'id':'p1','current_state':'ACTIVE','resources':['time'],'deadline':'soon','goal_impact':5},{'id':'p2','current_state':'BLOCKED','resources':['time'],'deadline':'soon','goal_impact':4}]); assert portfolio_out['conflicts'] and 'surface' in portfolio_out['recommendation']
# Research/decision/automation paths.
assert knowledge_to_action({'source':'verified note'},'evidence gap','research decision','ask user')['status']=='ACTION_PROPOSED'
assert revisit_decision({'id':'d1','assumptions':['market current']},['market current'])['status']=='REASSESS'
assert automation_contract('repo event','safe','project context','draft update','test','record failure','stop on ambiguity')['status']=='PROPOSED'
# GitHub is represented as a graph node, but no write is implied.
g.add('repo','REPOSITORY',{'name':'Themeta-verse/Nexus','read_only':True}); assert integrate_capabilities(g,github_state={'repository':'Themeta-verse/Nexus'})['writes_performed'] is False
# Chaos and zero trust.
chaos=chaos_day([{'event':'deadline changed','impact':'high'},{'event':'tool failed','impact':'high'},{'event':'github changed','impact':'high','verification':'read verified'}]); assert chaos['false_success_prevention'] and chaos['state_consistency']=='REQUIRES_REVIEW'
assert security_center({'risk':'critical','authorization':'unknown','verification':None})['status']=='BLOCK'
assert classify('SIMULATED',['synthetic chaos'])['classification']=='SIMULATED'
# Resource/attention decisions do not pretend unavailable resources exist.
assert attention({'id':'low','status':'OPEN','goal_impact':1})['bucket']=='CAN_WAIT'
print(json.dumps({'status':'passed','scenarios':['new_project','old_project','multi_project','research','decision','automation','github','failure','recovery','conflict','chaos_day','zero_trust'],'state_consistency':'bounded','false_success_prevention':True,'writes_performed':False},indent=2))
