#!/usr/bin/env python3
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from engine_registry import default_registry
from canonical_runtime import compile_request,validate_envelope

r=default_registry(); assert len(r.all())>=10
assert r.get('canonical-runtime').status=='INTEGRATED'
assert r.get('omega4-reality').capabilities
sel=r.select('Audit this repository and determine what should happen next')
assert sel['selected'][0]=='canonical-runtime' and 'capability-registry' in sel['selected'] and 'omega4-reality' in sel['selected']
assert sel['invocation_performed'] is False and sel['external_invocations']==0
G=r.graph(); assert G['relationships_invented'] is False and any(e['type']=='engine_to_contract' for e in G['edges'])
p=compile_request('Prepare my product launch',{'project_id':'p'},'PLAN_ONLY')
assert p['status']=='PLANNED' and p['reality']=='PLANNED'
assert p['engine_selection']['invocation_performed'] is False
assert p['engine_graph']['relationships_invented'] is False
assert validate_envelope(p)['valid']
s=compile_request('Simulate this workflow',{'project_id':'p'},'SIMULATION')
assert s['reality']=='SIMULATED' and s['execution']['status']=='NOT_EXECUTED' and s['writes_performed'] is False
bad=dict(p); bad['status']='COMPLETED'; bad['verification']={'status':'UNKNOWN'}; assert validate_envelope(bad)['valid'] is False
print(json.dumps({'status':'passed','engine_registry':'passed','contracts':'passed','selection':'passed','graph':'passed','canonical_integration':'passed','simulation':'passed','false_completion':'passed','no_side_effects':'passed'},indent=2))
