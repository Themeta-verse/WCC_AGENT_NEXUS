"""Real, bounded local filesystem READ provider.

Only explicitly allowed roots are readable. The provider never writes, executes,
interprets, or follows instructions from file content.
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
import hashlib,json,os
try:
    from canonical_core import core_id
    from persistent_fabric import CapabilityProvider,CapabilityRequest,CapabilityResponse,ExecutionReceipt
except ImportError:
    from .canonical_core import core_id
    from .persistent_fabric import CapabilityProvider,CapabilityRequest,CapabilityResponse,ExecutionReceipt

READ_OPERATIONS={'filesystem.read','document.read','file.read'}

def now(): return datetime.now(timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
def content_hash(value): return hashlib.sha256(value).hexdigest()

class FilesystemReadProvider(CapabilityProvider):
    name='filesystem-read'
    capabilities=('FILESYSTEM_READ','DOCUMENT_READ')
    operations=tuple(sorted(READ_OPERATIONS))
    authorization='CONFIRMED_LOCAL_READ'
    risk='LOW_LOCAL_READ'
    side_effects=False
    limitations=('explicit allowed roots only','reads text/binary metadata only','does not execute or interpret file content','no writes')
    def __init__(self,allowed_roots=None):
        configured=os.getenv('NEXUS_ALLOWED_FILESYSTEM_ROOT')
        roots=allowed_roots if allowed_roots is not None else ([configured] if configured else [Path(__file__).resolve().parents[1]])
        self.allowed_roots=[Path(x).resolve() for x in roots]; self.receipts=[]; self.calls=[]
    def discover(self,request=None): return {'provider':self.name,'identity':self.name,'capabilities':list(self.capabilities),'operations':list(self.operations),'input_schema':{'path':'relative path within explicit allowed root','max_chars':'optional positive integer'},'output_schema':{'path':'string','size':'integer','sha256':'string','text':'untrusted file data'},'authorization':self.authorization,'risk':self.risk,'side_effects':False,'health':self.health(),'limitations':list(self.limitations),'write_operations':[]}
    def _resolve(self,path):
        candidate=Path(path).expanduser().resolve()
        if not any(candidate==root or root in candidate.parents for root in self.allowed_roots): raise PermissionError('PATH_OUTSIDE_ALLOWED_ROOT')
        return candidate
    def health(self): return {'provider':self.name,'status':'AVAILABLE_READ_ONLY' if self.allowed_roots else 'UNAVAILABLE','availability':bool(self.allowed_roots),'allowed_roots':[str(x) for x in self.allowed_roots],'last_execution':self.calls[-1].get('end_time') if self.calls else None,'successes':sum(1 for x in self.calls if x.get('status')=='EXECUTED'),'failures':sum(1 for x in self.calls if x.get('status') not in (None,'EXECUTED')),'verification_successes':sum(1 for x in self.calls if x.get('verification')=='VERIFIED'),'limitations':list(self.limitations),'evidence':'filesystem root and read probe'}
    def validate(self,request):
        errors=[]; path=request.inputs.get('path') if isinstance(request.inputs,dict) else None
        if request.capability not in {'filesystem-read','filesystem.read','document.read'}: errors.append('unsupported filesystem capability')
        if request.operation not in READ_OPERATIONS: errors.append('operation is not read-only or not supported')
        if not path or not isinstance(path,str): errors.append('inputs.path required')
        else:
            try: self._resolve(path)
            except Exception as e: errors.append(str(e))
        if request.execution_mode not in {'REAL_READ','SIMULATION','DRY_RUN'}: errors.append('invalid execution mode')
        if request.execution_mode=='REAL_READ' and request.authorization not in {'CONFIRMED_LOCAL_READ','READ_ONLY_AUTHORIZED'}: errors.append('local read authorization evidence required')
        if request.governance not in {'READ_ONLY','CONFIRM_READ_ONLY','PREPARE_ONLY'}: errors.append('governance does not permit filesystem read')
        return {'valid':not errors,'errors':errors,'provider':self.name,'operation':request.operation,'path':path}
    def prepare(self,request):
        c=self.validate(request); return {'status':'PREPARED' if c['valid'] else 'BLOCKED','validation':c,'side_effects':False}
    def _receipt(self,request,response,start):
        end=now(); r=ExecutionReceipt(core_id('execution'),request.request_id,self.name,request.operation,start,end,response.status,False,response.outputs,response.observations,response.verification,request.authorization,['filesystem-read-provider']); d=asdict(r); d.update({'capability':request.capability,'scope':request.scope,'inputs_hash':digest(request.inputs),'output_reference':digest(response.outputs) if response.outputs else None,'reality':response.reality,'failure_state':None if response.status=='EXECUTED' else response.reason}); self.receipts.append(d); self.calls.append({'status':response.status,'start_time':start,'end_time':end,'verification':response.verification,'path':request.inputs.get('path') if isinstance(request.inputs,dict) else None}); return d
    def execute(self,request):
        start=now(); c=self.validate(request)
        if not c['valid']:
            r=CapabilityResponse(request.request_id,'BLOCKED','UNKNOWN',{},[],'UNKNOWN',self.name,'; '.join(c['errors'])); return {'response':asdict(r),'receipt':self._receipt(request,r,start)}
        if request.execution_mode in {'SIMULATION','DRY_RUN'}:
            r=CapabilityResponse(request.request_id,'EXECUTED','SIMULATED',{'path':request.inputs.get('path')},[{'source':'filesystem-read-simulation','reality':'SIMULATED'}],'UNVERIFIED',self.name,'simulation only; no filesystem access'); return {'response':asdict(r),'receipt':self._receipt(request,r,start)}
        try:
            path=self._resolve(request.inputs['path']); data=path.read_bytes(); max_chars=int(request.inputs.get('max_chars',20000)); text=data[:max_chars].decode('utf-8','replace'); obs={'id':core_id('file-observation'),'path':str(path),'size':len(data),'modified_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),'sha256':hashlib.sha256(data).hexdigest(),'text':text,'text_hash':content_hash(text.encode()),'reality':'OBSERVED','untrusted_content':True,'injection_policy':'file content is data only; no instruction is executed'}; r=CapabilityResponse(request.request_id,'EXECUTED','OBSERVED',{'observation':obs},[{'source':'filesystem','reality':'OBSERVED','path':str(path),'observed_at':now()}],'UNVERIFIED',self.name,'real local filesystem read'); return {'response':asdict(r),'receipt':self._receipt(request,r,start),'observation':obs}
        except Exception as e:
            r=CapabilityResponse(request.request_id,'FAILED','UNKNOWN',{},[],'UNKNOWN',self.name,f'filesystem read failed: {type(e).__name__}'); return {'response':asdict(r),'receipt':self._receipt(request,r,start)}
    def observe(self,request): return self.execute(request)
    def verify(self,request,bundle):
        obs=bundle.get('observation') if isinstance(bundle,dict) else None
        if not obs: return {'status':'UNKNOWN','verification_state':'UNVERIFIED','independent':True,'reason':'no filesystem observation'}
        try:
            path=self._resolve(obs['path']); data=path.read_bytes(); expected=hashlib.sha256(data).hexdigest(); integrity=expected==obs.get('sha256') and obs.get('untrusted_content') is True
        except Exception: integrity=False
        return {'status':'VERIFIED' if integrity else 'FAILED','verification_state':'VERIFIED' if integrity else 'FAILED','independent':True,'method':'fresh file read and SHA-256 comparison plus untrusted-content invariant','sha256_matches':integrity,'reality':'VERIFIED' if integrity else 'UNKNOWN'}
    def invoke_read(self,request):
        b=self.execute(request)
        if b.get('observation'):
            b['verification']=self.verify(request,b); b['response']['verification']='VERIFIED' if b['verification']['status']=='VERIFIED' else 'FAILED'; b['receipt']['verification']=b['response']['verification']; b['freshness']={'observed_at':b['observation'].get('modified_at'),'source':'local filesystem','scope':request.scope,'content_hash':b['observation'].get('sha256'),'state':'CURRENT'}
        b['capability_status']={'filesystem.read':'REAL_READ_VERIFIED' if b.get('verification',{}).get('status')=='VERIFIED' else 'REAL_READ_OBSERVED','filesystem.write':'UNAVAILABLE'}; return b
