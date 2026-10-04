import json,tempfile
from pathlib import Path
from mission_composer import MissionComposer,CapabilityResolver,capability_contracts,validate_completion,validate_receipt
from browser_provider import BrowserReadProvider
from filesystem_provider import FilesystemReadProvider
from persistent_fabric import CapabilityProvider,CapabilityRequest
from canonical_core import core_id
from living_loop import observation_delta

def main():
 browser=BrowserReadProvider(); filesystem=FilesystemReadProvider(['/home/ubuntu/nexus']); assert isinstance(browser,CapabilityProvider) and isinstance(filesystem,CapabilityProvider); assert browser.discover()['write_operations']==[] and filesystem.discover()['write_operations']==[]
 b_req=CapabilityRequest(core_id('request'),'browser-read','browser.read','benchmark',{'url':'https://github.com/Themeta-verse/Nexus','max_chars':1000},'CONFIRMED_BROWSER_READ','READ_ONLY','REAL_READ','browser observation','content integrity verification'); b=browser.invoke_read(b_req); assert b['response']['reality']=='OBSERVED' and b['receipt']['side_effects'] is False and b['verification']['status']=='VERIFIED'
 f_req=CapabilityRequest(core_id('request'),'filesystem-read','filesystem.read','benchmark',{'path':'/home/ubuntu/nexus/projects/nexus-v3/github-link.json'},'CONFIRMED_LOCAL_READ','READ_ONLY','REAL_READ','file observation','fresh digest verification'); f=filesystem.invoke_read(f_req); assert f['response']['reality']=='OBSERVED' and f['verification']['status']=='VERIFIED'
 bad=CapabilityRequest(core_id('request'),'filesystem-read','filesystem.read','benchmark',{'path':'/etc/passwd'},'CONFIRMED_LOCAL_READ','READ_ONLY','REAL_READ','file observation','fresh digest verification'); assert filesystem.execute(bad)['response']['status']=='BLOCKED'
 browser._page_read=lambda *args,**kwargs: (_ for _ in ()).throw(TimeoutError('induced')); failed=browser.execute(b_req); assert failed['response']['status']=='FAILED' and failed['receipt']['failure_state'].startswith('browser read failed')
 resolver=CapabilityResolver([{'provider':'provider-a','capabilities':['BROWSER_READ'],'operations':['READ','VERIFY'],'authorization':'AUTH','status':'AVAILABLE','quality':'REAL_OBSERVATION'},{'provider':'provider-b','capabilities':['BROWSER_READ'],'operations':['READ','VERIFY'],'authorization':'AUTH','status':'VERIFIED','quality':'REAL_OBSERVATION'}]); assert resolver.resolve([type('R',(),{'requirement_id':'r','capability':'BROWSER_READ','required_operations':['READ','VERIFY']})()],'REAL_READ')[0]['selected_provider']=='provider-b'
 c=MissionComposer(); p=c.compose('Read and summarize this web page https://github.com/Themeta-verse/Nexus','browser-scope','REAL_READ'); assert p['mission_type']=='BROWSER_RESEARCH' and p['capability_resolution'][0]['selected_provider']=='browser-read'; sim=c.execute(c.compose('Read and summarize this web page https://github.com/Themeta-verse/Nexus','browser-scope','SIMULATION'),mode='SIMULATION'); assert sim['mission']['state']=='PARTIAL' and sim['mission']['reality']=='SIMULATED' and not validate_completion(sim)['allowed']
 assert observation_delta(b['observation'],b['observation'])['status'] in {'UNCHANGED','STALE'}; replanned=c.replan(p,'browser observation changed',allow_simulation=False); assert replanned['replanned'] and replanned['pretend_success'] is False
 contracts=capability_contracts(); assert contracts['browser.read']['health']=='AVAILABLE_READ_ONLY' and contracts['filesystem.read']['health']=='AVAILABLE_READ_ONLY' and contracts['repository.write']['health']=='UNAVAILABLE'
 out={'status':'passed','provider_contracts':'passed','browser_real_read':'passed','filesystem_real_read':'passed','path_escape':'blocked','timeout_failure':'classified','provider_substitution':'passed','simulation_separation':'passed','change_detection':'passed','replanning':'passed','capability_ceiling':'passed','remote_writes':False,'connector_expansion':False}; Path('/home/ubuntu/nexus/artifacts/nexus-capability-expansion-benchmark.json').write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
