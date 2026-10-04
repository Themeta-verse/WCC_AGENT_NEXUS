#!/usr/bin/env python3
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from outcome_compiler import compile_outcome
from meta_orchestrator import meta_orchestrate
from ecosystem_engine import task_graph, compose, memory_leverage, perception_plan
from adaptive_engine import impact_propagation, best_method, fallback_chain, calibration
from godtier_governance import classify_action, decay

cases = [
    ('vague goal', 'I want something important done'),
    ('ambiguous request', 'Handle this'),
    ('contradictory context', 'Compare conflicting options and decide'),
    ('complex research', 'Research this deeply and tell me what matters'),
    ('strategic decision', 'Find the best option and explain what would change your recommendation'),
    ('product creation', 'Build this product'),
    ('creative challenge', 'Make this better and unforgettable'),
    ('automation', 'This keeps happening; automate it'),
    ('missing information', 'Launch this'),
    ('continue', 'Continue where we left off'),
    ('find valuable', 'Find something valuable'),
    ('stop', 'What should I stop doing?'),
]
outputs=[]
for name, request in cases:
    compiled=compile_outcome(request); meta=meta_orchestrate(request)
    assert compiled['definition_of_done'] and meta['temporary_roles'] and meta['ecosystem']['task_graph']['status']=='valid'
    outputs.append({'case':name,'mode':compiled['intent']['mode'],'roles':meta['temporary_roles']})

graph=task_graph([{'id':'A'},{'id':'B','depends_on':['A']},{'id':'C','depends_on':['A']},{'id':'D','depends_on':['B','C']}])
assert graph['layers']==[['A'],['B','C'],['D']]
assert task_graph([{'id':'A','depends_on':['B']},{'id':'B','depends_on':['A']}])['status']=='invalid'
assert {'product','research','automation','verification'} <= set(compose('Build and research a product and automate the workflow')['composed_capabilities'])
assert memory_leverage([{'class':'DECISION','content':'launch priority','confidence':'high'},{'class':'EPHEMERAL','content':'unrelated','confidence':'low'}],'what is the launch priority?')[0]['decision']=='retain/use'
assert perception_plan('screen.png')['modality']=='image'
assert 'project risk' in impact_propagation({'kind':'deadline'})['affected_dimensions']
assert best_method([{'name':'a','quality':.9,'reliability':.8,'speed':.5,'risk':.2,'complexity':.2}])['selected']['name']=='a'
assert fallback_chain('p','f','s','m')['second_fallback']=='s'
assert calibration([{'expected_result':'yes','actual_result':'yes'}])['accuracy']==1.0
assert classify_action('publish')['approval_required']
assert decay({'CLASS':'RESOURCE'})['decision']=='verify_freshness'
print(json.dumps({'status':'passed','cases':outputs,'quality_dimensions':['correctness','usefulness','context','reasoning','creativity','execution','autonomy','reliability','verification','explainability'],'chaos':['cycles','missing_context','stale_memory','approval_gate','fallback']},indent=2))
