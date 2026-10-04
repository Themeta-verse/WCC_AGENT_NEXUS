#!/usr/bin/env python3
"""NEXUS Ω¹⁰ Mission Composer and Capability Composition Engine.

This module composes local mission state and bounded specialists around the
already-proven Ω⁹ GitHub READ provider. It never exposes GitHub writes and never
turns external text into authority, governance, approval, or capability grants.
"""
from __future__ import annotations
from dataclasses import dataclass,field,asdict
from datetime import datetime,timezone
from typing import Any
import hashlib,json,re
try:
    from canonical_core import core_id
    from persistent_fabric import CapabilityRequest,LocalStateStore
    from github_provider import GitHubReadProvider
    from browser_provider import BrowserReadProvider
    from filesystem_provider import FilesystemReadProvider
    from omega4_reality import reconcile,transition
    from personal_agent import compile_agent_request,adversarial_content_is_data
    from convergence_engine import prompt_injection_defense,secret_scan
    from action_ready import normalize_observation,reconcile_sources,decision_engine,action_packet,reality_audit,evidence_gate
    from outcome_intelligence import continuity_projection
except ImportError:
    from .canonical_core import core_id
    from .persistent_fabric import CapabilityRequest,LocalStateStore
    from .github_provider import GitHubReadProvider
    from .browser_provider import BrowserReadProvider
    from .filesystem_provider import FilesystemReadProvider
    from .omega4_reality import reconcile,transition
    from .personal_agent import compile_agent_request,adversarial_content_is_data
    from .convergence_engine import prompt_injection_defense,secret_scan
    from .action_ready import normalize_observation,reconcile_sources,decision_engine,action_packet,reality_audit,evidence_gate
    from .outcome_intelligence import continuity_projection

MISSION_STATES={'DRAFT','UNDERSTANDING','PLANNING','READY','PREPARING','WAITING_FOR_APPROVAL','EXECUTING','OBSERVING','VERIFYING','REPLANNING','BLOCKED','PARTIAL','COMPLETED','FAILED','CANCELLED','UNKNOWN'}
TASK_STATES={'PLANNED','READY','EXECUTING','OBSERVED','INFERRED','VERIFIED','SIMULATED','BLOCKED','FAILED','COMPLETED','UNKNOWN'}
RELATION_TYPES={'SEQUENTIAL','PARALLEL','CONDITIONAL','OPTIONAL','BLOCKED'}
SUPPORTED_MODES={'PLAN_ONLY','DRY_RUN','SIMULATION','REAL_READ','REAL_WRITE','REAL_IRREVERSIBLE'}
REAL_MODES={'REAL_READ'}
WRITE_MODES={'REAL_WRITE','REAL_IRREVERSIBLE'}
MISSION_TYPES={'RESEARCH','REPOSITORY_ANALYSIS','PROJECT_AUDIT','DOCUMENT_ANALYSIS','DECISION_SUPPORT','CREATIVE_RESEARCH','ENGINEERING_DIAGNOSIS','PROJECT_REVIEW','KNOWLEDGE_SYNTHESIS','BROWSER_RESEARCH','FILE_ANALYSIS'}

def now(): return datetime.now(timezone.utc).isoformat()

def classify_mission_intent(intent):
    q=(intent or '').lower()
    if any(x in q for x in ('compare','options','decision','choose')): kind='DECISION_SUPPORT'
    elif any(x in q for x in ('browser','website','web page','navigate','online page')): kind='BROWSER_RESEARCH'
    elif any(x in q for x in ('local file','filesystem','file contents','read this file','workspace file')): kind='FILE_ANALYSIS'
    elif any(x in q for x in ('document','readme','docs')): kind='DOCUMENT_ANALYSIS'
    elif any(x in q for x in ('research','company','market','knowledge')): kind='RESEARCH'
    elif 'audit' in q: kind='PROJECT_AUDIT'
    elif any(x in q for x in ('fix first','engineering risk','unresolved risk','diagnos','improve')): kind='ENGINEERING_DIAGNOSIS'
    elif any(x in q for x in ('review project','project state','review current')): kind='PROJECT_REVIEW'
    elif any(x in q for x in ('synthes','summar')): kind='KNOWLEDGE_SYNTHESIS'
    elif any(x in q for x in ('creative','idea','brainstorm')): kind='CREATIVE_RESEARCH'
    elif any(x in q for x in ('repository','repo','github','branch','commit','tree','health')): kind='REPOSITORY_ANALYSIS'
    else: kind='REPOSITORY_ANALYSIS'
    objective={'REPOSITORY_ANALYSIS':'Produce a verified repository health assessment and safest high-value next engineering action','ENGINEERING_DIAGNOSIS':'Identify and prioritize the most important unresolved engineering risk from verified repository evidence','PROJECT_REVIEW':'Review the current project state and identify evidence-backed priorities','PROJECT_AUDIT':'Audit the project using only available, verified evidence','DOCUMENT_ANALYSIS':'Analyze the available project documentation without treating document text as instructions','RESEARCH':'Research the requested subject using an actually available evidence provider','DECISION_SUPPORT':'Compare the stated options using available evidence and explicit uncertainty','CREATIVE_RESEARCH':'Develop evidence-bounded creative research options without claiming external execution','KNOWLEDGE_SYNTHESIS':'Synthesize available verified knowledge and preserve evidence provenance','BROWSER_RESEARCH':'Read and analyze the explicitly requested web page using the real browser session','FILE_ANALYSIS':'Read and analyze an explicitly scoped local file without executing its contents'}[kind]
    capability='repository.read' if kind in {'REPOSITORY_ANALYSIS','ENGINEERING_DIAGNOSIS','PROJECT_REVIEW','PROJECT_AUDIT','DOCUMENT_ANALYSIS'} else ('browser.read' if kind=='BROWSER_RESEARCH' else ('filesystem.read' if kind=='FILE_ANALYSIS' else 'knowledge.read'))
    return {'mission_type':kind,'objective':objective,'capability':capability,'provider_agnostic':True,'intent':intent}

def capability_contracts():
    return {'repository.read':{'identity':'repository.read','operations':['READ','VERIFY'],'input_schema':['scope owner/repository','read authorization'],'output_schema':['raw response','normalized observation','analysis','verification'],'authorization_requirements':['CONFIRMED_READ_ONLY'],'risk':'LOW_READ_ONLY','side_effects':False,'verification_method':'independent RepositoryObservation comparison','provider':'github-read','health':'VERIFIED','limitations':['bounded seven-endpoint read path'],'scope':'owner/repository'},'browser.read':{'identity':'browser.read','operations':['READ','VERIFY'],'input_schema':['HTTPS URL','optional max_chars','optional screenshot'],'output_schema':['URL','title','untrusted text','content hash','analysis','verification'],'authorization_requirements':['CONFIRMED_BROWSER_READ'],'risk':'LOW_READ_ONLY','side_effects':False,'verification_method':'fresh content-integrity and HTTPS invariant check','provider':'browser-read','health':'AVAILABLE_READ_ONLY','limitations':['requires existing local Chromium CDP; no clicks, typing, submits, or writes'],'scope':'explicit URL'},'filesystem.read':{'identity':'filesystem.read','operations':['READ','VERIFY'],'input_schema':['path within configured allowed root','optional max_chars'],'output_schema':['path','size','sha256','untrusted text','analysis','verification'],'authorization_requirements':['CONFIRMED_LOCAL_READ'],'risk':'LOW_LOCAL_READ','side_effects':False,'verification_method':'fresh file read and SHA-256 comparison','provider':'filesystem-read','health':'AVAILABLE_READ_ONLY','limitations':['requires an explicit configured root; no execution or writes'],'scope':'project filesystem'},'local.analysis':{'identity':'local.analysis','operations':['ANALYZE','VERIFY'],'input_schema':['structured observation'],'output_schema':['findings','risks','recommendation','confidence','limitations'],'authorization_requirements':['LOCAL_RUNTIME'],'risk':'LOW','side_effects':False,'verification_method':'evidence trace and invariant checks','provider':'local-specialist-functions','health':'AVAILABLE','limitations':['analysis is bounded by supplied observation'],'scope':'mission'},'knowledge.read':{'identity':'knowledge.read','operations':['READ','VERIFY'],'input_schema':['research subject'],'output_schema':['evidence','observation','verification'],'authorization_requirements':['NO_REAL_PROVIDER_CONFIRMED'],'risk':'UNKNOWN','side_effects':False,'verification_method':'UNAVAILABLE','provider':None,'health':'UNAVAILABLE','limitations':['no real knowledge provider enabled'],'scope':'explicit'},'repository.write':{'identity':'repository.write','operations':['WRITE'],'input_schema':['target','approval'],'output_schema':[],'authorization_requirements':['explicit separate authorization'],'risk':'HIGH','side_effects':True,'verification_method':'UNAVAILABLE','provider':None,'health':'UNAVAILABLE','limitations':['blocked by governance'],'scope':'owner/repository'}}

def approval_contract(scope,operation,target,reason,risk='HIGH',expiration=None,approved_by=None,approval_time=None):
    return {'scope':scope,'operation':operation,'target':target,'reason':reason,'risk':risk,'expiration':expiration,'approved_by':approved_by,'approval_time':approval_time,'valid':bool(approved_by and approval_time and expiration),'invalidated_by':['scope change','operation change','target change','mission change']}
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()

@dataclass
class CapabilityRequirement:
    requirement_id:str
    capability:str
    purpose:str
    required_operations:list[str]
    scope:str
    required_reality:str
    verification_required:bool=True
    optional:bool=False

@dataclass
class ProviderResolution:
    requirement_id:str
    capability:str
    selected_provider:str|None
    status:str
    authorization:str
    supported_operations:list[str]
    evidence_quality:str
    reason:str
    fallback_provider:str|None=None

@dataclass
class MissionTask:
    task_id:str
    title:str
    kind:str
    depends_on:list[str]=field(default_factory=list)
    relation:str='SEQUENTIAL'
    capability_requirement:str|None=None
    specialist:str|None=None
    state:str='PLANNED'
    reality:str='PLANNED'
    side_effect_risk:str='NONE'
    retryable:bool=True
    evidence_ids:list[str]=field(default_factory=list)
    output:dict=field(default_factory=dict)
    failure:str|None=None

@dataclass
class SpecialistContract:
    specialist_id:str
    role:str
    objective:str
    scope:str
    allowed_capabilities:list[str]
    input_task_ids:list[str]
    output_schema:list[str]
    verification_method:str
    state:str='PLANNED'

@dataclass
class CompletionCriteria:
    criteria_id:str
    statements:list[str]
    required_task_ids:list[str]
    required_reality:str
    required_verification:str='VERIFIED'

