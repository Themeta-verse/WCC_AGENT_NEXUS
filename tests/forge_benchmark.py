#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from forge_engine import *

# Minimal intent must remain explicit and must not silently become a complete product spec.
p=compile_product_intent('Build a private reading workflow')
assert p['status']=='NEEDS_CLARIFICATION'
assert 'target_user' in p['missing_information']
assert p['assumptions']==[]

supplied={'product_id':'reader-workflow','target_user':'researchers','problem':'slowly turning sources into verified notes','success_criteria':['verified note produced in under five minutes'],'constraints':['local-first','no secret exposure'],'non_goals':['social feed'],'value_proposition':'reduce research friction'}
blueprint=forge_product('Build a private reading workflow',supplied,evidence=[{'source':'user brief','claim':'research friction exists'}],capabilities={'ai_required':True})
assert blueprint['product']['status']=='DEFINED'
assert len(blueprint['discovery']['directions'])==6
assert blueprint['direction_evaluation']['selected_direction'] is not None
assert blueprint['ux']['states'] and blueprint['ux']['quality_checks']
assert 'security_requirements' in blueprint['product']
assert 'output_validation' in blueprint['ai'] and blueprint['ai']['ai_necessary']
assert blueprint['task_graph']['critical_path']
assert blueprint['execution_boundary']['writes_performed'] is False
assert blueprint['reality']['classification']=='EXPERIMENTAL'

# Explicit security controls are preserved and product compilation never creates permissions.
assert 'authorization and protected resources' in blueprint['product']['security_requirements']
assert 'secret management' in blueprint['product']['security_requirements']
assert blueprint['execution_boundary']['confirm']

# Architecture remains intentionally simple and marks unresolved data requirements.
arch=compile_architecture(blueprint['product'], 'IMPROVED')
assert arch['principle'].startswith('simplest')
data=compile_data_architecture(blueprint['product'])
assert data['status']=='REQUIRES_DOMAIN_INPUT'
ai=compile_ai_feature(blueprint['product'],False)
assert ai['ai_necessary'] is False

print(json.dumps({'status':'passed','explicit_requirements':'passed','no_silent_assumptions':'passed','six_directions':'passed','ux_states_and_quality':'passed','security_controls':'passed','ai_reliability':'passed','task_graph':'passed','reality_labeling':'passed','no_side_effects':'passed','simple_architecture':'passed'},indent=2))
