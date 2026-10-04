#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from frontier_research import *

h=hypothesis('Context-first workflow improves verification','relevant context may reduce failure',['verified records'],['sparse history'],{'quality':3},{'risk':1},{'cost':1},0.9,'compare baseline and variant'); assert h['status']=='PROPOSED'
e=Experiment('FR-001','Can context-first improve verification?','context-first improves verification','current','context-first',['context strategy'],['real read','synthetic failure'],{}, {'quality':0.9}, {'risk':1}); assert register_experiment(e)['status']=='PROPOSED'; assert update_experiment(e,'RUNNING')['status']=='RUNNING'; assert update_experiment(e,'PASSED',{'quality':0.9},'PROMOTE',['verification improved'])['decision']=='PROMOTE'
# Pareto must preserve incomparable tradeoffs and remove strictly dominated candidate.
cands=[{'name':'quality','quality':1,'reliability':1,'security':1,'user_value':0.8,'verifiability':1,'maintainability':0.7,'capability':1,'speed':0.5,'complexity':0.6,'cost':0.6,'risk':0.1},{'name':'fast','quality':0.7,'reliability':0.7,'security':0.9,'user_value':0.8,'verifiability':0.7,'maintainability':0.9,'capability':0.8,'speed':1,'complexity':0.2,'cost':0.2,'risk':0.1},{'name':'dominated','quality':0.5,'reliability':0.5,'security':0.5,'user_value':0.4,'verifiability':0.4,'maintainability':0.3,'capability':0.4,'speed':0.3,'complexity':0.9,'cost':0.9,'risk':0.9}]
front=pareto_frontier(cands); assert len(front)==2 and all(x['name']!='dominated' for x in front)
rank=prioritize_experiments([{'name':'high-info','expected_capability_gain':2,'learning_value':3,'user_value':1,'feasibility':1,'risk':0.1,'reversibility':1,'cost':1,'uncertainty_reduction':3},{'name':'busywork','expected_capability_gain':1,'learning_value':0.2,'user_value':0,'feasibility':1,'risk':0.5,'reversibility':0.2,'cost':4,'uncertainty_reduction':0}]); assert rank[0]['name']=='high-info'
assert information_gain([{'name':'high','uncertainty_reduction':3,'testability':1,'cost':1,'risk':0},{'name':'low','uncertainty_reduction':1,'testability':1,'cost':2,'risk':0.5}])[0]['name']=='high'
c={'name':'alternative','easier':['testing'],'harder':['migration'],'new_failure_modes':['state drift'],'complexity':'medium','dependencies':['context'],'scale_behavior':'unknown','failure_behavior':'recoverable','verification_difficulty':'medium'}; assert architecture_critic(c)['recommendation']=='REVIEW_REQUIRED'; assert architecture_red_team(c)['promotion_blocked_until_tested']
assert simulate_architecture(c,{'type':'tool failure'})['classification']=='SIMULATED'
assert workflow_grammar({'input':'x','context':'y','decision':'z','capability':'a','action':'b','checkpoint':'c','verification':'d','recovery':'e','output':'f'})['verification']=='d'
assert stop_condition({'hypothesis_disproven':True,'max_reasonable_effort':'2h'})['stop']
assert emergent_capability(['world model','foresight','verification'],'proactive warning',['real read'], 'repeatable','high')['classification']=='EXPERIMENTAL'
assert github_evolution_plan(['runtime/frontier_research.py'],['frontier benchmark'],['security scan'])['status']=='PREPARE_ONLY'
assert github_evolution_plan(['remove approval boundary'],[],[],authorized=True)['status']=='PREPARE_ONLY'
print(json.dumps({'status':'passed','hypothesis_registry':'passed','lifecycle':'passed','pareto':'passed','prioritization':'passed','information_gain':'passed','architecture_critic':'passed','architecture_red_team':'passed','simulation_labeling':'passed','workflow_grammar':'passed','stop_conditions':'passed','emergent_capability':'passed','github_gating':'passed'},indent=2))
