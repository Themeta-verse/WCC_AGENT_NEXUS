import json
import sys
from pathlib import Path

ROOT = Path('/home/ubuntu/nexus')
sys.path.insert(0, str(ROOT / 'runtime'))

from cognitive_os import attention_view, approval_center, next_action_view, query, counterfactual, fork
from local_control import command_center

SCOPE = 'consolidated/Themeta-verse/Nexus'
STORE = '/home/ubuntu/nexus/artifacts/nexus-consolidated-store-rHt2fg'


def main():
    attention = attention_view(SCOPE, STORE)
    approvals = approval_center(SCOPE, STORE)
    next_view = next_action_view(SCOPE, STORE)
    command = command_center(SCOPE, STORE)
    assert attention['evidence_backed'] is True
    assert approvals['execution_allowed'] is False
    assert len(approvals['pending']) == 1
    assert next_view['authorization_required'] is True
    assert next_view['execution_allowed'] is False
    assert command['WHAT_REQUIRES_USER']
    assert command['WHAT_NEXUS_CAN_DO_WITHOUT_USER']
    assert 'DECISIONS_PENDING' in command and 'STALE_EVIDENCE' in command
    assert 'HAPPENED_SINCE_LAST_SESSION' in command and 'MATERIAL_DELTA' in command
    assert query('What should I do next?', SCOPE, STORE)['next_action'] == next_view['next_action']
    assert query('What requires approval?', SCOPE, STORE)['pending'] == approvals['pending']
    assert query('What matters most?', SCOPE, STORE)['evidence_backed'] is True
    isolated = command_center(SCOPE, '/tmp/does-not-exist')
    assert isolated['PROJECTS'] == [SCOPE]
    cf = counterfactual('what if the evidence changes?', SCOPE, STORE)
    fk = fork('change only the repository observation', SCOPE, STORE)
    assert cf['canonical_state_mutated'] is False
    assert fk['canonical_state_mutated'] is False
    assert cf['reality'] == 'SIMULATED' and fk['reality'] == 'SIMULATED'
    evidence = json.loads((ROOT / 'artifacts/nexus-command-center-real-query-evidence.json').read_text())
    assert evidence['query_next_matches_next'] is True
    result = {
        'status': 'passed',
        'persisted_scope': SCOPE,
        'attention_items': len(attention['items']),
        'pending_approvals': len(approvals['pending']),
        'authorization_boundary': 'execution_allowed=false',
        'natural_language_routing': 'passed',
        'command_center_state_fields': 'passed',
        'scope_isolation': 'passed',
        'counterfactual_non_mutation': 'passed',
        'remote_writes': False,
        'connector_expansion': False,
    }
    (ROOT / 'artifacts/nexus-personal-os-benchmark.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
