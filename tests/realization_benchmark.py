from pathlib import Path
import json
import sys
import subprocess

ROOT = Path('/home/ubuntu/nexus')
RUNTIME = str(ROOT / 'runtime')


def main():
    sys.path.insert(0, RUNTIME)
    from capability_registry import discover_actual_registry

    graph = discover_actual_registry().graph()
    nodes = {node['name']: node for node in graph['nodes'] if node['id'].startswith('runtime:')}
    expected = {'repository.read', 'repository.metadata.read', 'browser.read', 'filesystem.read'}
    assert expected.issubset(nodes)
    for name in expected:
        node = nodes[name]
        assert node['availability'] == 'AVAILABLE'
        assert node['authorization'] in {'CONFIRMED_READ_ONLY', 'CONFIRMED_BROWSER_READ', 'CONFIRMED_LOCAL_READ'}
        assert node['state']['CALLABLE'] is True
        assert node['state']['AUTHORIZED'] is True
        assert node['state']['VERIFIED'] is True
        assert node['action_level'] == 1
        assert node['approval_required'] is False
        assert node['execution_allowed'] is True
        assert node['governance_boundary'] == 'SCOPED_READ_ONLY'
        assert 'no write or deployment authority' in node['limitations']
    assert 'metadata-only; does not prove deep repository health' in nodes['repository.metadata.read']['limitations']
    assert 'full health depth uses seven GitHub API reads' in nodes['repository.read']['limitations']
    result = subprocess.run([str(ROOT / 'nexus'), 'capabilities'], capture_output=True, text=True, check=True)
    cli = json.loads(result.stdout)
    cli_nodes = {node['name'] for node in cli['operation_graph']['nodes'] if node['id'].startswith('runtime:')}
    assert expected.issubset(cli_nodes)
    assert cli['capability_ceiling']['repository.write']['authorized'] is False
    assert cli['operation_graph']['fail_closed'] is True
    summary = {
        'status': 'passed',
        'operation_nodes': sorted(expected),
        'operation_level_states': 'passed',
        'metadata_full_health_distinction': 'passed',
        'read_only_boundary': 'passed',
        'action_level_and_fail_closed_governance': 'passed',
        'cli_exposure': 'passed',
        'remote_writes': False,
        'connector_expansion': False,
    }
    (ROOT / 'artifacts/nexus-realization-benchmark.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
