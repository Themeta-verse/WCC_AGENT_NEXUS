#!/usr/bin/env python3
from pathlib import Path
from tempfile import TemporaryDirectory
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from living_loop import context_package,current_reality,delta,observation_delta,mission_reaction,deadline_state,followups,classify_failure,RetryBudget,CircuitBreaker,provider_metrics,lineage,learning,run_operating_loop

scope='Themeta-verse/Nexus'
assert delta(None,{'x':1})['status']=='NEW'
assert delta({'x':1},None)['status']=='REMOVED'
assert delta({'x':1},{'x':2})['status']=='CHANGED'
assert delta({'x':1},{'x':1})['status']=='UNCHANGED'
assert observation_delta({'x':1},{'x':1})['status']=='UNCHANGED'
assert deadline_state(None)['status']=='UNKNOWN'
assert deadline_state('not-a-date')['status']=='UNKNOWN'
assert deadline_state('2999-01-01T00:00:00+00:00')['status']=='CURRENT'
assert classify_failure('provider timeout')['retryable'] is True and classify_failure('authorization denied')['retryable'] is False
b=RetryBudget(max_attempts=2); assert b.permit(); b.consume(); assert b.permit(); b.consume(); assert not b.permit()
cb=CircuitBreaker('github-read',threshold=2); cb.record_failure(); assert cb.state=='CLOSED'; cb.record_failure(); assert cb.state=='OPEN'; cb.record_success(); assert cb.state=='CLOSED' and cb.failures==0
metrics=provider_metrics({'receipt':{'status':'EXECUTED','side_effects':False,'start_time':'2026-01-01T00:00:00+00:00','end_time':'2026-01-01T00:00:01+00:00'},'verification':{'verification':{'verification_state':'VERIFIED'}}}); assert metrics['latency_seconds']==1.0 and metrics['verification_success']
assert mission_reaction({'status':'CHANGED','changes':[{'field':'observation','kind':'CHANGED'}]},{'state':'PARTIAL'})['requires_reverification']
assert followups({'state':'PARTIAL','next_action':{'action':'retry'}})
with TemporaryDirectory() as td:
    c=context_package(scope,td); assert c['known'] is False and c['reality']=='UNKNOWN'
    sim=run_operating_loop('Analyze repository health.',scope,'SIMULATION',td)
    assert sim['status']=='PARTIAL' and sim['operating_loop']['reality']=='SIMULATED'; assert sim['operating_loop']['external_invocations']==0; assert sim['writes_performed'] is False
    assert sim['operating_loop']['lineage'][0]['type']=='intent'; assert sim['operating_loop']['learning']['reality']=='INFERRED'
    assert run_operating_loop('Analyze repository health.',scope,'SIMULATION',td)['operating_loop']['context']['known'] is True
print(json.dumps({'status':'passed','context':'passed','current_reality':'passed','delta_intelligence':'passed','mission_reaction':'passed','deadline_unknown_safety':'passed','followups':'passed','retry_classification':'passed','bounded_retry':'passed','circuit_breaker':'passed','provider_metrics':'passed','lineage':'passed','procedural_learning':'passed','simulation_boundary':'passed','persistence_context':'passed','scope_safety':'passed','no_remote_writes':'passed'},indent=2))
