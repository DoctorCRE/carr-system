#!/usr/bin/env python3
"""Dispatch an allowlisted, verified workflow through the remote-CI meter."""
from __future__ import annotations

import argparse
import json
import os
import re
from urllib.parse import quote
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.local_jobs import locked
from lib.platform_metering import MeteringRefusal, authorize_metered_execution

REPOSITORY = 'DoctorCRE/carr-system'
RECOVERY_EVENTS = {'push', 'merge_group', 'schedule', 'workflow_dispatch'}
WORKFLOWS = {
    (REPOSITORY, 'main-canary.yml'): {
        'ref_pattern': r'main', 'prerequisite': 'ci.yml', 'evidence_events': RECOVERY_EVENTS,
    },
    (REPOSITORY, 'deploy.yml'): {
        'ref_pattern': r'main', 'prerequisite': 'main-canary.yml', 'evidence_events': RECOVERY_EVENTS,
    },
    ('jbookout/doctorcre-app', 'home-lcp-diagnostic.yml'): {
        'ref_pattern': r'wr622-s1-j336-lcp-evidence', 'prerequisite': 'ci.yml',
        'evidence_events': {'pull_request', 'push', 'workflow_dispatch'},
        'duplicate_events': {'workflow_dispatch'},
    },
}


def github(*args):
    result = subprocess.run(['gh', 'api', *args], check=True, text=True,
                            capture_output=True, timeout=45)
    return json.loads(result.stdout)


def dispatch(workflow, ref, dry_run, *, ledger, policy, api=github, repository=REPOSITORY):
    rule = WORKFLOWS.get((repository, workflow))
    if rule is None:
        raise MeteringRefusal('repository/workflow is not allowlisted')
    if not re.fullmatch(rule['ref_pattern'], ref):
        raise MeteringRefusal('ref is not allowlisted for this repository/workflow')
    base = f'repos/{repository}'
    commit_path = f'{base}/commits/{quote(ref, safe="")}'
    sha = api(commit_path)['sha']
    prerequisite = rule['prerequisite']
    runs = api(f'{base}/actions/workflows/{prerequisite}/runs?head_sha={sha}&status=success&per_page=100')['workflow_runs']
    evidence = next((run for run in runs if run['head_sha'] == sha
                     and run['conclusion'] == 'success'
                     and run['event'] in rule['evidence_events']
                     and run['head_repository']['full_name'] == repository), None)
    if evidence is None:
        raise MeteringRefusal(f'{prerequisite} must have succeeded for exact commit {sha}')
    # Hosted CI executes the local check classes against this exact candidate.
    admission = authorize_metered_execution(policy, 'github-actions-remote-ci', {
        'candidate_sha': sha, 'local_checks_green': True, 'evidence_url': evidence['html_url'],
    })
    existing = api(f'{base}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100')['workflow_runs']
    if any(run['head_sha'] == sha
           and ('duplicate_events' not in rule or run.get('event') in rule['duplicate_events'])
           and run.get('conclusion') not in {'failure', 'cancelled', 'skipped', 'timed_out', 'action_required', 'stale'}
           for run in existing):
        raise MeteringRefusal('pipeline already running or successful for this commit; inspect the existing run')
    cap = policy['global_policy']['cloud_full_verification']['max_per_publication_candidate']
    key = f'{repository}/{workflow}/{sha}'
    receipt = {'key': key, 'repository': repository, 'workflow': workflow, 'ref': ref, 'candidate_sha': sha,
               'admission': admission, 'evidence_url': evidence['html_url'],
               'reserved_remote_runs': 1, 'actual_minutes': None,
               'recorded_at': datetime.now(timezone.utc).isoformat()}
    with locked(ledger) as handle:
        handle.seek(0)
        spent = sum(row['reserved_remote_runs'] for line in handle
                    if (row := json.loads(line))['key'] == key and row['status'] == 'reserved')
        if spent >= cap:
            raise MeteringRefusal(f'remote-CI budget exhausted for {workflow} at {sha}; diagnose the failure or use a new commit before retrying')
        if dry_run:
            return {**receipt, 'status': 'dry_run', 'remaining_remote_runs': cap - spent}

        def record(status, **fields):
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps({**receipt, 'status': status, **fields}) + '\n')
            handle.flush()
            os.fsync(handle.fileno())

        record('reserved')
        try:
            if api(commit_path)['sha'] != sha:
                raise MeteringRefusal('ref changed during admission; reservation retained, no dispatch sent')
            response = api(f'{base}/actions/workflows/{workflow}/dispatches',
                           '--method', 'POST', '-H', 'X-GitHub-Api-Version: 2026-03-10',
                           '-f', f'ref={ref}')
            url = response.get('html_url') if isinstance(response, dict) else None
            if not isinstance(url, str) or not url.startswith(f'https://github.com/{repository}/actions/runs/'):
                raise ValueError('dispatch response did not contain a repository run URL')
            record('dispatched', run_url=url)
            return {**receipt, 'status': 'dispatched', 'run_url': url}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, MeteringRefusal) as exc:
            record('dispatch_unconfirmed')
            raise DispatchUnconfirmed(
                f'dispatch outcome unknown; check {workflow} runs for {sha}; reservation retained: {exc}'
            ) from exc


class DispatchUnconfirmed(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dispatch'])
    parser.add_argument('workflow')
    parser.add_argument('--repo', default=REPOSITORY)
    parser.add_argument('--ref', default='main')
    parser.add_argument('--dry-run', action='store_true', help='check admission without dispatching or reserving spend')
    args = parser.parse_args()
    policy = json.loads((ROOT / 'ops/config/platform-metering.v1.json').read_text())
    ledger = Path.home() / '.local/state/carr/ci-dispatch.jsonl'
    try:
        receipt = dispatch(args.workflow, args.ref, args.dry_run, ledger=ledger, policy=policy, repository=args.repo)
    except DispatchUnconfirmed as exc:
        print(str(exc), file=sys.stderr)
        return 77
    except (MeteringRefusal, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'workflow recovery refused: {exc}', file=sys.stderr)
        return 77
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
