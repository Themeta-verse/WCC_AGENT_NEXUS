#!/usr/bin/env python3
from pathlib import Path
import sys, json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from canonical_core import *

contracts=[
    Outcome(id='o',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='OBSERVED',intent='i'),
    Objective(id='obj',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='INFERRED',statement='s'),
    Task(id='t',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='OBSERVED',title='t'),
    Workflow(id='w',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='INFERRED'),
    Capability(id='c',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='OBSERVED',name='GitHub',operations=['READ']),
    Execution(id='e',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='OBSERVED',action='read'),
    Verification(id='v',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='OBSERVED',target='t',verification_method='independent',authority='fixture',result='SUCCESS'),
    RepositoryObservation(id='r',status='SUCCESS',source='test',scope='s',failure_state='FAILED',reality='OBSERVED',repository='o/r'),
]
for c in contracts: assert c.validate()
for bad in ('BAD','COMPLETE'):
    try: Outcome(id='x',status=bad,source='t',scope='s',failure_state='f').validate()
    except ValueError: pass
    else: raise AssertionError('invalid result state accepted')
cap=read_only_github_capability(); assert cap.validate(); assert cap.operations==['DISCOVER','READ','VERIFY']; assert 'WRITE' not in cap.operations
assert governance_for('repository read')['state']=='SAFE'
assert governance_for('commit and push')['state']=='BLOCK'
node=EvidenceNode(id='ev',observation='claim',evidence=['tree'],interpretation='bounded',recommendation='test',reality='INFERRED'); assert node.validate()
plan=compile_objective('Analyze repository health'); assert plan['governance']['writes_allowed'] is False; assert plan['capability']['operations']==['DISCOVER','READ','VERIFY']; assert len(plan['workflow']['tasks'])==3
print(json.dumps({'status':'passed','single_source_contracts':'passed','provenance_reality':'passed','verification_contract':'passed','governance':'passed','connector_boundary':'passed','evidence_graph':'passed','thin_orchestration':'passed','no_write_capability':'passed'},indent=2))
