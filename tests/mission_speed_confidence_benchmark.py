import json
from pathlib import Path

ROOT = Path('/home/ubuntu/nexus')


def main():
    metadata = json.loads((ROOT / 'artifacts/nexus-auto-selection-metadata.json').read_text())
    decision = json.loads((ROOT / 'artifacts/nexus-auto-selection-decision.json').read_text())
    performance = json.loads((ROOT / 'artifacts/nexus-selective-performance-suite.json').read_text())
    metadata_bundle = metadata['execution']['provider_bundles']['repository.metadata.read']
    repo_bundle = decision['execution']['provider_bundles']['repository.read']
    assert metadata['intent_compilation']['selection_policy'] == 'AUTO_MINIMUM_SUFFICIENT_METADATA'
    assert metadata['intent_compilation']['capabilities'] == ['repository.metadata.read']
    assert metadata['execution']['external_invocations'] == 1
    assert metadata['mission']['state'] == 'COMPLETED'
    assert metadata['mission']['verification']['completion_verification']['status'] == 'VERIFIED'
    assert metadata_bundle['observation']['provenance']['depth'] == 'METADATA_ONLY'
    assert metadata_bundle['verification']['verification_state'] == 'VERIFIED'
    assert decision['intent_compilation']['selection_policy'] == 'AUTO_FULL_EVIDENCE_FOR_DECISION_OR_UNSPECIFIED_INTENT'
    assert decision['intent_compilation']['capabilities'] == ['repository.read', 'browser.read', 'filesystem.read']
    assert decision['execution']['external_invocations'] == 9
    assert decision['execution']['source_reconciliation']['status'] == 'CONFLICT'
    assert decision['mission']['verification']['completion_verification']['status'] == 'VERIFIED'
    assert repo_bundle['observation']['provenance']['calls'] == 7
    assert performance['status'] == 'passed'
    assert performance['aggregate']['confidence_preserved_on_reuse'] is True
    assert performance['aggregate']['refresh_preserved_verification'] is True
    assert performance['aggregate']['repeat_call_reduction_fraction'] > 0
    result = {
        'status': 'passed',
        'automatic_metadata_path': 'passed',
        'automatic_full_evidence_escalation': 'passed',
        'metadata_calls': metadata['execution']['external_invocations'],
        'full_decision_calls': decision['execution']['external_invocations'],
        'deep_repository_subcalls': repo_bundle['observation']['provenance']['calls'],
        'decision_conflict_preserved': decision['execution']['source_reconciliation']['status'] == 'CONFLICT',
        'verification_preserved': True,
        'remote_writes': False,
        'connector_expansion': False,
    }
    (ROOT / 'artifacts/nexus-mission-speed-confidence-benchmark.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
