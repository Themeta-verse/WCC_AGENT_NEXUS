#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from adaptive_intelligence import *

e=AdaptiveEngine()
# Single incident versus repeated systemic signal.
e.record(PerformanceRecord('research X','context-first',['context','research'],['retrieve','analyze'],'accurate answer','partial answer','verified',user_effort='low',failures=['tool_error'],recovery='retry read',complexity='medium',lessons=['source access should be checked']))
e.record(PerformanceRecord('research Y','context-first',['context','research'],['retrieve','analyze'],'accurate answer','partial answer','verified',user_effort='low',failures=['tool_error'],recovery='retry read',complexity='medium',lessons=['source access should be checked']))
patterns=e.failure_patterns(); assert patterns[0]['pattern']=='tool_error'; assert patterns[0]['classification']=='SYSTEMIC_SIGNAL'
# Bottleneck diagnosis must identify the actual layer.
d=e.diagnose(e.records[0]); assert d['primary_bottleneck']=='tool_or_external_dependency'
# Meta-critique must inspect more than output.
c=e.meta_critique(e.records[0]); assert c['tool_selection']=='REVIEW' and c['final_outcome']=='REVIEW'
# Alternative strategy and experiment records.
assert len(e.alternatives('research','context-first'))>=3
ex=e.design_experiment('Context improves task completion','current production','context retrieval','context-first',['research X','research Y'],'higher verified quality')
assert ex['status']=='EXPERIMENTAL'
obj=e.experiments[0]
e.compare_experiment(obj,'higher verified quality',{'quality':1,'verification':1,'user_effort':0},'KEEP')
assert obj.status=='COMPLETED'
assert e.promote(obj,True,True,True)['status']=='PROMOTED'
# Method selection must protect against speed/task-count reward hacking.
choice=e.select_method('research',[{'name':'fast','quality':0.5,'reliability':0.5,'verification':0.2,'risk':0.8,'complexity':0.1,'user_effort':0.1},{'name':'verified','quality':0.9,'reliability':0.9,'verification':0.9,'risk':0.1,'complexity':0.4,'user_effort':0.2}])
assert choice['selected']['name']=='verified'; assert 'does not optimize speed alone' in choice['reward_hacking_defense']
# Protected surfaces must be rejected.
safety=self_improvement_safety_test(e); assert safety['passed']
# Commands are controlled and do not silently mutate production.
assert e.command('Improve yourself')['automatic_production_change'] is False
assert 'weaknesses' in e.command('What are you bad at?')
assert 'private_chain_of_thought' in e.command('Teach me what you learned')
# Health is multidimensional.
h=e.health(); assert h['governance']=='immutable' and 'verification' in h
print(json.dumps({'status':'passed','systemic_failure_detection':'passed','diagnostics':'passed','meta_critique':'passed','experiments':'passed','promotion_gates':'passed','method_selection':'passed','reward_hacking_defense':'passed','protected_surface_rejection':'passed','commands':'passed','health_model':'passed'},indent=2))
