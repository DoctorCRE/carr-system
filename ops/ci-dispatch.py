#!/usr/bin/env python3
"""Restart a verified main pipeline once through the remote-CI meter."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.local_jobs import locked
from lib.platform_metering import MeteringRefusal, authorize_metered_execution

REPOSITORY = 'DoctorCRE/carr-system'
PREREQUISITES = {'main-canary.yml': 'ci.yml', 'deploy.yml': 'main-canary.yml'}


def github(*args):
    result = subprocess.run(['gh', 'api', *args], check=True, text=True,
                            capture_output=True, timeout=45)
    return json.loads(result.stdout)


def dispatch(workflow, ref, dry_run, *, ledger, policy, api=github):
    if workflow not in PREREQUISITES:
        raise MeteringRefusal('workflow is not allowlisted; use main-canary.yml or deploy.yml')
    if ref != 'main':
        raise MeteringRefusal('workflow recovery permits only --ref main')
    base = f'repos/{REPOSITORY}'
    sha = api(f'{base}/commits/main')['sha']
    prerequisite = PREREQUISITES[workflow]
    runs = api(f'{base}/actions/workflows/{prerequisite}/runs?head_sha={sha}&status=success&per_page=100')['workflow_runs']
    evidence = next((run for run in runs if run['head_sha'] == sha
                     and run['conclusion'] == 'success'
                     and run['event'] in {'push', 'merge_group', 'schedule', 'workflow_dispatch'}
                     and run['head_repository']['full_name'] == REPOSITORY), None)
    if evidence is None:
        raise MeteringRefusal(f'{prerequisite} must have succeeded for exact main commit {sha}')
    # Hosted CI executes the local check classes against this exact candidate.
    admission = authorize_metered_execution(policy, 'github-actions-remote-ci', {
        'candidate_sha': sha, 'local_checks_green': True,
    })
    existing = api(f'{base}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100')['workflow_runs']
    if any(run['head_sha'] == sha and run.get('conclusion') not in {'failure', 'cancelled', 'skipped', 'timed_out', 'action_required', 'stale'}
           for run in existing):
        raise MeteringRefusal('pipeline already running or successful for this main commit; inspect the existing run')
    cap = policy['global_policy']['cloud_full_verification']['max_per_publication_candidate']
    key = f'{REPOSITORY}/{workflow}/{sha}'
    receipt = {'key': key, 'workflow': workflow, 'ref': ref, 'candidate_sha': sha,
               'admission': admission, 'evidence_url': evidence['html_url'],
               'reserved_remote_runs': 1, 'actual_minutes': None,
               'recorded_at': datetime.now(timezone.utc).isoformat()}
    with locked(ledger) as handle:
        handle.seek(0)
        spent = sum(row['reserved_remote_runs'] for line in handle
                    if (row := json.loads(line))['key'] == key and row['status'] == 'reserved')
        if spent >= cap:
            raise MeteringRefusal(f'remote-CI budget exhausted for {workflow} at {sha}; inspect the existing run')
        if dry_run:
            return {**receipt, 'status': 'dry_run', 'remaining_remote_runs': cap - spent}

        def record(status, **fields):
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps({**receipt, 'status': status, **fields}) + '\n')
            handle.flush()
            os.fsync(handle.fileno())

        record('reserved')
        try:
            if api(f'{base}/commits/main')['sha'] != sha:
                raise MeteringRefusal('main changed during admission; reservation retained, no dispatch sent')
            response = api(f'{base}/actions/workflows/{workflow}/dispatches',
                           '--method', 'POST', '-H', 'X-GitHub-Api-Version: 2026-03-10',
                           '-f', 'ref=main')
            url = response['html_url']
            if not url.startswith(f'https://github.com/{REPOSITORY}/actions/runs/'):
                raise ValueError('dispatch response did not contain a repository run URL')
            record('dispatched', run_url=url)
            return {**receipt, 'status': 'dispatched', 'run_url': url}
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, MeteringRefusal):
            record('dispatch_unconfirmed')
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['dispatch'])
    parser.add_argument('workflow')
    parser.add_argument('--ref', default='main')
    parser.add_argument('--dry-run', action='store_true', help='check admission without dispatching or reserving spend')
    args = parser.parse_args()
    policy = json.loads((ROOT / 'ops/config/platform-metering.v1.json').read_text())
    ledger = Path.home() / '.local/state/carr/ci-dispatch.jsonl'
    try:
        receipt = dispatch(args.workflow, args.ref, args.dry_run, ledger=ledger, policy=policy)
    except (MeteringRefusal, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'workflow recovery refused: {exc}', file=sys.stderr)
        return 77
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
