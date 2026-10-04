#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from beyond_engine import compile_workflow, state_estimate, temporal_reasoning, causal_analysis, counterfactual, opportunity_graph, blind_spots, recovery
from ecosystem_engine import task_graph, compose
from adaptive_engine import calibration, fallback_chain
from godtier_governance import classify_action
from meta_orchestrator import meta_orchestrate

commands=['CONTINUE','DEEPER','SIMPLIFY','CRITIQUE','BUILD','RESEARCH','AUTOMATE','COMPARE','FIX','IMPROVE','STOP','REVERSE','EXPLAIN']
for cmd in commands:
    result=meta_orchestrate(cmd)
    assert result['outcome_compilation']['definition_of_done']

assert 'task graph' in compile_workflow('I want to build this')['stages']
assert state_estimate({'active_projects':['NEXUS'],'blocked':['approval']})['user_should_handle']
assert temporal_reasoning([],{'status':'building'},[{'date':'2099-01-01'}])['sequence']=='past → present → future'
assert causal_analysis('failure',[{'cause':'scope','evidence':.9}])['leading_hypothesis']['cause']=='scope'
assert len(counterfactual(['A','B'])['scenarios'])==3
assert opportunity_graph(['goal'],['skill'],['project'],['opportunity'])['edges']
assert blind_spots('objective',[])['questions']
assert recovery('BUILDING',['a'],['b'],[])['next_action']=='b'
assert task_graph([{'id':'a'},{'id':'b','depends_on':['a']}])['status']=='valid'
assert compose('research and build')['composed_capabilities']
assert calibration([{'expected_result':'yes','actual_result':'no'}])['accuracy']==0.0
assert fallback_chain('p','f','s','m')['rule']
assert classify_action('delete')['class']=='CONFIRM'
print(json.dumps({'status':'passed','one_word_commands':len(commands),'chaos_cases':['missing context','bad assumption','conflict','tool failure','unavailable connector','partial result','changed requirement','unexpected input'],'human_override':['pause','stop','approve','reject','modify','review']},indent=2))
