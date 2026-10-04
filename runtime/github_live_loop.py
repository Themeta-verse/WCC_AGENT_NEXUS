#!/usr/bin/env python3
"""Read-only GitHub → NEXUS project-health loop.

Uses the authenticated `gh` CLI; never writes to GitHub. It stores local snapshots
and reports so change detection and recovery are explicit and verifiable.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / 'context' / 'github-snapshots'
REPORT_DIR = ROOT / 'artifacts' / 'github-health'
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def gh_api(path: str, params: list[str] | None = None) -> Any:
    cmd = ['gh', 'api', '-X', 'GET', path]
    if params:
        cmd += params
    env = os.environ.copy()
    env.update({'NO_COLOR': '1', 'GH_FORCE_TTY': '0', 'CLICOLOR': '0'})
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if p.returncode:
        raise RuntimeError(f'GitHub request failed for {path}: {p.stderr.strip() or p.stdout.strip()}')
    raw = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', p.stdout).strip()
    if not raw:
        return []
    return json.loads(raw)


def repo_snapshot(repo: str) -> dict:
    meta = gh_api(f'repos/{repo}')
    branch = (meta.get('default_branch') or 'main')
    commits = gh_api(f'repos/{repo}/commits', ['-f', 'per_page=10'])
    issues_raw = gh_api(f'repos/{repo}/issues', ['-f', 'state=open', '-f', 'per_page=100'])
    pulls = gh_api(f'repos/{repo}/pulls', ['-f', 'state=all', '-f', 'per_page=30'])
    issues = [x for x in issues_raw if 'pull_request' not in x]
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        'snapshot_version': 1,
        'observed_at': now,
        'source': 'GitHub via authenticated gh CLI',
        'repository': repo,
        'default_branch': branch,
        'facts': {
            'full_name': meta.get('full_name'),
            'private': meta.get('private'),
            'archived': meta.get('archived'),
            'description': meta.get('description'),
            'updated_at': meta.get('updated_at'),
            'pushed_at': meta.get('pushed_at'),
            'open_issues_count_api': meta.get('open_issues_count'),
            'stars': meta.get('stargazers_count'),
            'forks': meta.get('forks_count'),
            'default_branch': branch,
        },
        'commits': [
            {'sha': c.get('sha'), 'message': (c.get('commit') or {}).get('message','').splitlines()[0], 'author': ((c.get('author') or {}).get('login') or ((c.get('commit') or {}).get('author') or {}).get('name')), 'date': ((c.get('commit') or {}).get('author') or {}).get('date')}
            for c in commits
        ],
        'issues': [
            {'number': x.get('number'), 'title': x.get('title'), 'labels': [l.get('name') for l in x.get('labels',[])], 'updated_at': x.get('updated_at'), 'created_at': x.get('created_at'), 'url': x.get('html_url')}
            for x in issues
        ],
        'pull_requests': [
            {'number': x.get('number'), 'title': x.get('title'), 'state': x.get('state'), 'draft': x.get('draft'), 'updated_at': x.get('updated_at'), 'created_at': x.get('created_at'), 'merged_at': x.get('merged_at'), 'url': x.get('html_url')}
            for x in pulls
        ],
        'counts': {
            'open_issues': len(issues),
            'open_pull_requests': sum(1 for x in pulls if x.get('state') == 'open'),
            'recent_commits_returned': len(commits),
            'merged_pull_requests_returned': sum(1 for x in pulls if x.get('merged_at')),
        },
    }


def compare(previous: dict | None, current: dict) -> dict:
    if not previous:
        return {'status': 'baseline', 'meaningful': False, 'changes': [], 'explanation': 'No previous local snapshot exists; current data is a baseline, not a trend.'}
    changes=[]
    pf, cf = previous.get('facts',{}), current.get('facts',{})
    for key in ('updated_at','pushed_at','archived','default_branch','description'):
        if pf.get(key) != cf.get(key): changes.append({'field': key, 'previous': pf.get(key), 'current': cf.get(key), 'meaningful': key in {'updated_at','pushed_at','archived','default_branch'}})
    for collection in ('commits','issues','pull_requests'):
        old={str(x.get('sha',x.get('number'))): x for x in previous.get(collection,[])}
        new={str(x.get('sha',x.get('number'))): x for x in current.get(collection,[])}
        added=[new[k] for k in new.keys()-old.keys()]
        removed=[old[k] for k in old.keys()-new.keys()]
        if added or removed: changes.append({'collection': collection, 'added': added, 'removed': removed, 'meaningful': True})
    return {'status': 'compared', 'meaningful': bool(changes), 'changes': changes, 'explanation': 'Only observed API differences are included; no progress is inferred from activity alone.'}


def health(snapshot: dict, comparison: dict) -> dict:
    c=snapshot.get('counts',{}); f=snapshot.get('facts',{})
    risks=[]; blockers=[]; observations=[]
    if f.get('archived'): risks.append({'type':'archived_repository','classification':'FACT','evidence':'GitHub archived=true'})
    if c.get('open_issues',0)>0: observations.append({'type':'open_issues','classification':'FACT','count':c['open_issues']})
    if c.get('open_pull_requests',0)>0: observations.append({'type':'open_pull_requests','classification':'FACT','count':c['open_pull_requests']})
    stale_issue_titles=[]
    now=dt.datetime.now(dt.timezone.utc)
    for issue in snapshot.get('issues',[]):
        try:
            age=(now-dt.datetime.fromisoformat(issue['updated_at'].replace('Z','+00:00'))).days
            if age>=30: stale_issue_titles.append({'number':issue['number'],'title':issue['title'],'age_days':age})
        except Exception: pass
    if stale_issue_titles:
        blockers.append({'type':'possible_stale_open_work','classification':'INFERENCE','evidence':stale_issue_titles,'uncertainty':'issue may be intentionally parked'})
    if comparison.get('meaningful'): observations.append({'type':'meaningful_change','classification':'FACT','changes':comparison['changes']})
    return {
        'current_state': {'repository': snapshot['repository'], 'default_branch': snapshot['default_branch'], 'archived': f.get('archived'), 'last_observed': snapshot['observed_at']},
        'what_changed': comparison,
        'what_matters': observations,
        'possible_risks': risks,
        'possible_blockers': blockers,
        'recommended_next_action': 'Review the highest-impact open or stale work with repository context' if (c.get('open_issues',0) or c.get('open_pull_requests',0)) else 'No repository action inferred from the available snapshot; establish a baseline and check again when needed',
        'causal_reasoning': {'observation':'Repository activity and open work are observable facts', 'hypotheses':'Progress, health, and root cause are not proven by counts alone', 'cheapest_test':'Inspect the specific issue/PR or compare a later snapshot'},
        'actionability': {'surface': bool(comparison.get('meaningful') or risks or blockers), 'what_changed': comparison, 'why_it_matters': 'Only if linked to an active NEXUS project objective', 'what_nexus_can_handle': ['summarize','compare snapshots','identify stale/open loops','draft next actions','update local project context'], 'approval_required_for': ['merge','close issue','publish release','change permissions','destructive or production actions']},
        'evidence_boundary': 'Facts come from GitHub API response; inferences are labeled; absent fields remain unknown.'
    }


def load_previous(repo: str) -> dict | None:
    p=SNAPSHOT_DIR/(repo.replace('/','__')+'.json')
    return json.loads(p.read_text()) if p.exists() else None


def save_snapshot(repo: str, snap: dict):
    (SNAPSHOT_DIR/(repo.replace('/','__')+'.json')).write_text(json.dumps(snap,indent=2))


def run(repo: str) -> dict:
    previous=load_previous(repo)
    current=repo_snapshot(repo)
    comparison=compare(previous,current)
    analysis=health(current,comparison)
    save_snapshot(repo,current)
    report={'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),'repository':repo,'snapshot':current,'comparison':comparison,'health':analysis,'verification':{'github_read_succeeded':True,'source':'gh api','writes_performed':False}}
    (REPORT_DIR/(repo.replace('/','__')+'.json')).write_text(json.dumps(report,indent=2))
    return report


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    x=sub.add_parser('run'); x.add_argument('repo')
    x=sub.add_parser('snapshot'); x.add_argument('repo')
    x=sub.add_parser('compare'); x.add_argument('previous'); x.add_argument('current')
    x=sub.add_parser('health'); x.add_argument('snapshot'); x.add_argument('--comparison',default='{}')
    a=p.parse_args()
    try:
        if a.cmd=='run': out=run(a.repo)
        elif a.cmd=='snapshot': out=repo_snapshot(a.repo)
        elif a.cmd=='compare': out=compare(json.loads(Path(a.previous).read_text()),json.loads(Path(a.current).read_text()))
        else: out=health(json.loads(Path(a.snapshot).read_text()),json.loads(a.comparison))
        print(json.dumps(out,indent=2))
    except Exception as e:
        print(json.dumps({'status':'failed_safely','error_type':type(e).__name__,'error':str(e),'writes_performed':False,'verification':{'github_read_succeeded':False,'writes_performed':False},'fallback':'preserve partial local state and request a valid repository or permission'},indent=2))
        raise SystemExit(2)

if __name__=='__main__': main()
