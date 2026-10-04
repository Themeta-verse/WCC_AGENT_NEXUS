#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from execution_engine import *

eng=ExecutionEngine(); de=DecisionEngine()
# Decision compilation must separate decision from forecast and expose options.
d=de.compile('I need to launch this',current_state={'stage':'planning'},evidence=['repository exists'])
assert d.recommended_option=='research_first'
assert d.status=='RESEARCH'
assert len(d.available_options)>=3
# Missing verification contract blocks.
bad=ActionContract('publish release','PUBLISH',{},'release published','high','external publication','confirmation','','')
r=eng.execute('launch',d,bad,lambda:{'ok':True},lambda x:{'status':'verified'},confirmation=False)
assert r.status=='BLOCKED'
# Safe local action succeeds only after independent verification.
state={'value':0}
a=ActionContract('increment local state','MODIFY',{},'value becomes 1','low','local state change','local','read state equals 1','restore prior value')
r=eng.execute('test local update',d,a,lambda:state.update(value=1) or {'value':state['value']},lambda x:{'status':'verified' if state['value']==1 else 'failed'},confirmation=True)
assert r.status=='SUCCESS'
assert quality_score(r)['verification_quality']=='high'
# Duplicate action is prevented.
r2=eng.execute('test local update',d,a,lambda:state.update(value=2),lambda x:{'status':'verified'},confirmation=True)
assert r2.status=='CANCELLED'
# False success: operation returns success but authoritative state did not change.
state2={'value':0}; a2=ActionContract('fake update','MODIFY',{},'value becomes 1','low','local state change','local','authoritative value equals 1','restore prior value')
r3=eng.execute('false success',d,a2,lambda:{'claimed':'done'},lambda x:{'status':'verified' if state2['value']==1 else 'failed'},confirmation=True)
assert r3.status=='FAILED'
assert 'false-success' in (r3.lesson or '')
# Tool failure must be FAILED, never SUCCESS.
a3=ActionContract('failing read','READ',{},'read result','low','none','none','result exists','retry read')
r4=eng.execute('failure',d,a3,lambda:1/0,lambda x:{'status':'verified'})
assert r4.status=='FAILED' and r4.recovery
# Ambiguous verification yields UNKNOWN.
a4=ActionContract('ambiguous read','READ',{},'read result','low','none','none','independent state read','retry after inspection')
r5=eng.execute('ambiguous',d,a4,lambda:{'claimed':'ok'},lambda x:{'status':'ambiguous'})
assert r5.status=='UNKNOWN'
# Approval queue and autonomy ceiling.
assert approval_request(bad)['state']=='PENDING_APPROVAL'
assert autonomy_ceiling()['level']==3
print(json.dumps({'status':'passed','decision_compilation':'passed','approval_gate':'passed','safe_execution':'passed','idempotency':'passed','false_success_prevention':'passed','failure_recovery':'passed','ambiguous_verification':'passed','autonomy_ceiling':'passed'},indent=2))
