#!/usr/bin/env python3
"""Post idempotent PR comments from GitHub merge and verified-deploy events."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def waits_on(body):
    numbers = set()
    for line in (body or '').splitlines():
        if re.fullmatch(r'Waits-on: *#[1-9][0-9]*(?:[ ,]+#[1-9][0-9]*)* *', line):
            numbers.update(map(int, re.findall(r'#([0-9]+)', line)))
    return numbers


def github_api(path, body=None):
    command = ['gh', 'api', f'repos/{os.environ["GITHUB_REPOSITORY"]}/{path}']
    if body is not None:
        result = subprocess.run(command + ['--method', 'POST', '--input', '-'],
                                input=json.dumps(body), text=True, capture_output=True, timeout=30)
        result.check_returncode()
        return json.loads(result.stdout)
    rows = []
    for page in range(1, 101):
        separator = '&' if '?' in path else '?'
        result = subprocess.run(command[:-1] + [command[-1] + f'{separator}per_page=100&page={page}'],
                                text=True, capture_output=True, timeout=30)
        result.check_returncode()
        data = json.loads(result.stdout)
        batch = data['commits'] if path.startswith('compare/') else data
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise RuntimeError('GitHub pagination exceeded 100 pages; notification stopped')


def comment_once(api, number, marker, message):
    path = f'issues/{number}/comments'
    if any(marker in (comment.get('body') or '')
           and comment.get('user', {}).get('login') == 'github-actions[bot]'
           for comment in api(path)):
        return
    api(path, {'body': f'{marker}\n{message}'})


def notify_merge(api, number):
    for pr in api('pulls?state=open'):
        if number in waits_on(pr.get('body')):
            comment_once(api, pr['number'], f'<!-- carr-merge:{number} -->',
                         f'Dependency #{number} merged into main. This PR can continue.')


def notify_deploy(api, previous, current):
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha or '') for sha in (previous, current)):
        raise ValueError('Deploy comments require previous and verified full release SHAs')
    if previous == current:
        return
    commits = api(f'compare/{previous}...{current}')
    shipped = {commit['sha'] for commit in commits}
    numbers = set()
    for commit in commits:
        for pr in api(f'commits/{commit["sha"]}/pulls'):
            if (pr.get('merged_at') and pr['base']['ref'] == 'main'
                    and pr.get('merge_commit_sha') in shipped):
                numbers.add(pr['number'])
    for number in sorted(numbers):
        comment_once(api, number, f'<!-- carr-deploy:{current} -->',
                     f'Production deployment verified. This PR shipped in release `{current}`.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['merge', 'deploy'])
    args = parser.parse_args()
    try:
        if args.mode == 'merge':
            event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
            pr = event['pull_request']
            if pr['merged'] and pr['base']['ref'] == 'main':
                notify_merge(github_api, pr['number'])
        else:
            notify_deploy(github_api, os.environ['PREVIOUS_SHA'], os.environ['SHA'])
    except (KeyError, ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'::warning::PR notification failed ({type(exc).__name__}): {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
