#!/usr/bin/env python3
"""Reconcile trusted held branches after a merged main PR without rewriting history."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

REPOSITORY = 'DoctorCRE/carr-system'


def command(*argv, cwd=None):
    return subprocess.check_output(argv, cwd=cwd, text=True, stderr=subprocess.PIPE, timeout=45).strip()


def manifest(value):
    if set(value) != {'schema', 'branches'} or value['schema'] != 'held-branches.v1':
        raise ValueError('invalid held branch manifest')
    entries = value['branches']
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError('held branch cap exceeded')
    seen = set()
    for row in entries:
        if set(row) != {'branch', 'card', 'holders', 'title', 'body'}:
            raise ValueError('invalid held branch entry')
        branch = row['branch']
        if (not isinstance(branch, str) or branch == 'main' or branch in seen or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', branch)):
            raise ValueError('invalid or duplicate held branch')
        command('git', 'check-ref-format', '--branch', branch)
        seen.add(branch)
        if not re.fullmatch(r'WR[1-9][0-9]*\.S[1-9][0-9]*\.J[1-9][0-9]*', row['card']):
            raise ValueError('invalid build Card')
        holders = row['holders']
        if (not isinstance(holders, list) or not 1 <= len(holders) <= 20 or
                any(type(n) is not int or n < 1 for n in holders) or len(set(holders)) != len(holders)):
            raise ValueError('invalid holder PRs')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ('title', 'body')):
            raise ValueError('PR title and body required')
        if len(row['title']) > 256 or len(row['body']) > 65000 or row['body'].splitlines()[0] != 'Card: ' + row['card']:
            raise ValueError('PR body must start with build Card')
    return entries


class Github:
    def __init__(self, root):
        self.root = root
        self.holders = {}

    def api(self, endpoint, *args):
        return json.loads(command('gh', 'api', f'repos/{REPOSITORY}/{endpoint}', *args))

    def holder_merged(self, number):
        if number not in self.holders:
            pr = self.api(f'pulls/{number}')
            self.holders[number] = pr['merged'] is True and pr['base']['ref'] == 'main'
        return self.holders[number]

    def pull_requests(self, branch=None):
        endpoint = 'pulls?state=' + ('all&head=DoctorCRE:' + branch if branch else 'open') + '&per_page=100'
        rows = self.api(endpoint)
        if not isinstance(rows, list) or len(rows) >= 100:
            raise ValueError('PR listing cap reached; refusing partial admission')
        return rows

    def files(self, number):
        pages = json.loads(command('gh', 'api', '--paginate', '--slurp',
                                   f'repos/{REPOSITORY}/pulls/{number}/files?per_page=100'))
        rows = [row for page in pages for row in page]
        if len(rows) >= 3000:
            raise ValueError('PR files cap reached; refusing partial admission')
        return {path for row in rows for path in (row['filename'], row.get('previous_filename')) if path}

    def overlap(self, paths, branch):
        return [pr['number'] for pr in self.pull_requests()
                if not (pr['head']['ref'] == branch and
                        pr['head']['repo'] and pr['head']['repo']['full_name'] == REPOSITORY)
                and paths.intersection(self.files(pr['number']))]

    def git(self, *args, cwd=None):
        return command('git', '-c', 'core.hooksPath=/dev/null', *args, cwd=cwd or self.root)

    def create_pr(self, row):
        return self.api('pulls', '--method', 'POST', '-f', 'head=' + row['branch'], '-f', 'base=main',
                        '-f', 'title=' + row['title'], '-f', 'body=' + row['body'])['number']


def merged_event(event):
    pr = event.get('pull_request', {})
    return (event.get('action') == 'closed' and pr.get('merged') is True and
            pr.get('base', {}).get('ref') == 'main' and
            event.get('repository', {}).get('full_name') == REPOSITORY)


def reconcile(event, entries, github, execute=False):
    if not merged_event(event):
        return []
    receipts = []
    for row in entries:
        branch = row['branch']
        result = {'card': row['card'], 'branch': branch, 'state': 'held'}
        receipts.append(result)
        if not all(github.holder_merged(n) for n in row['holders']):
            result['reason'] = 'holders_unmerged'
            continue
        prs = github.pull_requests(branch)
        if any(pr['state'] == 'closed' for pr in prs):
            result.update(state='retired', reason='prior_closed_pr')
            continue
        if len(prs) > 1 or any(pr['base']['ref'] != 'main' for pr in prs):
            raise ValueError('ambiguous held branch PR')
        github.git('fetch', 'origin', '+refs/heads/main:refs/remotes/origin/main',
                   f'+refs/heads/{branch}:refs/remotes/origin/{branch}')
        main = github.git('rev-parse', 'origin/main')
        head = github.git('rev-parse', 'origin/' + branch)
        paths = set(github.git('diff', '--name-only', '-z', '--no-renames', main + '...' + head).split('\0'))
        paths.discard('')
        if not paths:
            result.update(state='retired', reason='empty_diff')
            continue
        overlap = github.overlap(paths, branch)
        if overlap:
            result.update(reason='overlap', holders=overlap)
            continue
        with tempfile.TemporaryDirectory(prefix='held-release-') as directory:
            tree = str(Path(directory) / 'tree')
            github.git('worktree', 'add', '--detach', tree, head)
            try:
                try:
                    github.git('-c', 'user.name=github-actions[bot]', '-c',
                               'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                               'merge', '--no-edit', '--no-gpg-sign', main, cwd=tree)
                except subprocess.CalledProcessError:
                    conflicts = github.git('diff', '--name-only', '--diff-filter=U', cwd=tree).splitlines()
                    if not conflicts:
                        raise
                    key = hashlib.sha256(f"{row['card']}:{branch}:{head}:{main}".encode()).hexdigest()
                    result.update(state='fix_needed', fix_job={'idempotency_key': key,
                                  'card': row['card'], 'branch_head': head, 'main_head': main, 'paths': conflicts})
                    continue
                paths = set(github.git('diff', '--name-only', '-z', '--no-renames', main, 'HEAD', cwd=tree).split('\0'))
                paths.discard('')
                if not paths:
                    result.update(state='retired', reason='empty_diff')
                    continue
                overlap = github.overlap(paths, branch)
                if overlap:
                    result.update(reason='overlap_after_merge', holders=overlap)
                    continue
                refreshed = github.git('rev-parse', 'HEAD', cwd=tree)
                if not execute:
                    result.update(state='ready', branch_head=head, main_head=main)
                    continue
                if refreshed != head:
                    github.git('push', 'origin', f'HEAD:refs/heads/{branch}', cwd=tree)
                prs = github.pull_requests(branch)
                if any(pr['state'] != 'open' for pr in prs) or len(prs) > 1:
                    raise ValueError('PR state changed during release')
                number = prs[0]['number'] if prs else github.create_pr(row)
                result.update(state='published', pr=number, head=refreshed)
            finally:
                github.git('worktree', 'remove', '--force', tree)
    return receipts


def write_receipt(path, results, runtime_sha, state='succeeded'):
    path.write_text(json.dumps({'schema': 'held-release.v1', 'branches': results}, indent=2) + '\n')
    receipt = {'state': state, 'runtime_sha': runtime_sha,
               'counts': {state: sum(row['state'] == state for row in results)
                          for state in ('published', 'ready', 'held', 'retired', 'fix_needed')}}
    path.with_suffix('.txt').write_text('held-branch-release-receipt ' + json.dumps(receipt) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('.github/held-branches.json'))
    parser.add_argument('--event', type=Path, default=Path(os.environ.get('GITHUB_EVENT_PATH', '/dev/null')))
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help='Push and open PRs using an authorized coordinator identity')
    args = parser.parse_args()
    entries = manifest(json.loads(args.manifest.read_text()))
    event = json.loads(args.event.read_text())
    github = Github(Path.cwd())
    runtime_sha = command('git', 'rev-parse', 'HEAD')
    results = []
    state = 'failed'
    try:
        for row in entries:
            results.extend(reconcile(event, [row], github, execute=args.execute))
            write_receipt(args.receipt, results, runtime_sha, 'running')
        state = 'succeeded'
    finally:
        write_receipt(args.receipt, results, runtime_sha, state)


if __name__ == '__main__':
    main()