@dataclass
class CompletionEvidence:
    evidence_id:str
    criteria_id:str
    source_ids:list[str]
    satisfied:bool
    reality:str
    explanation:str

@dataclass
class CompletionVerification:
    verification_id:str
    criteria_id:str
    status:str
    independent:bool
    evidence_ids:list[str]
    authority:str
    reason:str

@dataclass
class DecisionRecord:
    decision_id:str
    decision:str
    alternatives:list[str]
    criteria:list[str]
    evidence:list[str]
    assumptions:list[str]
    reason:str
    confidence:str
    reversibility:str
    future_validation:str

@dataclass
class Mission:
    mission_id:str
    user_intent:str
    objective:str
    success_criteria:list[str]
    constraints:list[str]
    scope:str
    priority:str
    deadline:str|None
    state:str
    current_phase:str
    workflow_ids:list[str]
    task_ids:list[str]
    capability_requirements:list[str]
    specialist_requirements:list[str]
    dependencies:list[str]
    risks:list[str]
    approvals:list[dict]
    observations:list[dict]
    decisions:list[dict]
    verification:dict
    open_loops:list[dict]
    next_action:dict
    completion_state:str
    created_at:str
    updated_at:str
    provenance:list[str]
    reality:str='PLANNED'
    mission_type:str='REPOSITORY_ANALYSIS'
    context_memory_ids:list[str]=field(default_factory=list)
    context_reused:bool=False



