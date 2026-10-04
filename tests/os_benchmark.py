#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from os_engine import *
from forge_engine import forge_product
from meta_orchestrator import meta_orchestrate

g=GraphStore()
g.add('vision-1','VISION',{'text':'build durable capability'})
g.add('goal-1','GOAL',{'text':'improve research leverage'})
g.add('project-1','PROJECT',{'objective':'verified research briefs','current_state':'ACTIVE'})
g.add('task-1','TASK',{'text':'validate source workflow'})
g.add('decision-1','DECISION',{'text':'use local-first storage'})
g.link('vision-1','contains','goal-1'); g.link('goal-1','contains','project-1'); g.link('project-1','contains','task-1'); g.link('project-1','informed_by','decision-1')
ctx=g.context('project-1','verified research'); assert ctx['entity']['id']=='project-1' and ctx['related']
assert set(goal_hierarchy([{'id':'v','type':'VISION'},{'id':'g','type':'GOAL','parent':'v'},{'id':'p','type':'PROJECT','parent':'g'}])['levels'])>= {'VISION','GOAL','PROJECT'}
conf=detect_conflicts([{'id':'a','resources':['time'],'deadline':'2026-08-20'},{'id':'b','resources':['time'],'deadline':'2026-08-20'}]); assert conf and conf[0]['recommendation'].startswith('surface')
assert priority({'goal_impact':5,'urgency':4,'dependencies':3,'risk':2,'strategic_value':4,'effort':1})['score']>0
assert attention({'id':'x','status':'BLOCKED','goal_impact':1})['bucket']=='BLOCKED'
ps=project_state({'id':'p','objective':'x','current_state':'ACTIVE','health':{}}); assert 'task_graph' in ps['missing']
assert waiting_state('w','approval','user confirms')['status']=='WAITING'
mem=resolve_memory_conflict({'content':'a','confidence':'high','freshness':'2026-08-10','authority':'user'},{'content':'b','confidence':'high','freshness':'2026-08-10','authority':'user'}); assert mem['preserve_uncertainty']
assert knowledge_to_action({'source':'file'},'insight','decision','action')['requires_verification']
assert revisit_decision({'id':'d','assumptions':['a']},['a'])['status']=='REASSESS'
a=automation_contract('event','safe','minimum context','draft','check','record failure','stop on ambiguity'); assert 'authorization' in a['governance']
assert automation_discovery(5,5,1,4,5)['should_automate']
agent=agent_lifecycle('researcher','find evidence',['project-1']); assert agent['minimum_context_only']
ap=autopilot({'id':'p'},[{'id':'t1','status':'BLOCKED'},{'id':'t2','status':'OPEN','goal_impact':5,'urgency':5,'safe_local':True}]); assert 't1' in ap['blocked'] and ap['next']['id']=='t2'
ev=normalize_event({'event':'repo changed','context':['project-1'],'impact':'high','approval_status':'not_required'}); assert ev['event']['event']=='repo changed'
assert one_command('Take care of my project')['false_completion_prevention']
assert reality_map()['UNSUPPORTED']
fb=forge_product('Build a private research brief tool', {'target_user':'researchers','problem':'slow synthesis','success_criteria':['brief'],'constraints':['local'],'non_goals':['social']})
proj=integrate_capabilities(g,fb,{'id':'exp-1','status':'PROPOSED'},{'repository':'Themeta-verse/Nexus','read_only':True}); assert proj['writes_performed'] is False
assert graph_query(g,'project-1','research')['minimum_sufficient_context']
chaos=chaos_day([{'event':'deadline changed','impact':'high'},{'event':'tool failed','impact':'high','verification':'recorded'}]); assert chaos['false_success_prevention']
assert security_center({'risk':'high','authorization':'unknown'})['status']=='BLOCK'
audit=self_audit_os(g, [{'id':'p'}], [a]); assert audit['writes_performed'] is False
meta=meta_orchestrate('What is next for my project?'); assert 'os_operation' in meta
print(json.dumps({'status':'passed','graph':'passed','context_retrieval':'passed','goal_hierarchy':'passed','conflicts':'passed','priority_attention':'passed','project_state':'passed','waiting_open_loop':'passed','memory_trust':'passed','decision_revisit':'passed','automation_governance':'passed','agent_boundary':'passed','autopilot':'passed','event_loop':'passed','one_command':'passed','reality_map':'passed'},indent=2))
