#!/usr/bin/env python3
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from canonical_runtime import compile_request,validate_envelope

p=compile_request('Prepare my product launch',{'project_id':'launch-a'},'PLAN_ONLY')
assert p['status']=='PLANNED' and p['reality']=='PLANNED' and p['action_state']=='PLANNED'
assert p['execution']['status']=='NOT_EXECUTED' and p['observation']['status']=='NOT_OBSERVED'
assert p['verification']['status']=='UNKNOWN' and p['external_invocations']==0 and p['writes_performed'] is False
assert validate_envelope(p)['valid']
s=compile_request('Build this',{'project_id':'p'},'SIMULATION')
assert s['status']=='PREPARED' and s['reality']=='SIMULATED' and s['preparation']['side_effects'] is False
assert s['execution']['status']=='NOT_EXECUTED' and s['observation']['reality']=='UNKNOWN'
# Forced false state must be rejected by envelope validation.
bad=dict(p); bad['status']='COMPLETED'; bad['verification']={'status':'UNKNOWN'}; assert validate_envelope(bad)['valid'] is False
assert p['provenance']==['omega3_transcendence','omega2_intelligence','omega4_reality','canonical_core']
print(json.dumps({'status':'passed','canonical_envelope':'passed','provenance':'passed','plan_execution_separation':'passed','observation_verification_separation':'passed','simulation_boundary':'passed','false_completion_rejection':'passed','no_writes':'passed','no_external_invocations':'passed'},indent=2))