def _task_graph(tasks:list[MissionTask]):
    ids={t.task_id for t in tasks}; missing=[]; children={t.task_id:[] for t in tasks}; indegree={t.task_id:0 for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep not in ids: missing.append({'task':t.task_id,'dependency':dep})
            else: indegree[t.task_id]+=1; children[dep].append(t.task_id)
    order=[]; ready=[x for x,v in indegree.items() if v==0]
    while ready:
        n=ready.pop(0); order.append(n)
        for child in children[n]:
            indegree[child]-=1
            if indegree[child]==0: ready.append(child)
    cycle=len(order)!=len(tasks)
    blockers=missing+([{'type':'CYCLE'}] if cycle else [])
    levels={x:0 for x in order}
    for x in order:
        t=next(t for t in tasks if t.task_id==x)
        levels[x]=max([levels[d]+1 for d in t.depends_on if d in levels] or [0])
    groups=[]
    for level in sorted(set(levels.values())):
        groups.append([x for x in order if levels[x]==level])
    return {'order':order,'critical_path':order,'parallel_groups':groups,'levels':levels,'blockers':blockers,'status':'BLOCKED' if blockers else 'VALID','relations':[{ 'task_id':t.task_id,'relation':t.relation,'depends_on':t.depends_on} for t in tasks]}

class CapabilityResolver:
    """Resolve abstract requirements using evidence, not provider assumptions."""
    def __init__(self,provider_inventory=None):
        browser_health=BrowserReadProvider().health(); filesystem_health=FilesystemReadProvider().health(); self.provider_inventory=provider_inventory or [
            {'provider':'github-read','capabilities':['REPOSITORY_READ','REPOSITORY_METADATA_READ'],'operations':['READ','VERIFY'],'authorization':'CONFIRMED_READ_ONLY','status':'VERIFIED','quality':'REAL_OBSERVATION'},
            {'provider':'browser-read','capabilities':['BROWSER_READ','DOCUMENT_READ','RESEARCH'],'operations':['READ','VERIFY'],'authorization':'CONFIRMED_BROWSER_READ','status':'VERIFIED' if browser_health['availability'] else 'UNAVAILABLE','quality':'REAL_OBSERVATION' if browser_health['availability'] else 'UNKNOWN'},
            {'provider':'filesystem-read','capabilities':['FILESYSTEM_READ','DOCUMENT_READ'],'operations':['READ','VERIFY'],'authorization':'CONFIRMED_LOCAL_READ','status':'VERIFIED' if filesystem_health['availability'] else 'UNAVAILABLE','quality':'REAL_OBSERVATION' if filesystem_health['availability'] else 'UNKNOWN'},
            {'provider':'simulation','capabilities':['REPOSITORY_READ','RESEARCH','IMAGE_CREATION','VIDEO_CREATION','COMMUNICATION','SCHEDULING','ANALYTICS','BROWSER_READ','FILESYSTEM_READ','DOCUMENT_READ'],'operations':['READ','ANALYZE'],'authorization':'LOCAL_SIMULATION_ONLY','status':'SIMULATED','quality':'SIMULATED'},
        ]
    def resolve(self,requirements:list[CapabilityRequirement],mode='REAL_READ'):
        out=[]
        for req in requirements:
            candidates=[p for p in self.provider_inventory if req.capability in p['capabilities'] and all(x in p['operations'] for x in req.required_operations) and (mode in {'SIMULATION','DRY_RUN'} or p.get('status') not in {'UNAVAILABLE','BROKEN','UNAUTHORIZED','UNKNOWN'})]
            if mode in {'SIMULATION','DRY_RUN'}:
                candidates=sorted(candidates,key=lambda p: p['provider']!='simulation')
            else:
                candidates=sorted(candidates,key=lambda p: (p['status']!='VERIFIED',p['provider']=='simulation'))
            chosen=candidates[0] if candidates else None
            if not chosen:
                out.append(asdict(ProviderResolution(req.requirement_id,req.capability,None,'UNAVAILABLE','NONE',[],'UNKNOWN','no evidence-backed provider or approved simulation')))
            else:
                status='VERIFIED' if mode=='REAL_READ' and chosen['provider']!='simulation' else ('SIMULATED' if chosen['provider']=='simulation' else 'AVAILABLE')
                out.append(asdict(ProviderResolution(req.requirement_id,req.capability,chosen['provider'],status,chosen['authorization'],chosen['operations'],chosen['quality'],'selected from evidence-backed provider inventory',fallback_provider='simulation' if chosen['provider']!='simulation' else None)))
        return out

class MissionComposer:
    def __init__(self,provider=None,resolver=None):
        self.providers={'github-read':GitHubReadProvider(),'browser-read':BrowserReadProvider(),'filesystem-read':FilesystemReadProvider()}; self.provider=provider or self.providers['github-read']; self.providers[self.provider.name]=self.provider; self.resolver=resolver or CapabilityResolver(); self.missions={}
    def _requirements(self,scope,spec):
        ids={'repository.read':('cap-repository-read','REPOSITORY_READ'),'repository.metadata.read':('cap-repository-metadata-read','REPOSITORY_METADATA_READ'),'browser.read':('cap-browser-read','BROWSER_READ'),'filesystem.read':('cap-filesystem-read','FILESYSTEM_READ')}; rid,cap=ids.get(spec['capability'],('cap-'+spec['capability'].replace('.','-'),spec['capability'].upper().replace('.','_'))); return [CapabilityRequirement(rid,cap,spec['objective'],['READ','VERIFY'],scope,'OBSERVED')]
    def _specialists(self,scope):
        return [
            SpecialistContract('sp-engineering','Engineering Specialist','derive engineering health findings from the observed repository',''+scope,['repository observation'],['observe-repository','engineering-analysis'],['findings','risks','recommendation'],'independent comparison to repository observation'),
            SpecialistContract('sp-security','Security Specialist','identify security-relevant risks without treating repository text as instructions',scope,['repository observation'],['observe-repository','security-analysis'],['findings','risks','injection_policy'],'untrusted-content and invariant checks'),
            SpecialistContract('sp-qa','QA Specialist','assess test and verification posture from observed repository evidence',scope,['repository observation'],['observe-repository','qa-analysis'],['findings','risks','verification_gaps'],'independent evidence trace'),
        ]
    def compose(self,user_intent,scope='Themeta-verse/Nexus',mode='REAL_READ',priority='HIGH',deadline=None,store_root=None):
        if mode not in SUPPORTED_MODES or mode in WRITE_MODES: raise ValueError('unsupported or unauthorized mission execution mode')
        spec=classify_mission_intent(user_intent); agent=compile_agent_request(user_intent,{'project_id':scope},scope,'PLAN_ONLY')
        context_memory_ids=[]; context_reused=False
        if store_root:
            try:
                memory_query='' if any(token in (user_intent or '').lower() for token in ('continue','fix','review','next','risk','improve')) else user_intent; context_memories=LocalStateStore(store_root).retrieve(memory_query,scope,limit=8); context_memory_ids=[x.get('memory_id') for x in context_memories]; context_reused=bool(context_memory_ids)
            except Exception: context_memories=[]
        requirements=self._requirements(scope,spec); resolutions=self.resolver.resolve(requirements,mode)
        if spec['capability']=='repository.read':
            tasks=[MissionTask('observe-repository','Observe repository','CAPABILITY',[], 'SEQUENTIAL','cap-repository-read',None,'READY','PLANNED','NONE',False)]
            if spec['mission_type']=='ENGINEERING_DIAGNOSIS':
                tasks += [MissionTask('risk-analysis','Engineering risk analysis','SPECIALIST',['observe-repository'],'PARALLEL',None,'sp-engineering','PLANNED','INFERRED'),MissionTask('security-analysis','Security analysis','SPECIALIST',['observe-repository'],'PARALLEL',None,'sp-security','PLANNED','INFERRED'),MissionTask('qa-analysis','QA analysis','SPECIALIST',['observe-repository'],'PARALLEL',None,'sp-qa','PLANNED','INFERRED'),MissionTask('risk-prioritization','Prioritize unresolved risk','DECISION',['risk-analysis','security-analysis','qa-analysis'],'SEQUENTIAL',None,None,'PLANNED','INFERRED'),MissionTask('mission-verification','Verify mission criteria','VERIFICATION',['risk-prioritization'],'SEQUENTIAL',None,None,'PLANNED','UNKNOWN')]
            elif spec['mission_type']=='PROJECT_AUDIT':
                tasks += [MissionTask('engineering-analysis','Engineering audit','SPECIALIST',['observe-repository'],'SEQUENTIAL',None,'sp-engineering','PLANNED','INFERRED'),MissionTask('security-analysis','Security audit','SPECIALIST',['engineering-analysis'],'SEQUENTIAL',None,'sp-security','PLANNED','INFERRED'),MissionTask('documentation-audit','Documentation audit','SPECIALIST',['security-analysis'],'SEQUENTIAL',None,'sp-qa','PLANNED','INFERRED'),MissionTask('audit-recommendation','Compose audit recommendation','DECISION',['documentation-audit'],'SEQUENTIAL',None,None,'PLANNED','INFERRED'),MissionTask('mission-verification','Verify mission criteria','VERIFICATION',['audit-recommendation'],'SEQUENTIAL',None,None,'PLANNED','UNKNOWN')]
            else:
                tasks += [MissionTask('engineering-analysis','Engineering analysis','SPECIALIST',['observe-repository'],'PARALLEL',None,'sp-engineering','PLANNED','INFERRED'),MissionTask('security-analysis','Security analysis','SPECIALIST',['observe-repository'],'PARALLEL',None,'sp-security','PLANNED','INFERRED'),MissionTask('qa-analysis','QA analysis','SPECIALIST',['observe-repository'],'PARALLEL',None,'sp-qa','PLANNED','INFERRED'),MissionTask('recommendation','Compose recommendation','DECISION',['engineering-analysis','security-analysis','qa-analysis'],'SEQUENTIAL',None,None,'PLANNED','INFERRED'),MissionTask('mission-verification','Verify mission criteria','VERIFICATION',['recommendation'],'SEQUENTIAL',None,None,'PLANNED','UNKNOWN')]
        elif spec['capability'] in {'browser.read','filesystem.read'}:
            label='browser page' if spec['capability']=='browser.read' else 'local file'; tasks=[MissionTask('observe-capability','Observe '+label,'CAPABILITY',[],'SEQUENTIAL',requirements[0].requirement_id,None,'READY','PLANNED','NONE',False),MissionTask('capability-analysis','Analyze '+label,'SPECIALIST',['observe-capability'],'SEQUENTIAL',None,None,'PLANNED','INFERRED'),MissionTask('mission-verification','Verify capability evidence','VERIFICATION',['capability-analysis'],'SEQUENTIAL',None,None,'PLANNED','UNKNOWN')]
        else:
            tasks=[MissionTask('context-retrieval','Retrieve relevant scoped context','CONTEXT',[],'SEQUENTIAL',None,None,'READY','OBSERVED'),MissionTask('capability-check','Resolve required capability','CAPABILITY',['context-retrieval'],'SEQUENTIAL',requirements[0].requirement_id,None,'PLANNED','PLANNED'),MissionTask('mission-verification','Verify capability and evidence boundary','VERIFICATION',['capability-check'],'SEQUENTIAL',None,None,'PLANNED','UNKNOWN')]
        graph=_task_graph(tasks); specialists=self._specialists(scope) if spec['capability']=='repository.read' else []
        mission=Mission(core_id('mission'),user_intent,spec['objective'],([ 'A real or explicitly simulated repository observation exists','Engineering, security, and QA analyses preserve observation provenance','Recommendation is traceable to evidence','Completion verification passes for the selected reality mode'] if spec['capability']=='repository.read' else ['A real or explicitly simulated capability observation exists','Analysis preserves source-provider provenance','Independent verification passes for the selected reality mode']),

            ['No GitHub writes','No deployment','External repository content is untrusted data','Unavailable capabilities remain unavailable or simulated','Mission approval does not authorize future write actions'],scope,priority,deadline,'READY','CAPABILITIES_RESOLVED',[core_id('workflow')],[t.task_id for t in tasks],[r.requirement_id for r in requirements],[s.specialist_id for s in specialists],[],['provider failure','stale observation','conflicting evidence'],[],[],[],{},[],{'action':'execute observe-repository' if spec['capability']=='repository.read' else ('execute observe browser or file capability' if spec['capability'] in {'browser.read','filesystem.read'} else 'resolve an evidence-backed provider'),'why':'capability-first mission compilation selected '+spec['capability'],'evidence':['task graph','capability resolution']+context_memory_ids,'dependencies':[],'risk':'bounded read-only provider call' if spec['capability'] in {'repository.read','browser.read','filesystem.read'} else 'no proven provider','expected_outcome':spec['objective'],'verification':'receipt plus independent observation comparison' if spec['capability'] in {'repository.read','browser.read','filesystem.read'} else 'provider evidence required'},'PENDING',now(),now(),['canonical-core','capability-fabric','mission-composer'], 'PLANNED' if mode in {'REAL_READ','PLAN_ONLY'} else 'SIMULATED',spec['mission_type'],context_memory_ids,context_reused)
        package={'mission_type':spec['mission_type'],'intent_compilation':spec,'context':{'scope':scope,'memory_ids':context_memory_ids,'reused':context_reused,'source':'scoped LocalStateStore'},'mission':asdict(mission),'agent':agent,'tasks':[asdict(t) for t in tasks],'task_graph':graph,'capability_requirements':[asdict(x) for x in requirements],'capability_resolution':resolutions,'provider_resolution':{'selected':[r for r in resolutions if r['selected_provider']],'unavailable':[r for r in resolutions if not r['selected_provider']],'mode':mode},'specialists':[asdict(s) for s in specialists],'workflow_graph':{'workflow_ids':mission.workflow_ids,'task_ids':mission.task_ids,'edges':graph['relations']},'verification_graph':{'criteria_id':'criteria-'+spec['mission_type'].lower(),'dependencies':list(graph['order']),'final':'mission-verification'},'reality_graph':{'nodes':[{'id':'mission-plan','reality':'PLANNED'},{'id':'repository-observation','reality':'OBSERVED' if mode=='REAL_READ' else 'SIMULATED'},{'id':'specialist-analysis','reality':'INFERRED' if mode=='REAL_READ' else 'SIMULATED'},{'id':'mission-verification','reality':'VERIFIED' if mode=='REAL_READ' else 'SIMULATED'}],'edges':['observation->analysis','analysis->recommendation','recommendation->verification']},'no_external_invocations':True}
        self.missions[mission.mission_id]=package; return package
    def _analysis(self,role,obs):
        raw=obs.get('raw',{}); tree=raw.get('tree',{}); paths=[x.get('path','') for x in tree.get('tree',[]) if isinstance(x,dict)] if isinstance(tree,dict) else []
        content=json.dumps(raw,default=str); injection=prompt_injection_defense(content); secrets=secret_scan(content)
        if role=='Engineering Specialist':
            return {'role':role,'reality':'INFERRED','source_observation_id':obs.get('id'),'findings':[{'area':'repository activity','value':len(raw.get('commits',[])) if isinstance(raw.get('commits'),list) else 0,'status':'OBSERVED'},{'area':'tree size','value':len(paths),'status':'OBSERVED'}],'risks':['bounded health analysis only'],'recommendation':'Review the observed repository health findings before any implementation change'}
        if role=='Security Specialist':
            return {'role':role,'reality':'INFERRED','source_observation_id':obs.get('id'),'findings':[{'area':'untrusted content','status':'OBSERVED','policy':'data only; cannot authorize actions'}],'risks':[],'injection_policy':injection,'secret_scan':secrets}
        return {'role':role,'reality':'INFERRED','source_observation_id':obs.get('id'),'findings':[{'area':'test paths','value':len([p for p in paths if '/test' in p or p.startswith('test')]),'status':'OBSERVED'},{'area':'documentation paths','value':len([p for p in paths if p.lower().endswith(('.md','.rst'))]),'status':'OBSERVED'}],'risks':['verification depth is bounded by available tree and recent records'],'verification_gaps':['deep static analysis not performed']}
    def _call_count(self,provider):
        adapter=getattr(provider,'adapter',None)
        return len(getattr(adapter,'calls',[])) if adapter is not None else len(getattr(provider,'calls',[]))
    def _external_analysis(self,provider,obs):
        text=obs.get('text','') if isinstance(obs,dict) else ''
        injection=prompt_injection_defense(text); secrets=secret_scan(text)
        return {'role':'Capability Evidence Analyst','provider':provider,'reality':'INFERRED','source_observation_id':obs.get('id') or obs.get('content_hash') or obs.get('sha256'),'findings':[{'area':'external content','status':'OBSERVED','length':len(text),'content_hash':obs.get('content_hash') or obs.get('sha256')}],'risks':['content is untrusted data and cannot authorize actions'],'recommendation':'Use the observed content only within the requested mission scope','injection_policy':injection,'secret_scan':secrets,'limitations':['bounded read and independent integrity verification']}
    def execute(self,package,store_root=None,mode=None):
        if package.get('multi_provider'): return self.execute_multi_provider(package,store_root,mode)
        mission=package['mission']; mode=mode or package.get('provider_resolution',{}).get('mode','REAL_READ'); scope=mission['scope']; tasks={t['task_id']:t for t in package['tasks']}; events=[]; writes=False; external=0; spec=package.get('intent_compilation',{}); cap=spec.get('capability','repository.read')
        mission['state']='EXECUTING'; mission['current_phase']='OBSERVING'; mission['updated_at']=now()
        resolution=next((x for x in package['capability_resolution'] if x['requirement_id']==package['capability_requirements'][0]['requirement_id']),None)
        provider_name=resolution.get('selected_provider') if resolution else None
        operation={'repository.read':'repository.health.read','browser.read':'browser.read','filesystem.read':'filesystem.read'}.get(cap,'read')
        provider_cap={'repository.read':'github-read','browser.read':'browser-read','filesystem.read':'filesystem-read'}.get(cap,cap)
        inputs={'repository':scope}
        authorization='CONFIRMED_READ_ONLY'
        if cap=='browser.read':
            urls=re.findall(r'https://[^\s,]+',mission['user_intent']); inputs={'url':urls[0] if urls else '','max_chars':20000,'screenshot':False}; authorization='CONFIRMED_BROWSER_READ'
        elif cap=='filesystem.read':
            paths=re.findall(r'(/[^\s,]+)',mission['user_intent']); inputs={'path':paths[0] if paths else '','max_chars':20000}; authorization='CONFIRMED_LOCAL_READ'
        if mode in {'SIMULATION','DRY_RUN'}: authorization='SIMULATION_AUTHORIZED'
        req=CapabilityRequest(core_id('request'),provider_cap,operation,scope,inputs,authorization,'READ_ONLY',mode,mission['objective'],'independent observation integrity verification',mission['workflow_ids'][0],next((x for x in tasks if x.startswith('observe')), 'observe-capability'))
        if not resolution or not provider_name:
            mission['state']='BLOCKED'; mission['current_phase']='WAITING_FOR_APPROVAL'; mission['completion_state']='BLOCKED'; mission['next_action']={'action':'obtain an evidence-backed provider or remain blocked','why':'no acceptable provider resolution','verification':'provider evidence'}
            package['execution']={'request':asdict(req),'provider_bundle':{},'specialist_outputs':[],'external_invocations':0,'writes_performed':False,'deployment_performed':False}
            return self._finish(package,mission,tasks,{},[],{},events,0,writes,store_root,req,{'status':'BLOCKED','reason':'no provider'})
        provider=self.providers.get(provider_name)
        if provider_name=='simulation': bundle=self._simulation(req)
        elif provider is None: bundle={'response':{'status':'FAILED','reality':'UNKNOWN','reason':'provider object unavailable'},'receipt':{'execution_id':core_id('execution'),'request_id':req.request_id,'provider':provider_name,'operation':operation,'scope':scope,'status':'FAILED','side_effects':False,'reality':'UNKNOWN'}}
        elif provider_name=='github-read': bundle=provider.invoke_health(req)
        elif hasattr(provider,'invoke_read'): bundle=provider.invoke_read(req)
        else: bundle=provider.execute(req)
        external=self._call_count(provider) if provider_name not in {None,'simulation'} else 0
        observation_task='observe-repository' if 'observe-repository' in tasks else 'observe-capability'; tasks[observation_task]['state']='OBSERVED' if bundle.get('observation') else ('SIMULATED' if bundle.get('response',{}).get('reality')=='SIMULATED' else 'FAILED'); tasks[observation_task]['reality']=bundle.get('response',{}).get('reality','UNKNOWN'); tasks[observation_task]['output']=bundle
        if bundle.get('response',{}).get('reality')=='SIMULATED':
            obs=bundle.get('observation',{}); mission['observations']=[{'id':obs.get('id') or core_id('observation'),'source':provider_name,'reality':'SIMULATED','scope':scope}]; mission['state']='PARTIAL'; mission['current_phase']='SIMULATED_ANALYSIS'; mission['completion_state']='PARTIAL'; mission['reality']='SIMULATED'
            for task_id in tasks:
                if task_id!=observation_task: tasks[task_id]['state']='SIMULATED'; tasks[task_id]['reality']='SIMULATED'; tasks[task_id]['output']={'reality':'SIMULATED','source_provider':provider_name,'reason':'simulation does not prove external facts'}
            mission['next_action']={'action':'obtain an authorized real provider observation before claiming mission completion','why':'simulation cannot become observed reality','evidence':[bundle.get('receipt',{}).get('execution_id','')],'verification':'real provider receipt and independent verification'}; package['execution']={'request':asdict(req),'provider_bundle':bundle,'specialist_outputs':[],'external_invocations':0,'writes_performed':False,'deployment_performed':False}; return self._finish(package,mission,tasks,bundle,[],{},events,0,False,store_root,req,{'status':'PARTIAL'})
        if bundle.get('observation'):
            obs=bundle['observation']; obs_id=obs.get('id') or obs.get('content_hash') or obs.get('sha256') or core_id('observation'); mission['observations']=[{'id':obs_id,'source':provider_name,'reality':'OBSERVED','scope':scope}]; mission['state']='EXECUTING'; mission['current_phase']='ANALYSIS'
            if cap=='repository.read':
                specialist_outputs=[]; analysis_ids=['risk-analysis' if 'risk-analysis' in tasks else 'engineering-analysis','security-analysis','qa-analysis' if 'qa-analysis' in tasks else 'documentation-audit']; analysis_roles=['Engineering Specialist','Security Specialist','QA Specialist']
                for task_id,role in zip(analysis_ids,analysis_roles):
                    out=self._analysis(role,obs); tasks[task_id]['state']='INFERRED'; tasks[task_id]['reality']='INFERRED'; tasks[task_id]['output']=out; tasks[task_id]['evidence_ids']=[obs_id]; specialist_outputs.append(out)
                recommendation_id='risk-prioritization' if 'risk-prioritization' in tasks else ('audit-recommendation' if 'audit-recommendation' in tasks else 'recommendation'); tasks[recommendation_id]['state']='INFERRED'; tasks[recommendation_id]['output']={'reality':'INFERRED','source_ids':[obs_id],'recommendation':'Review the observed repository health findings and address the highest-confidence verification gap before feature expansion'}; mission['decisions']=[asdict(DecisionRecord(core_id('decision'),tasks[recommendation_id]['output']['recommendation'],['continue without evidence','request deeper evidence'],['traceability','risk reduction'],[obs_id],'analysis depth is bounded','recommendation is conservative and evidence-traceable','MEDIUM','HIGH','repeat with fresher/deeper evidence'))]
            else:
                specialist_outputs=[self._external_analysis(provider_name,obs)]; tasks['capability-analysis']['state']='INFERRED'; tasks['capability-analysis']['reality']='INFERRED'; tasks['capability-analysis']['output']=specialist_outputs[0]; tasks['capability-analysis']['evidence_ids']=[obs_id]
            provider_verification=bundle.get('verification',{}); verified=(provider_verification.get('status')=='VERIFIED' or provider_verification.get('verification_state')=='VERIFIED' or provider_verification.get('verification',{}).get('verification_state')=='VERIFIED' or provider_verification.get('verification',{}).get('status')=='SUCCESS' or bundle.get('response',{}).get('verification')=='VERIFIED'); verification_task='mission-verification'; tasks[verification_task]['state']='VERIFIED' if verified else 'FAILED'; tasks[verification_task]['reality']='VERIFIED' if verified else 'UNKNOWN'; tasks[verification_task]['output']={'status':'VERIFIED' if verified else 'FAILED','independent':bool(bundle.get('verification',{}).get('independent',True)),'receipt_id':bundle.get('receipt',{}).get('execution_id'),'observation_id':obs_id}
            criteria=CompletionCriteria('criteria-'+mission.get('mission_type','capability-read').lower(),mission['success_criteria'],list(tasks.keys()),'OBSERVED'); evidence=CompletionEvidence(core_id('completion-evidence'),criteria.criteria_id,[obs_id,bundle.get('receipt',{}).get('execution_id','')],verified,'OBSERVED','real observation, receipt, and independent verification are present' if verified else 'provider observation did not pass independent verification'); ver=CompletionVerification(core_id('completion-verification'),criteria.criteria_id,'VERIFIED' if verified else 'FAILED',verified,[evidence.evidence_id,bundle.get('receipt',{}).get('execution_id','')],'provider-independent observation integrity verifier','criteria satisfied and independently checked' if verified else 'verification failed; no false completion')
            mission['verification']={'criteria':asdict(criteria),'evidence':asdict(evidence),'completion_verification':asdict(ver)}; mission['completion_state']='COMPLETED' if verified else 'FAILED'; mission['state']='COMPLETED' if verified else 'FAILED'; mission['current_phase']='COMPLETED' if verified else 'REPLANNING'; mission['reality']='OBSERVED'; mission['next_action']={'action':'review verified capability result' if verified else 'classify provider failure and retry only within bounded policy','why':'provider observation and independent verification outcome','evidence':[obs_id,bundle.get('receipt',{}).get('execution_id','')],'verification':'new mission-specific evidence required'}; package['execution']={'request':asdict(req),'provider_bundle':bundle,'specialist_outputs':specialist_outputs,'external_invocations':external,'writes_performed':writes,'deployment_performed':False}; return self._finish(package,mission,tasks,bundle,specialist_outputs,ver,events,external,writes,store_root,req,{'status':'COMPLETED' if verified else 'FAILED'})
        mission['state']='FAILED' if bundle.get('response',{}).get('status')=='FAILED' else 'PARTIAL'; mission['current_phase']='REPLANNING'; mission['completion_state']=mission['state']; mission['next_action']={'action':'classify provider failure and use fallback or block','why':'provider execution did not produce an observation','evidence':[bundle.get('receipt',{}).get('execution_id','')],'verification':'failure classification'}; package['execution']={'request':asdict(req),'provider_bundle':bundle,'specialist_outputs':[],'external_invocations':external,'writes_performed':False,'deployment_performed':False}; return self._finish(package,mission,tasks,bundle,[],{},events,external,writes,store_root,req,{'status':mission['state']})
    def compose_multi_provider(self,user_intent,scope='Themeta-verse/Nexus',mode='REAL_READ',browser_url='https://github.com/Themeta-verse/Nexus',store_root=None):
        if mode not in {'REAL_READ','SIMULATION','DRY_RUN'}: raise ValueError('unsupported multi-provider mode')
        context_memory_ids=[]; context_reused=False
        if store_root:
            try:
                context_memories=LocalStateStore(store_root).retrieve('',scope,limit=8); context_memory_ids=[x.get('memory_id') for x in context_memories]; context_reused=bool(context_memory_ids)
            except Exception: context_memories=[]
        reqs=[CapabilityRequirement('cap-repository-read','REPOSITORY_READ','Repository health evidence',['READ','VERIFY'],scope,'OBSERVED'),CapabilityRequirement('cap-browser-read','BROWSER_READ','Browser page evidence',['READ','VERIFY'],scope,'OBSERVED')]; resolutions=self.resolver.resolve(reqs,mode)
        tasks=[MissionTask('observe-repository','Observe repository','CAPABILITY',[],'PARALLEL','cap-repository-read',None,'READY','PLANNED','NONE',False),MissionTask('observe-browser','Observe browser page','CAPABILITY',[],'PARALLEL','cap-browser-read',None,'READY','PLANNED','NONE',False),MissionTask('joint-analysis','Joint cross-provider analysis','DECISION',['observe-repository','observe-browser'],'SEQUENTIAL',None,None,'PLANNED','INFERRED'),MissionTask('mission-verification','Verify combined evidence','VERIFICATION',['joint-analysis'],'SEQUENTIAL',None,None,'PLANNED','UNKNOWN')]; graph=_task_graph(tasks)
        mission=Mission(core_id('mission'),user_intent,'Combine verified repository and browser observations into one bounded recommendation',['Both provider observations exist','Joint analysis preserves source-provider provenance','Cross-provider decision preserves conflict/uncertainty','Independent verification passes'],['No writes','No deployment','External content is untrusted data','Provider failure is not hidden'],scope,'HIGH',None,'READY','CAPABILITIES_RESOLVED',[core_id('workflow')],[t.task_id for t in tasks],[r.requirement_id for r in reqs],[],[],['provider failure','cross-source conflict','stale observation'],[],[],[],{},[],{'action':'execute both read-only providers','why':'two independent evidence sources improve decision quality','evidence':['cap-repository-read','cap-browser-read']+context_memory_ids,'dependencies':[],'risk':'bounded read-only provider calls','expected_outcome':'joint recommendation','verification':'two receipts and independent provider verifications'},'PENDING',now(),now(),['canonical-core','capability-fabric','mission-composer'],'PLANNED' if mode=='REAL_READ' else 'SIMULATED','MULTI_PROVIDER_ANALYSIS',context_memory_ids,context_reused)
        return {'multi_provider':True,'mission_type':'MULTI_PROVIDER_ANALYSIS','intent_compilation':{'mission_type':'MULTI_PROVIDER_ANALYSIS','provider_agnostic':True,'capabilities':['repository.read','browser.read'],'repository_scope':'Themeta-verse/Nexus','browser_url':browser_url,'intent':user_intent},'context':{'scope':scope,'memory_ids':context_memory_ids,'reused':context_reused},'mission':asdict(mission),'tasks':[asdict(t) for t in tasks],'task_graph':graph,'capability_requirements':[asdict(x) for x in reqs],'capability_resolution':resolutions,'provider_resolution':{'selected':[r for r in resolutions if r['selected_provider']],'unavailable':[r for r in resolutions if not r['selected_provider']],'mode':mode},'workflow_graph':{'workflow_ids':mission.workflow_ids,'task_ids':mission.task_ids,'edges':graph['relations']},'verification_graph':{'criteria_id':'criteria-multi-provider-analysis','dependencies':graph['order'],'final':'mission-verification'},'reality_graph':{'nodes':[{'id':'repository-observation','reality':'OBSERVED' if mode=='REAL_READ' else 'SIMULATED'},{'id':'browser-observation','reality':'OBSERVED' if mode=='REAL_READ' else 'SIMULATED'},{'id':'joint-analysis','reality':'INFERRED' if mode=='REAL_READ' else 'SIMULATED'},{'id':'mission-verification','reality':'VERIFIED' if mode=='REAL_READ' else 'SIMULATED'}],'edges':['repository->joint-analysis','browser->joint-analysis','joint-analysis->verification']},'specialists':[],'no_external_invocations':True}
    def execute_multi_provider(self,package,store_root=None,mode=None):
        mode=mode or package.get('provider_resolution',{}).get('mode','REAL_READ'); mission=package['mission']; tasks={t['task_id']:t for t in package['tasks']}; resolutions={x['requirement_id']:x for x in package['capability_resolution']}; scope=mission['scope']; provider_scope=package.get('intent_compilation',{}).get('repository_scope','Themeta-verse/Nexus'); url=package['intent_compilation']['browser_url']; req_repo=CapabilityRequest(core_id('request'),'github-read','repository.health.read',provider_scope,{'repository':provider_scope},'CONFIRMED_READ_ONLY' if mode=='REAL_READ' else 'SIMULATION_AUTHORIZED','READ_ONLY',mode,'repository observation','independent RepositoryObservation comparison',mission['workflow_ids'][0],'observe-repository'); req_browser=CapabilityRequest(core_id('request'),'browser-read','browser.read',scope,{'url':url,'max_chars':12000,'screenshot':False},'CONFIRMED_BROWSER_READ' if mode=='REAL_READ' else 'SIMULATION_AUTHORIZED','READ_ONLY',mode,'browser observation','content integrity verification',mission['workflow_ids'][0],'observe-browser')
        if any(not resolutions.get(k,{}).get('selected_provider') for k in ('cap-repository-read','cap-browser-read')):
            mission['state']='BLOCKED'; mission['current_phase']='WAITING_FOR_APPROVAL'; mission['completion_state']='BLOCKED'; package['execution']={'request':asdict(req_repo),'provider_bundle':{},'provider_bundles':{},'external_invocations':0,'writes_performed':False,'deployment_performed':False}; return self._finish(package,mission,tasks,{},[],{},[],0,False,store_root,req_repo,{'status':'BLOCKED'})
        if mode in {'SIMULATION','DRY_RUN'}: b_repo=self._simulation(req_repo); b_browser=self._simulation(req_browser)
        else: b_repo=self.providers['github-read'].invoke_health(req_repo); b_browser=self.providers['browser-read'].invoke_read(req_browser)
        bundles={'github-read':b_repo,'browser-read':b_browser}; calls=sum(self._call_count(self.providers[k]) for k in ('github-read','browser-read')) if mode=='REAL_READ' else 0; obs_repo=b_repo.get('observation'); obs_browser=b_browser.get('observation');
        def _verification(bundle):
            v=bundle.get('verification',{}) if isinstance(bundle,dict) else {}
            return 'VERIFIED' if (v.get('status')=='VERIFIED' or v.get('verification_state')=='VERIFIED' or v.get('verification',{}).get('verification_state')=='VERIFIED') else 'UNKNOWN'
        normalized_observations=[]
        if obs_repo:
            normalized_observations.append(normalize_observation(source='github-api',provider='github-read',capability='repository.read',scope=provider_scope,raw=obs_repo.get('raw',obs_repo),receipt=b_repo.get('receipt'),authority='repository-api',verification_state=_verification(b_repo),freshness_policy={'fresh_seconds':3600,'aging_seconds':86400,'stale_seconds':604800,'expired_seconds':1209600}))
        if obs_browser:
            normalized_observations.append(normalize_observation(source='browser-page',provider='browser-read',capability='browser.read',scope=scope,raw=obs_browser.get('text',obs_browser),receipt=b_browser.get('receipt'),authority='browser-visible-page',verification_state=_verification(b_browser),freshness_policy={'fresh_seconds':3600,'aging_seconds':86400,'stale_seconds':604800,'expired_seconds':1209600}))
        reconciliation=reconcile_sources(normalized_observations)
        tasks['observe-repository']['state']='OBSERVED' if obs_repo else ('SIMULATED' if mode!='REAL_READ' else 'FAILED'); tasks['observe-browser']['state']='OBSERVED' if obs_browser else ('SIMULATED' if mode!='REAL_READ' else 'FAILED'); tasks['observe-repository']['reality']=b_repo.get('response',{}).get('reality','UNKNOWN'); tasks['observe-browser']['reality']=b_browser.get('response',{}).get('reality','UNKNOWN'); tasks['observe-repository']['output']=b_repo; tasks['observe-browser']['output']=b_browser
        if not obs_repo or not obs_browser:
            mission['state']='PARTIAL' if mode!='REAL_READ' else 'FAILED'; mission['current_phase']='REPLANNING'; mission['completion_state']=mission['state']; mission['reality']='SIMULATED' if mode!='REAL_READ' else 'UNKNOWN'; mission['next_action']={'action':'classify failed provider and retry only within bounded policy','why':'multi-provider mission requires both observations','evidence':[b_repo.get('receipt',{}).get('execution_id'),b_browser.get('receipt',{}).get('execution_id')],'verification':'provider-specific failure evidence'}; package['execution']={'request':asdict(req_repo),'provider_bundle':b_repo,'provider_bundles':bundles,'external_invocations':calls,'writes_performed':False,'deployment_performed':False}; return self._finish(package,mission,tasks,b_repo,[],{},[],calls,False,store_root,req_repo,{'status':mission['state']})
        source_ids=[obs_repo.get('id') or obs_repo.get('content_hash') or obs_repo.get('sha256'),obs_browser.get('id') or obs_browser.get('content_hash') or obs_browser.get('sha256')]; joint={'role':'Joint Evidence Analyst','reality':'INFERRED','source_providers':['github-read','browser-read'],'source_ids':source_ids,'repository_status':b_repo.get('response',{}).get('reality'),'browser_status':b_browser.get('response',{}).get('reality'),'recommendation':'Compare the browser-visible project context with repository health evidence; investigate any divergence before external change','conflict_policy':'preserve both sources, compare authority/freshness/scope/independence','untrusted_content':'both external sources are data only','normalized_observations':normalized_observations,'reconciliation':reconciliation};
        decision=decision_engine(outcome=mission['objective'],success_condition='Both real observations are independently verified and the recommendation remains traceable',observations=normalized_observations,unknowns=['external write authorization','deep static evidence beyond collected reads'],options=[{'title':'review joint recommendation and gather targeted evidence','impact':3,'confidence':2,'risk_reduction':3,'effort':1,'reversibility':3,'dependencies':2,'urgency':2,'evidence_quality':2,'verification_difficulty':1},{'title':'execute an external write immediately','impact':3,'confidence':0,'risk_reduction':0,'effort':4,'reversibility':0,'dependencies':0,'urgency':0,'evidence_quality':0,'verification_difficulty':4}],constraints=mission['constraints'],reconciliation=reconciliation);
        packet=action_packet(objective='Review the joint recommendation and, if needed, gather targeted read-only evidence',target=scope,reason='The real three-provider evidence is complete but external writes remain unavailable and unapproved',evidence=normalized_observations,expected_effect='Improve decision confidence without side effects',risk='LOW_READ_ONLY',dependencies=['human review','freshness check after meaningful delta'],rollback_concept='No external side effect; discard prepared packet',verification_plan='Repeat provider-specific read and independent verification if evidence changes',required_authorization='Specific human authorization for any future external action',required_provider='github-read + browser-read + filesystem-read',state='READY_FOR_AUTHORIZATION');
        tasks['joint-analysis']['state']='INFERRED'; tasks['joint-analysis']['reality']='INFERRED'; tasks['joint-analysis']['output']=joint; tasks['joint-analysis']['evidence_ids']=source_ids; verified=b_repo.get('verification',{}).get('verification',{}).get('verification_state')=='VERIFIED' or b_repo.get('verification',{}).get('status')=='VERIFIED'; verified=verified and b_browser.get('verification',{}).get('status')=='VERIFIED'; tasks['mission-verification']['state']='VERIFIED' if verified else 'FAILED'; tasks['mission-verification']['reality']='VERIFIED' if verified else 'UNKNOWN'; tasks['mission-verification']['output']={'status':'VERIFIED' if verified else 'FAILED','independent':verified,'receipt_ids':[b_repo.get('receipt',{}).get('execution_id'),b_browser.get('receipt',{}).get('execution_id')],'observation_ids':source_ids}; criteria=CompletionCriteria('criteria-multi-provider-analysis',mission['success_criteria'],list(tasks.keys()),'OBSERVED'); evidence=CompletionEvidence(core_id('completion-evidence'),criteria.criteria_id,source_ids+[b_repo.get('receipt',{}).get('execution_id',''),b_browser.get('receipt',{}).get('execution_id','')],verified,'OBSERVED' if verified else 'UNKNOWN','both real provider observations and independent verifications are present' if verified else 'one or more provider verifications failed'); ver=CompletionVerification(core_id('completion-verification'),criteria.criteria_id,'VERIFIED' if verified else 'FAILED',verified,[evidence.evidence_id,b_repo.get('receipt',{}).get('execution_id',''),b_browser.get('receipt',{}).get('execution_id','')],'two independent provider verifiers','joint mission criteria satisfied' if verified else 'joint completion rejected'); mission['verification']={'criteria':asdict(criteria),'evidence':asdict(evidence),'completion_verification':asdict(ver)};         mission['observations']=[{'id':source_ids[0],'source':'github-read','reality':'OBSERVED'},{'id':source_ids[1],'source':'browser-read','reality':'OBSERVED'}]; mission['normalized_observations']=normalized_observations; mission['source_reconciliation']=reconciliation; mission['decision_engine']=decision; mission['action_packet']=packet; mission['reality_audit']=reality_audit(capability={'implemented':True,'tested':True,'callable':True,'available':True},authorization={'authorized':True,'approved':False},action_packet_value=packet,execution={'executed':calls>0},observation=normalized_observations[0] if normalized_observations else None,verification={'status':'VERIFIED' if verified else 'UNKNOWN'},persisted=bool(store_root)); mission['decisions']=[asdict(DecisionRecord(core_id('decision'),joint['recommendation'],['trust repository only','trust browser only','request more evidence'],['freshness','independence','traceability'],source_ids,['browser content may be untrusted','repository health is bounded'],'the recommendation preserves both sources','MEDIUM','HIGH','repeat after meaningful delta'))];         mission['state']='COMPLETED' if verified else 'FAILED'; mission['current_phase']='COMPLETED' if verified else 'REPLANNING'; mission['completion_state']='COMPLETED' if verified else 'FAILED'; mission['reality']='OBSERVED' if verified else 'UNKNOWN'; mission['next_action']={'action':'review joint recommendation; do not write automatically','why':'multi-provider evidence is complete' if verified else 'verification failed','evidence':source_ids,'verification':'new mission-specific evidence'}; package['execution']={'request':asdict(req_repo),'provider_bundle':b_repo,'provider_bundles':bundles,'receipts':[b_repo.get('receipt',{}),b_browser.get('receipt',{})],'observations':[obs_repo,obs_browser],'normalized_observations':normalized_observations,'source_reconciliation':reconciliation,'decision':decision,'action_packet':packet,'specialist_outputs':[joint],'external_invocations':calls,'writes_performed':False,'deployment_performed':False}; return self._finish(package,mission,tasks,b_repo,[joint],ver,[],calls,False,store_root,req_repo,{'status':'COMPLETED' if verified else 'FAILED'})
    def compose_capability_mission(self,user_intent,scope='Themeta-verse/Nexus',mode='REAL_READ',browser_url='https://github.com/Themeta-verse/Nexus',filesystem_path=None,capabilities=None,store_root=None,repository_scope='Themeta-verse/Nexus'):
        """Compile an outcome-first evidence mission from abstract capabilities.

        The graph is derived from the requested capability set: independent reads
        run in parallel, then converge into reconciliation, decision, and
        verification. This remains a MissionComposer mode, not a second engine.
        """
        from dataclasses import asdict
        if mode not in {'REAL_READ','SIMULATION','DRY_RUN'}:
            raise ValueError('unsupported generalized mission mode')
        requested_capabilities=capabilities
        if capabilities is None:
            lowered=(user_intent or '').lower()
            metadata_hints=('metadata','identity','owner','visibility','default branch','repository name','quick check')
            deep_hints=('decide','whether','ready','health','risk','deep','compare','commit','tree','readme','test','issue','pull request')
            capabilities=['repository.metadata.read'] if any(x in lowered for x in metadata_hints) and not any(x in lowered for x in deep_hints) else ['repository.read','browser.read','filesystem.read']
            selection_policy='AUTO_MINIMUM_SUFFICIENT_METADATA' if capabilities==['repository.metadata.read'] else 'AUTO_FULL_EVIDENCE_FOR_DECISION_OR_UNSPECIFIED_INTENT'
        else:
            selection_policy='EXPLICIT_CAPABILITY_SELECTION'
        allowed={'repository.read','repository.metadata.read','browser.read','filesystem.read'}
        if not capabilities or any(x not in allowed for x in capabilities):
            raise ValueError('generalized mission supports only evidence-backed read capabilities')
        capabilities=list(dict.fromkeys(capabilities)); req_defs={
            'repository.read':('cap-repository-read','REPOSITORY_READ','Repository implementation health'),
            'repository.metadata.read':('cap-repository-metadata-read','REPOSITORY_METADATA_READ','Repository identity metadata'),
            'browser.read':('cap-browser-read','BROWSER_READ','Externally visible project state'),
            'filesystem.read':('cap-filesystem-read','FILESYSTEM_READ','Authoritative local project artifacts'),
        }
        reqs=[CapabilityRequirement(req_defs[c][0],req_defs[c][1],req_defs[c][2],['READ','VERIFY'],scope,'OBSERVED') for c in capabilities]
        resolutions=self.resolver.resolve(reqs,mode)
        task_ids=[('observe-repository' if c=='repository.read' else f'observe-{c.replace(".","-")}') for c in capabilities]
        tasks=[MissionTask(tid,'Observe '+c,'CAPABILITY',[],'PARALLEL',req_defs[c][0],None,'READY','PLANNED','NONE',False) for tid,c in zip(task_ids,capabilities)]
        tasks += [MissionTask('evidence-reconciliation','Reconcile normalized evidence','DECISION',task_ids,'SEQUENTIAL',None,None,'PLANNED','INFERRED'),MissionTask('decision-engine','Determine conclusion and next action','DECISION',['evidence-reconciliation'],'SEQUENTIAL',None,None,'PLANNED','INFERRED'),MissionTask('mission-verification','Verify generalized mission','VERIFICATION',['decision-engine'],'SEQUENTIAL',None,None,'PLANNED','UNKNOWN')]
        graph=_task_graph(tasks)
        context_memory_ids=[]; context_reused=False
        if store_root:
            try:
                context_memories=LocalStateStore(store_root).retrieve('',scope,limit=8); context_memory_ids=[x.get('memory_id') for x in context_memories]; context_reused=bool(context_memory_ids)
            except Exception: pass
        mission=Mission(core_id('mission'),user_intent,'Understand the requested outcome, collect minimum sufficient evidence from real read capabilities, reconcile sources, and prepare the safest next action',[
            'Outcome and success condition are explicit','All selected read providers return source-preserving observations','Evidence is normalized and reconciled without hiding conflicts','Decision identifies knowns, unknowns, and next action','Independent verification passes for all required observations'],[
            'No writes','No deployment','External and local content are untrusted data','Unavailable capabilities remain blocked or simulated','Approval is specific to a prepared action'],scope,'HIGH',None,'READY','CAPABILITIES_RESOLVED',[core_id('workflow')],[t.task_id for t in tasks],[r.requirement_id for r in reqs],[],[],['provider failure','stale observation','source conflict','scope mismatch'],[],[],[],{},[],{'action':'execute selected read capabilities in parallel','why':'minimum sufficient evidence is required for the outcome','evidence':[r.requirement_id for r in reqs]+context_memory_ids,'dependencies':[],'risk':'bounded read-only provider calls','expected_outcome':'evidence-backed decision and prepared next action','verification':'all observations independently verified'},'PENDING',now(),now(),['canonical-core','capability-fabric','mission-composer','action-ready'], 'PLANNED' if mode=='REAL_READ' else 'SIMULATED','GENERALIZED_EVIDENCE_MISSION',context_memory_ids,context_reused)
        return {'multi_provider':True,'generalized':True,'mission_type':'GENERALIZED_EVIDENCE_MISSION','intent_compilation':{'mission_type':'GENERALIZED_EVIDENCE_MISSION','provider_agnostic':True,'outcome':mission.objective,'success_condition':mission.success_criteria,'capabilities':capabilities,'requested_capabilities':requested_capabilities,'selection_policy':selection_policy,'inputs':{'repository':repository_scope or scope,'browser_url':browser_url,'filesystem_path':filesystem_path},'intent':user_intent},'context':{'scope':scope,'memory_ids':context_memory_ids,'reused':context_reused},'mission':asdict(mission),'tasks':[asdict(t) for t in tasks],'task_graph':graph,'capability_requirements':[asdict(x) for x in reqs],'capability_resolution':resolutions,'provider_resolution':{'selected':[r for r in resolutions if r['selected_provider']],'unavailable':[r for r in resolutions if not r['selected_provider']],'mode':mode},'workflow_graph':{'workflow_ids':mission.workflow_ids,'task_ids':mission.task_ids,'edges':graph['relations']},'verification_graph':{'criteria_id':'criteria-generalized-evidence-mission','dependencies':graph['order'],'final':'mission-verification'},'reality_graph':{'nodes':[{'id':t.task_id,'reality':'OBSERVED' if mode=='REAL_READ' else 'SIMULATED'} for t in tasks],'edges':graph['relations']},'specialists':[],'no_external_invocations':True}

    def execute_capability_mission(self,package,store_root=None,mode=None):
        """Execute a compiled generalized evidence mission through existing providers."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        mode=mode or package.get('provider_resolution',{}).get('mode','REAL_READ'); mission=package['mission']; tasks={t['task_id']:t for t in package['tasks']}; scope=mission['scope']; inputs=package.get('intent_compilation',{}).get('inputs',{}); resolutions={x['requirement_id']:x for x in package['capability_resolution']}; req_by_cap={x['capability'].lower().replace('_','.'):x for x in package['capability_requirements']}; cap_to_task={c:('observe-repository' if c=='repository.read' else f'observe-{c.replace(".","-")}') for c in package['intent_compilation']['capabilities']};         provider_map={'repository.read':'github-read','repository.metadata.read':'github-read','browser.read':'browser-read','filesystem.read':'filesystem-read'}; operation_map={'repository.read':'repository.health.read','repository.metadata.read':'repository.metadata.read','browser.read':'browser.read','filesystem.read':'filesystem.read'}; auth_map={'repository.read':'CONFIRMED_READ_ONLY','repository.metadata.read':'CONFIRMED_READ_ONLY','browser.read':'CONFIRMED_BROWSER_READ','filesystem.read':'CONFIRMED_LOCAL_READ'}

        requests={}; jobs={}; bundles={}; evidence_gates={}; reused_bundles={}; before_counts={name:self._call_count(provider) for name,provider in self.providers.items()}; cached_execution={}; prior_observations=[]
        if store_root and mode=='REAL_READ':
            try:
                cached_store=LocalStateStore(store_root); cached_snapshot=cached_store.load()
                if cached_snapshot and cached_snapshot.scope==scope:
                    cached_execution=cached_snapshot.state.get('execution',{}) or {}
                    prior_observations=cached_execution.get('normalized_observations',[]) or cached_snapshot.state.get('mission',{}).get('normalized_observations',[])
            except Exception:
                cached_execution={}; prior_observations=[]
        intent_lower=(mission.get('user_intent') or '').lower(); decision_terms=('decide','whether','should','authorize','recommend','risk','bottleneck','ready')
        decision_sensitivity='HIGH' if any(x in intent_lower for x in decision_terms) else 'MEDIUM'; uncertainty=['source conflict','write authorization'] if len(package['intent_compilation']['capabilities'])>1 else []; source_conflict=bool((cached_execution.get('source_reconciliation') or {}).get('status')=='CONFLICT')
        for cap in package['intent_compilation']['capabilities']:
            req_info=req_by_cap[cap]; resolution=resolutions.get(req_info['requirement_id'],{}); provider_name=resolution.get('selected_provider'); task_id=cap_to_task[cap]; auth=auth_map[cap] if mode=='REAL_READ' else 'SIMULATION_AUTHORIZED'; call_inputs={'repository':inputs.get('repository',scope)} if cap in {'repository.read','repository.metadata.read'} else ({'url':inputs.get('browser_url',''),'max_chars':12000,'screenshot':False} if cap=='browser.read' else {'path':inputs.get('filesystem_path',''),'max_chars':20000}); request_scope=inputs.get('repository',scope) if cap in {'repository.read','repository.metadata.read'} else scope; req=CapabilityRequest(core_id('request'),provider_map[cap],operation_map[cap],request_scope,call_inputs,auth,'READ_ONLY',mode,mission['objective'],'independent observation integrity verification',mission['workflow_ids'][0],task_id); requests[cap]=req
            if not provider_name:
                bundles[cap]={'response':{'status':'BLOCKED','reality':'UNKNOWN','reason':'no evidence-backed provider'},'receipt':{'execution_id':core_id('execution'),'request_id':req.request_id,'provider':None,'operation':req.operation,'scope':scope,'status':'BLOCKED','side_effects':False,'reality':'UNKNOWN','failure_state':'provider unavailable'}}; tasks[task_id]['state']='BLOCKED'; tasks[task_id]['reality']='UNKNOWN'; continue
            gate=evidence_gate(capability=cap,provider=provider_name,requested_scope=request_scope,prior_observations=prior_observations,uncertainty=uncertainty,decision_sensitivity=decision_sensitivity,source_conflict=source_conflict,could_change_decision=bool(uncertainty and decision_sensitivity=='HIGH')); evidence_gates[cap]=gate
            cached_bundles=cached_execution.get('provider_bundles',{}) or {}; cached_bundle=cached_bundles.get(cap) or cached_bundles.get(provider_name) or (cached_execution.get('provider_bundle',{}) if provider_name=='github-read' else {})
            if mode=='REAL_READ' and gate['decision']=='REUSE' and cached_bundle.get('observation'):
                bundles[cap]=cached_bundle; reused_bundles[cap]=gate; tasks[task_id]['state']='OBSERVED'; tasks[task_id]['reality']='OBSERVED'; continue
            provider=self.providers.get(provider_name)
            def invoke(cap=cap,provider_name=provider_name,provider=provider,req=req):
                if mode in {'SIMULATION','DRY_RUN'}: return cap,self._simulation(req)
                if provider_name=='github-read': return cap,provider.invoke_metadata(req) if cap=='repository.metadata.read' else provider.invoke_health(req)
                return cap,provider.invoke_read(req)
            jobs[cap]=invoke
        if mode=='REAL_READ' and len(jobs)>1:
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                futures=[pool.submit(job) for job in jobs.values()]
                for future in as_completed(futures): cap,bundle=future.result(); bundles[cap]=bundle
        else:
            for job in jobs.values(): cap,bundle=job(); bundles[cap]=bundle
        normalized=[]; raw_observations=[]; receipts=[]; failures=[]
        def verification(bundle):
            v=bundle.get('verification',{}) if isinstance(bundle,dict) else {}; return 'VERIFIED' if (v.get('status')=='VERIFIED' or v.get('verification_state')=='VERIFIED' or v.get('verification',{}).get('verification_state')=='VERIFIED') else 'UNKNOWN'
        for cap in package['intent_compilation']['capabilities']:
            task_id=cap_to_task[cap]; bundle=bundles.get(cap,{}); provider_name=provider_map[cap]; obs=bundle.get('observation'); receipt=bundle.get('receipt',{}); reality=bundle.get('response',{}).get('reality','UNKNOWN'); tasks[task_id]['output']=bundle; tasks[task_id]['reality']=reality
            if obs:
                tasks[task_id]['state']='OBSERVED' if reality=='OBSERVED' else 'SIMULATED'; raw_observations.append(obs); receipts.append(receipt); raw=obs.get('raw',obs) if cap in {'repository.read','repository.metadata.read'} else obs.get('text',obs); authority='repository-api-metadata' if cap=='repository.metadata.read' else 'repository-api' if cap=='repository.read' else 'browser-visible-page' if cap=='browser.read' else 'bounded-local-file'; policy={'fresh_seconds':3600,'aging_seconds':86400,'stale_seconds':604800,'expired_seconds':1209600}; observation_scope=inputs.get('repository',scope) if cap in {'repository.read','repository.metadata.read'} else scope; normalized.append(normalize_observation(source=provider_name,provider=provider_name,capability=cap,scope=observation_scope,raw=raw,receipt=receipt,authority=authority,verification_state=verification(bundle),freshness_policy=policy,reality=reality))
            else:
                tasks[task_id]['state']='FAILED' if mode=='REAL_READ' else 'SIMULATED'; failures.append({'capability':cap,'provider':provider_name,'receipt':receipt,'reason':bundle.get('response',{}).get('reason','observation unavailable')})
        calls=sum(max(0,self._call_count(self.providers[name])-before_counts.get(name,0)) for name in set(provider_map.values()) if name in self.providers) if mode=='REAL_READ' else 0; reconciliation=reconcile_sources(normalized); missing=[x['capability'] for x in failures]; all_verified=len(normalized)==len(package['intent_compilation']['capabilities']) and all(x.get('verification_state')=='VERIFIED' for x in normalized); source_ids=[x.get('observation_id') for x in normalized]
        decision=decision_engine(outcome=mission['objective'],success_condition='All selected read observations are independently verified and reconciled',observations=normalized,unknowns=['unavailable providers','external write authorization']+missing,options=[{'title':'review evidence and gather only targeted additional reads','impact':3,'confidence':2,'risk_reduction':3,'effort':1,'reversibility':3,'dependencies':2,'urgency':2,'evidence_quality':2,'verification_difficulty':1}],constraints=mission['constraints'],reconciliation=reconciliation)
        packet=action_packet(objective='Review generalized evidence and prepare the next governed step',target=scope,reason='The mission has source-preserving observations and an explicit uncertainty boundary',evidence=normalized,expected_effect='Improve decision quality without executing side effects',risk='LOW_READ_ONLY',dependencies=['human review','freshness check'],rollback_concept='No external side effect; discard packet',verification_plan='Re-run only failed or stale branches and reverify',required_authorization='Specific action approval for any future external effect',required_provider='; '.join(sorted(set(provider_map[c] for c in package['intent_compilation']['capabilities']))),state='READY_FOR_AUTHORIZATION' if not failures else 'BLOCKED_BY_CAPABILITY')
        tasks['evidence-reconciliation']['state']='INFERRED'; tasks['evidence-reconciliation']['reality']='INFERRED'; tasks['evidence-reconciliation']['output']=reconciliation; tasks['evidence-reconciliation']['evidence_ids']=source_ids; tasks['decision-engine']['state']='INFERRED'; tasks['decision-engine']['reality']='INFERRED'; tasks['decision-engine']['output']=decision; tasks['decision-engine']['evidence_ids']=source_ids
        if failures:
            mission['state']='PARTIAL' if normalized else 'FAILED'; mission['current_phase']='REPLANNING'; mission['completion_state']=mission['state']; mission['reality']='OBSERVED' if normalized else 'UNKNOWN'; tasks['mission-verification']['state']='BLOCKED'; tasks['mission-verification']['reality']='UNKNOWN'; mission['next_action']={'action':'retry only failed or stale branches after provider recovery','why':'unrelated completed observations are preserved','evidence':source_ids,'verification':'provider-specific receipt and independent verification'}; recovery={'status':'PARTIAL','failed_branches':failures,'completed_branches':[cap for cap in package['intent_compilation']['capabilities'] if cap not in missing],'blocked_branches':['mission-verification'],'replan':'smallest affected subgraph only'}
        else:
            mission['state']='COMPLETED' if all_verified and mode=='REAL_READ' else 'PARTIAL'; mission['current_phase']='COMPLETED' if mission['state']=='COMPLETED' else 'SIMULATED_ANALYSIS'; mission['completion_state']=mission['state']; mission['reality']='OBSERVED' if mode=='REAL_READ' and all_verified else 'SIMULATED'; tasks['mission-verification']['state']='VERIFIED' if mission['state']=='COMPLETED' else 'SIMULATED'; tasks['mission-verification']['reality']='VERIFIED' if mission['state']=='COMPLETED' else 'SIMULATED'; mission['next_action']={'action':'review prepared action packet; do not execute automatically','why':'all required evidence is present' if mission['state']=='COMPLETED' else 'simulation cannot prove external facts','evidence':source_ids,'verification':'new mission-specific evidence required'}; recovery={'status':'COMPLETED' if mission['state']=='COMPLETED' else 'SIMULATED','failed_branches':[],'completed_branches':list(package['intent_compilation']['capabilities']),'blocked_branches':[],'replan':'none'}
        mission['observations']=[{'id':x.get('observation_id'),'source':x.get('provider'),'capability':x.get('capability'),'reality':x.get('reality')} for x in normalized]; mission['normalized_observations']=normalized; mission['source_reconciliation']=reconciliation; mission['decision_engine']=decision; mission['action_packet']=packet; mission['recovery']=recovery; mission['decisions']=[asdict(DecisionRecord(core_id('decision'),(decision.get('best_option') or {}).get('title','Review evidence and preserve uncertainty'),['request targeted evidence','stop with insufficient evidence'],['evidence quality','risk reduction','reversibility'],source_ids,['provider availability and freshness are bounded'],'the recommendation preserves uncertainty and approval boundaries','MEDIUM','HIGH','reverify after meaningful delta'))]; mission['verification']={'criteria':{'criteria_id':'criteria-generalized-evidence-mission','statements':mission['success_criteria'],'required_task_ids':list(tasks.keys()),'required_reality':'OBSERVED'},'evidence':{'evidence_id':core_id('completion-evidence'),'source_ids':source_ids+[x.get('execution_id') for x in receipts],'satisfied':mission['state']=='COMPLETED','reality':'OBSERVED' if mission['state']=='COMPLETED' else mission['reality'],'explanation':'all selected provider observations were independently verified' if mission['state']=='COMPLETED' else 'partial or simulated evidence cannot prove full completion'},'completion_verification':{'verification_id':core_id('completion-verification'),'criteria_id':'criteria-generalized-evidence-mission','status':'VERIFIED' if mission['state']=='COMPLETED' else 'BLOCKED' if failures else 'SIMULATED','independent':mission['state']=='COMPLETED','evidence_ids':source_ids,'authority':'provider-specific integrity verifiers','reason':'generalized mission boundary'}};         execution={'requests':{c:asdict(r) for c,r in requests.items()},'provider_bundles':bundles,'receipts':receipts,'observations':raw_observations,'normalized_observations':normalized,'source_reconciliation':reconciliation,'decision':decision,'action_packet':packet,'failures':failures,'external_invocations':calls,'writes_performed':False,'deployment_performed':False,'selective_acquisition':{'gates':evidence_gates,'reused_capabilities':sorted(reused_bundles),'called_capabilities':sorted(set(package['intent_compilation']['capabilities'])-set(reused_bundles)),'external_calls_saved':len(reused_bundles),'cache_source':'scoped persisted execution evidence' if reused_bundles else 'none'}}; package['execution']=execution; package['replay']={'mission_id':mission['mission_id'],'mode':'OBSERVATIONAL_REPLAY_ONLY','external_side_effects':False}; package['recovery']=recovery; package['specialist_outputs']=[{'role':'Joint Evidence Analyst','reality':'INFERRED','source_ids':source_ids,'reconciliation':reconciliation,'decision':decision}]; package['generalized_evidence']=True; return self._finish(package,mission,tasks,bundles.get('repository.read',next(iter(bundles.values()),{})),package['specialist_outputs'],mission['verification']['completion_verification'],[],calls,False,store_root,requests.get('repository.read',next(iter(requests.values()),None)),{'status':mission['state']})


    def _simulation(self,request):
        return {'response':{'status':'EXECUTED','reality':'SIMULATED','reason':'approved simulation; no external provider'},'receipt':{'execution_id':core_id('execution'),'request_id':request.request_id,'provider':'simulation','operation':request.operation,'scope':request.scope,'status':'EXECUTED','side_effects':False,'reality':'SIMULATED'},'observation':{'id':core_id('observation'),'repository':request.scope,'raw':{},'reality':'SIMULATED'}}
    def _finish(self,package,mission,tasks,bundle,specialists,verification,events,external,writes,store_root,request,status):
        verification_dict=asdict(verification) if hasattr(verification,'__dataclass_fields__') else verification
        if store_root:
            store=LocalStateStore(store_root); previous_snapshot=store.load(); previous_mission=(previous_snapshot.state.get('mission',{}) if previous_snapshot else {}); normalized=package.get('execution',{}).get('normalized_observations',[]) or mission.get('normalized_observations',[]); continuity=continuity_projection(scope=mission['scope'],mission=mission,evidence=normalized,previous_state=previous_mission,lessons=[]); state={'mission':mission,'tasks':tasks,'execution':package.get('execution',{}),'capability_resolution':package['capability_resolution'],'specialist_outputs':specialists,'verification':verification_dict,'reality':mission.get('reality','UNKNOWN'),'next_action':mission.get('next_action',{}),'outcome_graph':continuity.get('outcome_graph',{}),'outcome_continuity':continuity}
            snap=store.checkpoint('VERIFICATION_COMPLETE' if mission['state']=='COMPLETED' else 'MISSION_STATE_UPDATE',state,mission['scope'],verified=mission['state']=='COMPLETED')
            store.append_event('mission_created',{'mission_id':mission['mission_id']},mission['scope'],['omega10-mission-composer'],'mission:'+mission['mission_id'])
            store.append_event('mission_state_changed',{'mission_id':mission['mission_id'],'state':mission['state']},mission['scope'],['omega10-mission-composer'],'state:'+mission['mission_id']+':'+mission['state'])
            if mission.get('open_loops'): store.append_event('open_loop_created',mission['open_loops'][0],mission['scope'],['omega10-mission-composer'],'loop:'+mission['mission_id'])
            if mission.get('decisions'): store.remember('DECISION',mission['scope'],mission['decisions'][0],'omega10-mission-composer','MEDIUM','INFERRED',['mission','recommendation'],'current')
            if mission.get('observations'):
                observed=mission.get('reality')=='OBSERVED'; bundle=package.get('execution',{}).get('provider_bundle',{}); receipt=bundle.get('receipt',{}); provider=receipt.get('provider','unknown-provider'); capability=package.get('intent_compilation',{}).get('capability','multi-provider' if package.get('multi_provider') else 'unknown'); store.remember('VERIFIED_OBSERVATION' if observed else 'SIMULATED_OBSERVATION',mission['scope'],mission['observations'][0],provider if observed else 'simulation','HIGH' if observed else 'LOW','OBSERVED' if observed else 'SIMULATED',['receipt','independent-verifier'] if observed else ['simulation-receipt'],'current'); store.remember('PROVIDER_LESSON',mission['scope'],{'provider':provider,'capability':capability,'operation':receipt.get('operation'),'receipt_id':receipt.get('execution_id'),'observation_ids':[x.get('id') for x in mission.get('observations',[])],'verification':mission.get('verification',{}).get('completion_verification',{}).get('status'),'lesson':'provider returned a bounded observation; keep source-specific evidence and reverify after meaningful change','limitations':getattr(self.providers.get(provider),'limitations',[]) if hasattr(self,'providers') else []},provider,'MEDIUM','INFERRED',['provider','receipt','verification'],'current')
                for extra in package.get('execution',{}).get('receipts',[]):
                    ep=extra.get('provider','unknown-provider'); store.remember('PROVIDER_LESSON',mission['scope'],{'provider':ep,'capability':capability,'operation':extra.get('operation'),'receipt_id':extra.get('execution_id'),'verification':'VERIFIED' if extra.get('verification')=='VERIFIED' else extra.get('verification','UNKNOWN'),'lesson':'retain provider-specific evidence separately from user decisions'},ep,'MEDIUM','INFERRED',['provider','receipt'],'current')
            package['persistence']={'persisted':True,'snapshot':asdict(snap),'events':len(store.events()),'memories':len(store.memories(mission['scope']))}
        else: package['persistence']={'persisted':False,'reason':'store_root not supplied'}
        package['mission']=mission; package['tasks']=[asdict(t) if isinstance(t,MissionTask) else t for t in tasks.values()]; package['verification']=verification_dict; package['external_invocations']=external; package['writes_performed']=writes; package['deployment_performed']=False; package['reality_audit']=reconcile({'capability_status':'AVAILABLE','workflow_status':'EXECUTED' if external else 'SIMULATED','execution_status':'SUCCESS' if status['status']=='COMPLETED' else 'FAILED','verification_status':'VERIFIED' if status['status']=='COMPLETED' else 'UNKNOWN','reality':mission.get('reality'),'final_status':mission['state'],'authorized':True,'verification_target':bool(bundle),'workflow_operation':'READ_ONLY','governance_approved':True,'external_observation':bool(bundle.get('observation') and mission.get('reality')=='OBSERVED'),'source':'github-read-provider' if external else 'simulation','scope':mission['scope'],'cross_project_context':False,'external_content_instruction':False}); package['replay']={'mission_id':mission['mission_id'],'timeline':['MISSION_CREATED','CAPABILITIES_RESOLVED','EXECUTION_COMPLETE','VERIFICATION_COMPLETE' if mission['state']=='COMPLETED' else 'REPLANNING'],'state_changes':[{'state':mission['state'],'timestamp':mission['updated_at']}],'receipt_ids':[bundle.get('receipt',{}).get('execution_id')] if bundle else []}; package['recovery']=self.recover(package); package['dashboard']=self.dashboard(package); package['self_awareness']={'active_missions':[] if mission['state']=='COMPLETED' else [mission['mission_id']],'current_phase':mission['current_phase'],'what_executed':external>0,'what_simulated':mission.get('reality')=='SIMULATED','blocked':mission['state'] in {'BLOCKED','FAILED','PARTIAL'},'needs_from_user':[] if mission['state']=='COMPLETED' else ['provider evidence or approval'], 'capabilities_available':[r['capability'] for r in package['capability_resolution'] if r.get('status') in {'VERIFIED','AVAILABLE'}], 'capabilities_unavailable':['repository.write','deployment'],'what_changed':mission['state'],'why_strategy_changed':'provider/evidence state if replanning'}; return package
    def recover(self,package_or_store,scope=None):
        if isinstance(package_or_store,str):
            store=LocalStateStore(package_or_store); snap=store.load()
            if not snap or (scope is not None and snap.scope!=scope): return {'status':'UNKNOWN','reason':'no persisted mission state for requested scope','snapshot':None,'timeline':[]}
            return {'status':'RECOVERED','snapshot':asdict(snap),'timeline':[asdict(e) for e in store.events() if scope is None or e.scope==scope]}
        m=package_or_store.get('mission',{}); return {'status':'RECOVERABLE','mission_id':m.get('mission_id'),'last_verified_state':m.get('state'),'safe_to_retry':m.get('state') not in {'COMPLETED'},'unsafe_to_repeat':['unknown side effects'] if m.get('state')!='COMPLETED' else [],'next_action':m.get('next_action')}
    def dashboard(self,package):
        m=package['mission']; return {'active_missions':[] if m['state']=='COMPLETED' else [m['mission_id']],'current_phase':m['current_phase'],'blockers':m['risks'] if m['state'] in {'BLOCKED','FAILED','PARTIAL'} else [],'waiting':[],'risks':m['risks'],'recent_observations':m['observations'],'recent_decisions':m['decisions'],'open_loops':m['open_loops'],'capability_status':{r['capability']:r['status'] for r in package['capability_resolution']},'next_best_action':m['next_action']}
    def replan(self,package,reason,allow_simulation=True):
        m=package['mission']; m['state']='REPLANNING'; m['current_phase']='REPLANNING'; m['risks'].append(reason); m['next_action']={'action':'switch to explicit simulation' if allow_simulation else 'block and request evidence','why':'new evidence invalidated the current provider path','evidence':[],'verification':'new provider or simulation receipt required'}; return {'replanned':True,'mission':m,'mode':'SIMULATION' if allow_simulation else 'BLOCKED','pretend_success':False}

def validate_completion(package):
    m=package.get('mission',{}); v=m.get('verification',{}).get('completion_verification',{}); return {'allowed':m.get('state')=='COMPLETED' and v.get('status')=='VERIFIED' and bool(v.get('evidence_ids')),'reason':'criteria and independent verification required'}

def validate_receipt(package):
    req=package.get('execution',{}).get('request',{}); receipt=package.get('execution',{}).get('provider_bundle',{}).get('receipt',{}); return {'valid':bool(receipt) and receipt.get('request_id')==req.get('request_id') and receipt.get('scope')==req.get('scope') and receipt.get('operation')==req.get('operation') and receipt.get('side_effects') is False,'reason':'receipt must match request/provider operation/scope and prove no side effects'}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('intent'); p.add_argument('--scope',default='Themeta-verse/Nexus'); p.add_argument('--mode',default='SIMULATION',choices=['REAL_READ','SIMULATION','DRY_RUN']); a=p.parse_args(); c=MissionComposer(); print(json.dumps(c.execute(c.compose(a.intent,a.scope,a.mode)),indent=2))
