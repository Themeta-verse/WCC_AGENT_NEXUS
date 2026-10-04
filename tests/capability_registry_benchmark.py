#!/usr/bin/env python3
from pathlib import Path
import json,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from capability_registry import *

r=CapabilityRegistry(); c=r.register_local('canonical-core','LOCAL',['DISCOVER','READ','VERIFY']); assert c.validate(); assert r.health()['available']==1
assert r.select('analyze repository')['selected']
assert r.select('write repository',required_ops=['WRITE'])['selected']==[]
try: r.register(CapabilityRecord(id='bad',name='bad',category='BAD',provider='x',operations=[],authorization='x',risk='x',input_schema='x',output_schema='x',verification_method='x',dependencies=[],failure_modes=[],availability='AVAILABLE',scope='x',source='x'))
except ValueError: pass
else: raise AssertionError('invalid category accepted')
with tempfile.TemporaryDirectory() as d:
    p=Path(d)/'config.json'; p.write_text(json.dumps({'connectors':[{'uid':'gh','name':'GitHub','enabled':True},{'uid':'gmail','name':'Gmail','enabled':False}]}))
    x=CapabilityRegistry(); x.discover_config(str(p)); assert x.discovery_events[-1]['invocations']==0; assert x.records['connector:gh'].operations==['DISCOVER','READ','VERIFY']; assert x.records['connector:gmail'].availability=='UNAVAILABLE'; assert x.select('read repo')['selected']
print(json.dumps({'status':'passed','actual_discovery_contract':'passed','health':'passed','safe_selection':'passed','write_boundary':'passed','provenance':'passed','unavailable_state':'passed','no_invocation':'passed'},indent=2))
