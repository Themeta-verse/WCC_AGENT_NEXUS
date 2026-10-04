import json,tempfile
from pathlib import Path
from mission_composer import MissionComposer,capability_contracts,approval_contract
from persistent_fabric import LocalStateStore,CapabilityProvider
from github_provider import GitHubReadProvider
from local_control import mission_view,mission_verify

def main():
 c=MissionComposer(); plans=[c.compose(x,'Themeta-verse/Nexus','DRY_RUN') for x in ['Analyze the repository.','Now tell me what I should fix first.','Audit this project.','Research this company.']]
 assert [p['mission_type'] for p in plans]==['REPOSITORY_ANALYSIS','ENGINEERING_DIAGNOSIS','PROJECT_AUDIT','RESEARCH']; assert len({tuple(p['task_graph']['order']) for p in plans[:3]})==3
 assert isinstance(GitHubReadProvider(),CapabilityProvider); contracts=capability_contracts(); assert contracts['repository.read']['provider']=='github-read' and contracts['repository.write']['health']=='UNAVAILABLE'
 approval=approval_contract('Themeta-verse/Nexus','repository.write','main','hypothetical change'); assert approval['valid'] is False
 sim=c.execute(c.compose('Analyze the repository.','Themeta-verse/Nexus','SIMULATION'),mode='SIMULATION'); assert sim['mission']['state']=='PARTIAL' and sim['mission']['reality']=='SIMULATED'; assert not sim['mission'].get('verification',{}).get('completion_verification')
 blocked=c.execute(c.compose('Research this company.','Themeta-verse/Nexus','REAL_READ'),mode='REAL_READ'); assert blocked['mission']['state']=='BLOCKED'; assert blocked['external_invocations']==0
 fake={'mission':{'state':'COMPLETED','verification':{'completion_verification':{'status':'VERIFIED','evidence_ids':[]}}}}; assert c.recover(fake)['safe_to_retry'] is False; from mission_composer import validate_completion; assert validate_completion(fake)['allowed'] is False
 a=tempfile.mkdtemp(prefix='nexus-generalized-bench-'); store=LocalStateStore(a); store.save({'mission':{'mission_id':'mission-a','state':'COMPLETED'}},'Project/A'); assert mission_view('mission-a','Project/B',a)['status']=='UNKNOWN'
 out={'status':'passed','generic_mission_types':'passed','different_task_graphs':'passed','provider_contract':'passed','capability_contracts':'passed','approval_boundary':'passed','simulation_separation':'passed','unsupported_provider_blocked':'passed','false_completion_rejected':'passed','scope_isolation':'passed','remote_writes':False,'connector_expansion':False}; print(json.dumps(out,indent=2)); Path('/home/ubuntu/nexus/artifacts/nexus-generalized-agent-benchmark.json').write_text(json.dumps(out,indent=2))
if __name__=='__main__': main()
