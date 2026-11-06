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
        if path.startswith('issues/'):
            batch = parse_comments(batch)
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise RuntimeError('GitHub pagination exceeded 100 pages; notification stopped')


def parse_comments(rows):
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get('body', ''), str)
            or row.get('user') is not None and (not isinstance(row['user'], dict)
            or not isinstance(row['user'].get('login'), str)) for row in rows):
        raise ValueError('invalid GitHub issue comment response')
    return rows


def has_marker(comment, marker):
    return marker in comment.get('body', '') and (comment.get('user') or {}).get('login') == 'github-actions[bot]'


def comment_once(api, number, marker, message):
    path = f'issues/{number}/comments'
    if any(has_marker(comment, marker) for comment in parse_comments(api(path))):
        return
    api(path, {'body': f'{marker}\n{message}'})


def notify_merge(api, number):
    for pr in api('pulls?state=open'):
        if number in waits_on(pr.get('body')):
            comment_once(api, pr['number'], f'<!-- carr-merge:{number} -->',
                         f'Dependency #{number} merged into main. This PR can continue.')


ACTIVATION_SHA = '498dd90d80a9a7fb58323253a4f3fc5483a788aa'


def notify_deploy(api, current):
    if not re.fullmatch(r'[0-9a-f]{40}', current or ''):
        raise ValueError('Deploy comments require a verified full release SHA')
    commits = api(f'compare/{ACTIVATION_SHA}...{current}')
    shipped = {commit['sha'] for commit in commits}
    numbers = {}
    for commit in commits:
        for pr in api(f'commits/{commit["sha"]}/pulls'):
            if (pr.get('merged_at') and pr['base']['ref'] == 'main'
                    and pr.get('merge_commit_sha') in shipped):
                numbers[pr['number']] = pr['merge_commit_sha']
    for number in sorted(numbers):
        comment_once(api, number, f'<!-- carr-deploy:{numbers[number]} -->',
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
            notify_deploy(github_api, os.environ['SHA'])
    except (KeyError, ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f'::warning::PR notification failed ({type(exc).__name__}): {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
