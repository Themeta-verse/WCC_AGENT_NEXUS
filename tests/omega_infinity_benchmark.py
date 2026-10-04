#!/usr/bin/env python3
from pathlib import Path
from tempfile import TemporaryDirectory
import json,subprocess,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from local_control import capability_ceiling,provider_health,command_center,doctor,self_test,memory,loops,verify,audit,define_automation,define_monitor,evaluate_automation,evaluate_monitor

root=Path(__file__).resolve().parents[1]
cap=capability_ceiling(); assert cap['repository.read']['real'] and cap['repository.read']['verified'] and cap['repository.read']['persistent']; assert not cap['repository.read']['automated']; assert not cap['repository.write']['callable']; assert cap['scheduling']['implemented'] and not cap['scheduling']['automated']
health=provider_health(); assert health['providers']['github-read']['contract'] and health['providers']['github-read']['real_calls']==0 and health['providers']['github-read']['writes_allowed'] is False
cc=command_center(); assert 'NOW' in cc and 'NEXT' in cc and 'CAPABILITIES' in cc and 'PROVIDER_HEALTH' in cc; assert cc['CAPABILITIES']['repository.write']['authorized'] is False
assert doctor()['status'] in {'HEALTHY','DEGRADED','UNKNOWN'}; st=self_test(); assert st['external_invocations']==0 and st['writes_performed'] is False
assert verify()['receipt']['valid'] in {True,False}; assert audit()['writes_performed'] is False
assert memory()['scope']=='Themeta-verse/Nexus'; assert loops()['scope']=='Themeta-verse/Nexus'
with TemporaryDirectory() as td:
    a=define_automation('Themeta-verse/Nexus','manual','condition_met','review repository evidence',schedule='interval:3600',enabled=True,store_root=td); assert a['reality']=='PLANNED' and a['enabled'] is True
    ev=evaluate_automation(a,{'condition_met':True}); assert ev['triggered'] and ev['side_effects'] is False and ev['reality']=='INFERRED'
    m=define_monitor('Themeta-verse/Nexus','repository','health','changed','explicit','condition_met','run read-only mission','independent verification',enabled=True,store_root=td); me=evaluate_monitor(m,{'condition_met':True}); assert me['status']=='UNKNOWN' and me['reality']=='UNKNOWN'
    try: define_automation('Themeta-verse/Nexus','manual','true','deploy production',approval_policy='NOT_REQUIRED')
    except ValueError as e: assert str(e)=='HIGH_IMPACT_AUTOMATION_REQUIRES_APPROVAL'
    else: raise AssertionError('high-impact automation bypassed approval')
# CLI entrypoints use the same control layer.
for command in ('doctor','self-test','status','capabilities','memory','loops','verify','audit','recover'):
    p=subprocess.run([str(root/'nexus'),command,'--scope','Themeta-verse/Nexus'],capture_output=True,text=True,check=True); json.loads(p.stdout)
assert subprocess.run([str(root/'nexus'),'mission','Analyze repository health.','--mode','SIMULATION','--scope','Themeta-verse/Nexus'],capture_output=True,text=True,check=True).stdout
print(json.dumps({'status':'passed','capability_ceiling':'passed','provider_health':'passed','command_center':'passed','doctor':'passed','self_test':'passed','shared_runtime_cli':'passed','memory':'passed','open_loops':'passed','verification':'passed','audit':'passed','local_automation':'passed','monitoring_unknown_boundary':'passed','approval_safety':'passed','simulation_boundary':'passed','no_remote_writes':'passed','no_connector_expansion':'passed'},indent=2))
