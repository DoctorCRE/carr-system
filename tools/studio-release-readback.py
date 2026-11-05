#!/usr/bin/env python3
"""Read installed Studio identity while its Worker delivery parent is in progress."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.studio_runtime_auto_refresh import Host, released_receipt
from lib.worker_release_receipt import github, REPOSITORY


def waiting_parent(worker, parent):
    if parent.get('id') != worker['run_id'] or parent.get('run_attempt') != worker['run_attempt']:
        raise ValueError('parent attempt mismatch')
    if parent.get('status') != 'in_progress':
        raise ValueError('delivery parent is not still in progress')


def observe(source, host, worker, parent):
    waiting_parent(worker, parent)
    sha = host.installed()
    if sha != worker['source_sha']:
        raise ValueError('installed runtime differs from Worker receipt')
    host.verify_current(sha)
    path = source / 'out/studio-runtime-auto-refresh/runs.jsonl'
    with path.open('rb') as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 1048576))
        if size > 1048576:
            handle.readline()
        rows = handle.read().decode().splitlines()
    for line in reversed(rows[-100:]):
        row = json.loads(line)
        if (row.get('status') == 'refreshed' and row.get('sha') == sha
                and row.get('installed_sha') == sha and row.get('worker_receipt') == worker):
            return {'installed_sha': sha, 'worker_receipt': worker,
                    'installation_started_at': row['installation_started_at'],
                    'installation_completed_at': row['installation_completed_at'],
                    'parent_status': parent['status'],
                    'canonical_proof': False}
    raise ValueError('matching diagnostic installation report unavailable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    worker = released_receipt()
    path = f'repos/{REPOSITORY}/actions/runs/{worker["run_id"]}'
    parent = github(path)
    result = observe(args.source, Host(args.source), worker, parent)
    waiting_parent(worker, github(path))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
