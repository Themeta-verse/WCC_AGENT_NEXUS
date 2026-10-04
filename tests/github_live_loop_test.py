#!/usr/bin/env python3
"""Read-only GitHub live-loop test (integration).

Requires the authenticated `gh` CLI AND network access to the GitHub API.
It is dependency-gated: when `gh` is absent the whole module is SKIPPED
(instead of failing collection with FileNotFoundError), so the normal suite
stays runnable without the external CLI. When `gh` IS present the live
assertions run unchanged.

This is an integration test, not a unit test: it performs real reads of
Themeta-verse/Nexus. It must never be silently deleted; the skip reason is
reported explicitly by pytest.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("gh") is None,
    reason="gh CLI not installed: github live-loop is an integration test requiring the authenticated gh CLI",
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import github_live_loop as g
from beyond_engine import recovery
from adaptive_engine import outcome_learning
from godtier_governance import classify_action


def test_live_authenticated_read():
    # Real authenticated read test; this repository was discovered from the live account and is treated provisionally.
    real = g.run('Themeta-verse/Nexus')
    assert real['verification']['github_read_succeeded'] is True
    assert real['verification']['writes_performed'] is False
    assert real['snapshot']['repository'] == 'Themeta-verse/Nexus'
    assert real['comparison']['status'] in {'baseline', 'compared'}
    assert real['health']['evidence_boundary']


def test_missing_repository_safe_failure():
    # Missing repository: safe failure, no invented state.
    original = g.gh_api

    def missing(path, params=None):
        raise RuntimeError('GitHub request failed for repos/missing/repo: HTTP 404 Not Found')
    g.gh_api = missing
    try:
        try:
            g.run('missing/repo')
        except RuntimeError as e:
            assert '404' in str(e)
    finally:
        g.gh_api = original


def test_partial_local_recovery_explicit():
    # Partial local recovery is explicit.
    r = recovery('RESEARCHING', ['snapshot', 'analysis'], ['recommendation'], ['user confirmation'])
    assert r['next_action'] == 'recommendation'


def test_learning_explicit_not_silent():
    # Learning is explicit, not silent governance mutation.
    lesson = outcome_learning({'expected_result': 'no open work', 'decision': 'baseline'}, 'no open work')
    assert lesson['alignment'] is True


def test_consequential_writes_gated():
    # Consequential writes remain gated.
    assert classify_action('merge')['approval_required'] is True
    assert classify_action('read')['approval_required'] is False
