import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path('/home/ubuntu/nexus')
sys.path.insert(0, str(ROOT / 'runtime'))

from action_ready import evidence_gate


def main():
    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(seconds=30)).isoformat()
    stale = (now - timedelta(days=8)).isoformat()
    base = {
        'observation_id': 'obs-fresh',
        'provider': 'filesystem-read',
        'capability': 'filesystem.read',
        'scope': 'selective/Nexus',
        'timestamp': fresh,
        'reality': 'OBSERVED',
        'verification_state': 'VERIFIED',
    }
    no_prior = evidence_gate(capability='filesystem.read', provider='filesystem-read', requested_scope='selective/Nexus', prior_observations=[], uncertainty=[])
    reusable = evidence_gate(capability='filesystem.read', provider='filesystem-read', requested_scope='selective/Nexus', prior_observations=[base], uncertainty=[], decision_sensitivity='MEDIUM')
    high_uncertainty = evidence_gate(capability='filesystem.read', provider='filesystem-read', requested_scope='selective/Nexus', prior_observations=[base], uncertainty=['material change'], decision_sensitivity='HIGH')
    conflicting = evidence_gate(capability='filesystem.read', provider='filesystem-read', requested_scope='selective/Nexus', prior_observations=[base], uncertainty=['source conflict'], decision_sensitivity='MEDIUM', source_conflict=True)
    stale_gate = evidence_gate(capability='filesystem.read', provider='filesystem-read', requested_scope='selective/Nexus', prior_observations=[{**base, 'timestamp': stale}], uncertainty=[])
    mismatch = evidence_gate(capability='filesystem.read', provider='filesystem-read', requested_scope='other/Nexus', prior_observations=[base], uncertainty=[])
    assert no_prior['decision'] == 'CALL' and no_prior['external_call_required']
    assert reusable['decision'] == 'REUSE' and not reusable['external_call_required']
    assert high_uncertainty['decision'] == 'REFRESH'
    assert conflicting['decision'] == 'REFRESH'
    assert stale_gate['decision'] == 'REFRESH'
    assert mismatch['decision'] == 'CALL'
    first = json.loads((ROOT / 'artifacts/nexus-selective-reuse-first-fixed.json').read_text())
    second = json.loads((ROOT / 'artifacts/nexus-selective-reuse-second-fixed.json').read_text())
    assert first['execution']['external_invocations'] == 1
    assert second['execution']['external_invocations'] == 0
    assert second['execution']['selective_acquisition']['external_calls_saved'] == 1
    assert second['execution']['selective_acquisition']['gates']['filesystem.read']['decision'] == 'REUSE'
    assert second['mission']['state'] == 'COMPLETED'
    assert second['mission']['verification']['completion_verification']['status'] == 'VERIFIED'
    assert second['execution']['writes_performed'] is False
    result = {
        'status': 'passed',
        'gate_policy': {'no_prior': 'CALL', 'fresh_verified_low_uncertainty': 'REUSE', 'high_uncertainty': 'REFRESH', 'source_conflict': 'REFRESH', 'stale': 'REFRESH', 'scope_mismatch': 'CALL'},
        'first_external_calls': first['execution']['external_invocations'],
        'second_external_calls': second['execution']['external_invocations'],
        'external_calls_saved': second['execution']['selective_acquisition']['external_calls_saved'],
        'reuse_preserved_completion': True,
        'reuse_preserved_verification': True,
        'remote_writes': False,
        'connector_expansion': False,
    }
    (ROOT / 'artifacts/nexus-selective-acquisition-benchmark.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
