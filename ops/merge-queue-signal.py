#!/usr/bin/env python3
"""Expose merge-group CI results on the queued PR's current head."""
import json
import os
from pathlib import Path
import re
import urllib.request


def publish(event, state, run_url, api):
    group = event.get('merge_group')
    if not group:
        return
    match = re.fullmatch(r'refs/heads/gh-readonly-queue/main/pr-(\d+)-[0-9a-f]{40}', group['head_ref'])
    if not match:
        raise ValueError('unrecognized merge queue ref')
    head = api(f'pulls/{match[1]}')['head']['sha']
    comparison = api(f'compare/{head}...{group["head_sha"]}')
    if comparison['status'] not in ('ahead', 'identical'):
        print('Queue signal skipped: PR head advanced beyond tested group')
        return
    api(f'statuses/{head}', {
        'state': state, 'context': 'merge-queue/CI', 'target_url': run_url,
        'description': ('Merge queue CI passed' if state == 'success' else
                        'Merge queue CI failed; inspect run before re-arming'),
    })
    print(f'Merge queue CI {state} exposed on PR {match[1]} at {head}')


def main():
    repository = os.environ['GITHUB_REPOSITORY']
    def api(path, payload=None):
        request = urllib.request.Request(
            f'https://api.github.com/repos/{repository}/{path}',
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                     'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    publish(json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()),
            os.environ['CI_QUEUE_RESULT'],
            f'https://github.com/{repository}/actions/runs/{os.environ["GITHUB_RUN_ID"]}', api)


if __name__ == '__main__':
    main()
