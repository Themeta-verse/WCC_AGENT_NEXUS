#!/usr/bin/env python3
from pathlib import Path
import sys,json
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from omega4_reality import *

assert transition('UNKNOWN','HYPOTHESIS')=='HYPOTHESIS'
try: transition('SIMULATED','OBSERVED',{'external_observation':True}); raise AssertionError('simulation promoted')
except RealityError: pass
try: transition('PLANNED','EXECUTED'); raise AssertionError('plan executed without evidence')
except RealityError: pass
try: transition('EXECUTED','VERIFIED',{'execution_evidence':True}); raise AssertionError('verified without verification')
except RealityError: pass

g=ClaimGraph(); c=g.add('repository has an unfinished critical task','github-read','project-a','OBSERVED',0.9,observed_at=utc_now(),domain='repository'); g.support(c.claim_id,'tree observation'); assert g.trace(c.claim_id)['source']=='github-read'; assert freshness(c)['status'] in {'fresh','aging'}
assert authority_rank('DIRECT_AUTHORITATIVE_EXTERNAL_OBSERVATION')>authority_rank('SIMULATION')
assert verification_quality('INDEPENDENT')['acceptable'] is True; assert verification_quality('SELF')['acceptable'] is False
m=compare_expected_actual({'files_modified':3},{'files_modified':5}); assert m['status']=='DISCREPANCY'
r=partial_recovery([{'id':'a','status':'SUCCEEDED','verification':'INDEPENDENT'},{'id':'b','status':'FAILED','retryable':False},{'id':'c','status':'PLANNED'}]); assert r['completed']==['a'] and r['failed']==['b'] and r['remaining']==['c']
conf=consistency_check({'capability_status':'UNAVAILABLE','workflow_status':'EXECUTED','execution_status':'SUCCESS','verification_status':'UNKNOWN','reality':'SIMULATED','final_status':'COMPLETED','external_content_instruction':True}); assert not conf['consistent'] and len(conf['conflicts'])>=3
inv=invariant_check({'execution_status':'EXECUTED','authorized':False,'external_observation':True,'source':'','workflow_operation':'WRITE','governance_approved':False}); assert inv['passed'] is False
safe=reconcile({'reality':'SIMULATED','final_status':'PREPARED'}); assert safe['writes_performed'] is False and safe['external_invocations']==0
print(json.dumps({'status':'passed','state_machine':'passed','claim_graph':'passed','authority':'passed','freshness':'passed','verification_quality':'passed','expectation_reality':'passed','partial_recovery':'passed','conflicts':'passed','invariants':'passed','no_external_side_effects':'passed'},indent=2))
