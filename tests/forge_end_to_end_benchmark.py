#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from forge_engine import *

request='Forge this: Build a lightweight system that helps independent researchers turn saved web sources into verified, searchable briefs.'
result=forge_command(request)
assert result['status']=='COMPILED'
b=result['blueprint']
assert b['product']['status']=='NEEDS_CLARIFICATION'
assert b['product']['missing_information']
assert b['product']['assumptions']==[]
assert len(b['discovery']['directions'])==6
assert b['direction_evaluation']['selected_direction'] is None
assert b['ux']['states']==['empty','loading','partial','success','error','offline/degraded','permission denied']
assert len(b['product']['security_requirements'])>=10
assert b['architecture']['principle'].startswith('simplest')
assert b['data']['status']=='REQUIRES_DOMAIN_INPUT'
assert b['task_graph']['blockers']
assert b['execution_boundary']['writes_performed'] is False
assert result['requires_authorization']
assert product_health()['dimensions'].keys() >= {'functionality','security','ux','operability'}
assert len(product_red_team())==8
attacks=forge_security_red_team()
assert len(attacks)==14 and any(x['attack']=='secret exposure' for x in attacks)
assert deployment_plan(b, {})['classification']=='UNSUPPORTED'
assert github_genesis_plan(b,'Themeta-verse/Nexus')['writes_performed'] is False
assert reawaken_project({'version':1},{'version':2})['what_changed']
print(json.dumps({'status':'passed','stages':result['stages'],'clarification_blockers':b['product']['missing_information'],'security_red_team_cases':len(attacks),'writes_performed':False,'deployment':'UNSUPPORTED'},indent=2))
