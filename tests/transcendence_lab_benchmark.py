#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from transcendence_lab import *

roles=compose_roles('improve repository health'); assert roles['permanent_personalities_created'] is False and 'VERIFIER' in roles['temporary_roles']
council=adversarial_council('publish an unreviewed change'); assert 'red_team' in council and 'risk_reviewer' in council
rank=prioritize_experiments([{'name':'low-cost-high-learning','expected_impact':3,'confidence':0.8,'learning_value':3,'reversibility':1,'cost':1},{'name':'expensive','expected_impact':3,'confidence':0.5,'learning_value':1,'reversibility':0.5,'cost':5}]); assert rank[0]['name']=='low-cost-high-learning'
f=fuse_capabilities('world model','foresight',['verified state']); assert f['classification']=='EXPERIMENTAL' and f['test_required']
o=discover_opportunities([{'evidence':'verified GitHub change','relevance':0.8}]); assert len(o)==1 and 'RESEARCH' in o[0]['decision_options']
ca=complexity_audit([{'name':'A','usage_count':0,'capabilities':[]},{'name':'A','usage_count':1,'capabilities':['x','y','z','w']}]); assert ca['status']=='SIMPLIFICATION_REVIEW'
assert len(self_red_team())>=10
assert safe_experiment(LabExperiment('remove approval','weaken approval gate',[], 'high','low','high','high'))['status']=='REJECTED_PROTECTED_SURFACE'
assert safe_experiment(LabExperiment('test retrieval','context-first retrieval improves verification',[], 'high','low','high','high'))['status']=='ELIGIBLE_FOR_SANDBOX'
print(json.dumps({'status':'passed','temporary_roles':'passed','adversarial_council':'passed','experiment_prioritization':'passed','capability_fusion':'passed','opportunity_discovery':'passed','complexity_audit':'passed','self_red_team':'passed','protected_surface_rejection':'passed'},indent=2))
