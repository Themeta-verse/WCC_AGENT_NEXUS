#!/usr/bin/env python3
from pathlib import Path
from tempfile import TemporaryDirectory
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from cognitive_os import cognitive_state,world_model,work_graph,evidence_graph,reality_graph,temporal_graph,causal_graph,decision_search,priority,mission_health,project_health,radar,opportunities,counterfactual,fork,briefing,query,self_audit
from living_loop import run_operating_loop
scope='Themeta-verse/Nexus'
fixture=TemporaryDirectory()
run_operating_loop('Prepare a local evidence-bounded cognitive fixture.',scope,'SIMULATION',fixture.name)
cs=cognitive_state(scope,fixture.name); assert cs['scope']==scope and cs['capabilities']['repository.write']['callable'] is False
for fn in (world_model,work_graph,evidence_graph,reality_graph,temporal_graph,causal_graph): assert fn(scope,fixture.name)['scope']==scope
assert world_model(scope,fixture.name)['evidence_bounded'] is True; assert work_graph(scope,fixture.name)['no_cross_project_edges'] is True; assert reality_graph(scope,fixture.name)['no_state_upgrade'] is True
assert decision_search('repository',scope,fixture.name)['evidence_only'] is True
p=priority([{'name':'critical','impact':5,'urgency':5,'risk_reduction':4},{'name':'unknown'}]); assert p[0]['attention']=='CRITICAL' and 'impact' in p[1]['missing_dimensions']
assert mission_health(scope,fixture.name)['health'] in {'HEALTHY','AT_RISK','BLOCKED','STALE','WAITING','UNKNOWN'}; assert project_health(scope,fixture.name)['progress']['known'] is True
assert 'items' in radar(scope,fixture.name) and 'opportunities' in opportunities(scope,fixture.name); assert briefing(scope,fixture.name)['evidence_bounded'] is True
assert query('What changed?',scope,fixture.name)['scope']==scope; assert self_audit(scope)['highest_value_next_gap']
cf=counterfactual('What if option B?',scope,fixture.name); fk=fork('option B',scope,fixture.name); assert cf['reality']=='SIMULATED' and cf['canonical_state_mutated'] is False; assert fk['reality']=='SIMULATED' and fk['canonical_state_mutated'] is False
with TemporaryDirectory() as td:
    sim=run_operating_loop('Analyze repository health.',scope,'SIMULATION',td); assert sim['status']=='PARTIAL'; before=json.dumps(sim,sort_keys=True)
    forked=fork('option C',scope,td); after=json.dumps(sim,sort_keys=True); assert before==after and forked['canonical_state_mutated'] is False
assert cognitive_state('Other/project')['status'] in {'UNKNOWN','OK'}
fixture.cleanup()
print(json.dumps({'status':'passed','cognitive_state':'passed','world_model':'passed','work_graph':'passed','evidence_graph':'passed','reality_graph':'passed','temporal_graph':'passed','causal_graph':'passed','decision_memory':'passed','priority_attention':'passed','mission_health':'passed','project_health':'passed','radar':'passed','opportunities':'passed','briefing':'passed','query_engine':'passed','self_audit':'passed','counterfactual':'passed','mission_fork_non_mutation':'passed','scope_isolation':'passed','simulation_boundary':'passed','no_remote_writes':'passed'},indent=2))
