#!/usr/bin/env python3
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from omega3_transcendence import *

x=compile_omega3('Launch my startup',context={'project_id':'project-a'},mode='PLAN_ONLY')
assert x['mode']=='PLAN_ONLY' and x['execution_performed'] is False and x['external_invocations']==0 and x['persisted'] is False
assert x['reality']=='PLANNED' and x['action_state']=='PLANNED'
assert len(x['strategy_search']['candidates'])==5
assert x['strategy_search']['selected']['selected'] is True
assert x['strategy_critique']['revision']
assert x['meta_cognition']['selected_strategy']
assert x['quality_loop']['stages'][0]=='GENERATE' and x['quality_loop']['stages'][-1]=='DELIVER'
assert all(o['status']=='OPTIONAL_OPPORTUNITY' for o in x['opportunities'])
assert x['governance']['writes_allowed'] is False
# Intent expansion without hijack
amp=amplify_intent('Build a portfolio',{'project_id':'p'}); assert amp['user_request']=='Build a portfolio'; assert amp['intent_boundary']
# strategy critique attacks maximum capability and does not promote it
s=search_strategies('complex outcome',available_count=0); mx=[c for c in s['candidates'] if c['name']=='maximum-capability'][0]; cr=critique_strategy(mx); assert cr['weaknesses'] and cr['reality']=='INFERRED'
# reality labels remain non-executed
assert x['omega2']['execution_performed'] is False
print(json.dumps({'status':'passed','meta_cognition':'passed','intent_amplification':'passed','strategy_search':'passed','strategy_scoring':'passed','strategy_critic':'passed','opportunities':'passed','quality_loop':'passed','reality_labels':'passed','governance':'passed','no_side_effects':'passed'},indent=2))
