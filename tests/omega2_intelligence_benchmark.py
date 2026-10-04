#!/usr/bin/env python3
from pathlib import Path
import sys,json,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from omega2_intelligence import *
from capability_registry import CapabilityRegistry

r=CapabilityRegistry(); r.register_local('local-analysis','LOCAL',['DISCOVER','READ','VERIFY'])
plan=compile_omega2('Launch my startup',context={},registry=r,mode='PLAN_ONLY')
assert plan['mode']=='PLAN_ONLY' and plan['execution_performed'] is False and plan['external_invocations']==0
assert plan['outcome']['action_state']=='PLANNED'; assert plan['reality']=='UNKNOWN'
assert any(g['status']=='UNKNOWN' for g in plan['knowledge_gaps'])
assert plan['workflow']['mode']=='SIMULATION' and plan['workflow']['external_side_effects'] is False
sim=compile_omega2('Research the repository',context={'project_id':'project-a'},registry=r,mode='SIMULATION')
assert sim['workflow']['reality']=='SIMULATED'; assert sim['project_state']['project_id']=='project-a'; assert sim['external_invocations']==0
assert route_command('WHAT-IF GitHub fails')['mode']=='SIMULATION'
assert route_command('BUILD product')['recognized']
d=compile_decision('choose a provider'); assert any(o['name']=='do nothing' for o in d.options); assert d.reality=='INFERRED'
s=ProjectState('project-a','x','BLOCKED',dependencies=['task-a'],blockers=['missing input']); t=temporal_analysis(s); assert t['future']=='AT_RISK' and t['critical_path']==['task-a']
# project isolation: explicit project id is carried, never mixed
p1=compile_omega2('Analyze project A',{'project_id':'A'},r,'PLAN_ONLY'); p2=compile_omega2('Analyze project B',{'project_id':'B'},r,'PLAN_ONLY'); assert p1['project_state']['project_id']=='A' and p2['project_state']['project_id']=='B'
assert p1['governance']['writes_allowed'] is False
print(json.dumps({'status':'passed','state_labels':'passed','intent_outcome':'passed','knowledge_gaps':'passed','shadow_simulation':'passed','minimum_selection':'passed','temporal':'passed','decision':'passed','command_router':'passed','project_isolation':'passed','governance':'passed','no_external_invocation':'passed'},indent=2))
