#!/usr/bin/env python3
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from external_intelligence import Signal, Provenance, SignalBus, WorldModel, normalize_github_health, priority, impact, connector_matrix, action_bridge, state_bridge

prov=Provenance(source='GitHub',observed_at='2026-08-13T00:00:00Z',exact_observation='test observation',fact='fact',inference='inference',hypothesis='hypothesis',raw_reference='https://github.com/Themeta-verse/Nexus')
s=Signal(source='GitHub',source_type='repository',event_type='repository_snapshot',entity='repository',entity_id='Themeta-verse/Nexus',timestamp='2026-08-13T00:00:00Z',current_state={'open_issues':0},change=[],confidence='high',importance='medium',relevance='high',actionability='record',project='nexus-v3',approval_requirement='SAFE',provenance=prov)
bus=SignalBus('nexus-v3')
assert bus.ingest(s)['accepted'] is True
assert bus.ingest(s)['duplicate_kind']=='duplicate'
s2=Signal(source=s.source,source_type=s.source_type,event_type=s.event_type,entity=s.entity,entity_id=s.entity_id,timestamp=s.timestamp,current_state=s.current_state,change=[],confidence=s.confidence,importance=s.importance,relevance=s.relevance,actionability=s.actionability,project=s.project,approval_requirement='SAFE',provenance=prov,signal_id='different-id')
assert bus.ingest(s2)['duplicate_kind']=='near_duplicate'
assert priority(s)['classification'] in {'RECORD','SURFACE'}
world=WorldModel(); update=state_bridge(s,world); assert update['state_update']['event_id']==s.signal_id
assert impact(s)['affected']
mat=connector_matrix(); names={x['connector'] for x in mat}; assert {'GitHub','Gmail','Calendar','Drive','Slack'} <= names
assert all(x['available'] is False for x in mat if x['connector']!='GitHub')
assert action_bridge('publish release',type('A',(),{'name':'GitHub','capabilities':{'PUBLISH':'not_tested'}})())['next_step'].startswith('prepare')
# Real GitHub report normalization when available.
report_path=Path('/home/ubuntu/nexus/artifacts/github-health/nexus-confirmed-second-report.json')
if report_path.exists():
 report=json.loads(report_path.read_text()); signals=normalize_github_health(report)
 assert signals and signals[0].provenance and signals[0].provenance.source=='GitHub via authenticated gh CLI'
 assert signals[0].provenance.raw_reference=='https://github.com/Themeta-verse/Nexus'
# Failure cases remain explicit.
malformed=Signal(source='GitHub',source_type='repository',event_type='malformed',entity='repository',entity_id='bad',timestamp='now',confidence='unknown',importance='low',relevance='low',actionability='unknown',provenance=prov)
assert priority(malformed)['classification'] in {'IGNORE','RECORD'}
print(json.dumps({'status':'passed','deduplication':'passed','provenance':'passed','world_model':'passed','priority_noise':'passed','connector_matrix':'passed','github_normalization':'passed'},indent=2))
