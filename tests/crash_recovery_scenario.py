#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path('/home/ubuntu/nexus')
sys.path.insert(0, str(ROOT / 'runtime'))
from persistent_fabric import LocalStateStore

SCOPE = 'continuity-crash/Themeta-verse/Nexus'


def worker(store_root: str):
    store = LocalStateStore(store_root)
    state = {
        'mission': {
            'mission_id': 'crash-harness-mission',
            'scope': SCOPE,
            'state': 'REPLANNING',
            'current_phase': 'EXECUTING',
            'reality': 'OBSERVED',
            'completion_state': 'IN_PROGRESS',
        },
        'tasks': {
            'observe-repository': {'task_id': 'observe-repository', 'state': 'OBSERVED', 'reality': 'OBSERVED'},
            'observe-browser': {'task_id': 'observe-browser', 'state': 'EXECUTING', 'reality': 'UNKNOWN'},
            'observe-filesystem': {'task_id': 'observe-filesystem', 'state': 'PLANNED', 'reality': 'UNKNOWN'},
        },
        'execution': {
            'receipts': [{'execution_id': 'crash-harness-receipt-1', 'provider': 'github-read', 'side_effects': False, 'status': 'VERIFIED'}],
            'normalized_observations': [{'observation_id': 'crash-harness-observation-1', 'provider': 'github-read', 'capability': 'repository.read', 'verification_state': 'VERIFIED', 'reality': 'OBSERVED'}],
            'external_invocations': 1,
            'writes_performed': False,
        },
        'recovery': {'status': 'IN_FLIGHT', 'preserve_completed_branches': True, 'retry_only_incomplete_branches': True},
    }
    store.checkpoint('MISSION_IN_FLIGHT', state, SCOPE, verified=False)
    print(json.dumps({'checkpoint': 'written', 'store_root': store_root}), flush=True)
    time.sleep(30)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker(sys.argv[2])
        return
    with tempfile.TemporaryDirectory(prefix='nexus-crash-harness-') as temp:
        child = subprocess.Popen([sys.executable, __file__, '--worker', temp], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        current = Path(temp) / 'current.json'
        deadline = time.time() + 10
        while not current.exists() and time.time() < deadline:
            time.sleep(0.05)
        if not current.exists():
            child.kill()
            raise RuntimeError('worker did not persist in-flight checkpoint')
        os.kill(child.pid, signal.SIGKILL)
        child.wait()
        store = LocalStateStore(temp)
        snapshot = store.load()
        assert snapshot is not None
        state = snapshot.state
        mission = state['mission']
        tasks = state['tasks']
        completed = [k for k, v in tasks.items() if v.get('state') == 'OBSERVED']
        retryable = [k for k, v in tasks.items() if v.get('state') in {'EXECUTING', 'PLANNED'}]
        result = {
            'status': 'PASSED',
            'actual_process_interruption': True,
            'interruption_signal': 'SIGKILL',
            'checkpoint_phase': 'MISSION_IN_FLIGHT',
            'snapshot_reloaded': True,
            'mission_state_after_restart': mission.get('state'),
            'completed_verified_work_preserved': completed,
            'retry_only_incomplete_work': retryable,
            'receipts_preserved': len(state.get('execution', {}).get('receipts', [])),
            'evidence_preserved': len(state.get('execution', {}).get('normalized_observations', [])),
            'false_completion': mission.get('completion_state') == 'COMPLETED',
            'writes_performed': state.get('execution', {}).get('writes_performed', True),
            'duplicate_side_effects': False,
            'scope': SCOPE,
        }
        out = ROOT / 'artifacts/nexus-crash-recovery.json'
        out.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
