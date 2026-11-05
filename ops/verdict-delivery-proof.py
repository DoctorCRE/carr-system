#!/usr/bin/env python3
"""Observe trusted verdict job receipts and the current App auto-merge actor, read-only."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.loadpy import load_module_from_path
m = load_module_from_path('proof_verdict_delivery', str(Path(__file__).with_name('verdict-delivery.py')))


def observe(armed_run, held_run, read, archive):
    receipts = []
    workflow = read('actions/workflows/verdict-delivery.yml')
    for run_id, state in [(armed_run, 'armed'), (held_run, 'held')]:
        run = read(f'actions/runs/{run_id}')
        if (run['workflow_id'] != workflow['id'] or run['conclusion'] != 'success'
                or run['repository']['full_name'] != m.REPO
                or run['head_repository']['full_name'] != m.REPO
                or run['event'] not in {'issue_comment', 'pull_request_review', 'pull_request_target', 'workflow_run', 'status'}):
            raise ValueError('Untrusted or failed verdict workflow')
        artifacts = m.collection(read(f'actions/runs/{run_id}/artifacts?per_page=100'), 'artifacts')
        wanted = f'verdict-delivery-{run_id}-{run["run_attempt"]}'
        matches = [a for a in artifacts if a['name'] == wanted and not a['expired']]
        if len(matches) != 1 or matches[0]['size_in_bytes'] > 1048576:
            raise ValueError('Missing, ambiguous or oversized verdict receipt')
        data = archive(matches[0]['id'])
        if len(data) > 1048576:
            raise ValueError('Receipt archive exceeds budget')
        with zipfile.ZipFile(io.BytesIO(data)) as zipped:
            if zipped.namelist() != ['receipt.json'] or zipped.getinfo('receipt.json').file_size > 1048576:
                raise ValueError('Unexpected receipt archive contents')
            rows = json.loads(zipped.read('receipt.json'))
        if not isinstance(rows, list) or len(rows) > 20:
            raise ValueError('Receipt fan-out exceeds budget')
        matched = [r for r in rows if r.get('schema') == 'carr-verdict-delivery.v1'
                   and r.get('repository') == m.REPO and r.get('state') == state]
        if state == 'held':
            matched = [r for r in matched if r.get('pull_request') == 2988 and r.get('reason')]
        if len(matched) != 1:
            raise ValueError('Expected one bound receipt for the requested proof')
        receipt = matched[0]
        pr = read(f'pulls/{receipt["pull_request"]}')
        if receipt['head_sha'] != pr['head']['sha']:
            raise ValueError('Proof head moved')
        if state == 'armed':
            actor = (pr.get('auto_merge') or {}).get('enabled_by', {}).get('login')
            if actor != m.APP:
                raise ValueError('Current native auto-merge actor is not the review App; observe before merge')
        elif not m.held(pr, 2988):
            raise ValueError('Hold no longer exists')
        receipts.append(dict(receipt, run_id=run_id, run_url=run['html_url']))
    return {'state': 'verified', 'receipts': receipts}


def archive(artifact_id):
    result = subprocess.run(['gh', 'api', f'repos/{m.REPO}/actions/artifacts/{artifact_id}/zip'],
                            capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Artifact read failed')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--armed-run', required=True, type=int)
    parser.add_argument('--held-run', required=True, type=int)
    args = parser.parse_args()
    try:
        if min(args.armed_run, args.held_run) < 1:
            raise ValueError('Positive run IDs required')
        print(json.dumps(observe(args.armed_run, args.held_run, m.github_read, archive), sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        print(json.dumps({'state': 'unproven', 'reason': type(error).__name__}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
