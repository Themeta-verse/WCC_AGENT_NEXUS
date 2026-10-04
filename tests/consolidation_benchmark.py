import json
import sys
from pathlib import Path

ROOT = Path('/home/ubuntu/nexus')
sys.path.insert(0, str(ROOT / 'runtime'))

from capability_registry import discover_actual_registry
from engine_registry import default_registry
from meta_orchestrator import meta_orchestrate
from personal_agent import classify_command, compile_agent_request


def main():
    registry = discover_actual_registry()
    runtime_records = {x.name: x for x in registry.records.values() if x.id.startswith('runtime:')}
    assert {'repository.read', 'repository.metadata.read', 'browser.read', 'filesystem.read'} <= set(runtime_records)
    assert all(x.state.get('AVAILABLE') and x.state.get('CALLABLE') and x.state.get('AUTHORIZED') for x in runtime_records.values())
    assert all(x.state.get('VERIFIED') and x.state.get('PERSISTED') for x in runtime_records.values())
    selection = registry.select('collect verified project evidence')
    assert selection['selection_status'] == 'SUCCESS'
    assert selection['authorization_not_inferred_from_catalog'] is True
    names = {x['name'] for x in selection['selected']}
    assert {'repository.read', 'browser.read', 'filesystem.read'} <= names
    engine = default_registry()
    selected = engine.select('audit this project and determine what should happen next')
    assert selected['canonical_path'][0] == 'canonical-runtime'
    assert selected['invocation_performed'] is False
    assert 'omega4-reality' in selected['legacy_reference']
    meta = meta_orchestrate('audit this project')
    assert meta['consolidation']['status'] == 'COMPATIBILITY_FACADE'
    assert meta['consolidation']['no_duplicate_external_execution'] is True
    assert meta['canonical_entrypoint'].startswith('MissionComposer')
    adapted = compile_agent_request('take it further', {'project_id': 'test-scope'}, 'test-scope', 'PLAN_ONLY')
    assert classify_command('take it further') == 'TAKE_FAR'
    assert adapted['adapter_role'] == 'intent-and-specialist compatibility adapter'
    assert adapted['canonical_fabric'].startswith('MissionComposer')
    assert adapted['legacy_engines_not_invoked'] is True
    live = json.loads((ROOT / 'artifacts/nexus-action-ready-runtime.json').read_text())
    assert live['mission']['state'] == 'COMPLETED'
    assert live['execution']['writes_performed'] is False
    out = {
        'status': 'passed',
        'verified_runtime_records': sorted(runtime_records),
        'capability_selection': 'passed',
        'canonical_engine_path': selected['canonical_path'],
        'legacy_reference_is_non_invoked': True,
        'meta_orchestrator_compatibility_facade': 'passed',
        'personal_agent_adapter': 'passed',
        'real_mission_preserved': 'passed',
        'remote_writes': False,
        'connector_expansion': False,
    }
    (ROOT / 'artifacts/nexus-consolidation-benchmark.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
