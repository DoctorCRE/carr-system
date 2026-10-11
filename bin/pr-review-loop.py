#!/usr/bin/env python3
"""Bounded PR repair/review loop using the canonical queue policy."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
POLICY = runpy.run_path(str(ROOT / 'tools/merge_queue/main.py'))
DOT = runpy.run_path(str(ROOT / 'bin/dot-review.py'))


@contextmanager
def loop_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def inspect(queue, repo, n):
    pr = queue.pr(repo, n)
    if pr.get('state') not in ('open', 'closed'):
        raise ValueError('unreadable PR state')
    if pr['state'] == 'closed':
        return {'state': 'closed', 'verdict': '', 'ready': False}
    sha = pr['head']['sha']
    last, receipt = DOT['independent_verdict'](queue.pages(f'repos/{repo}/issues/{n}/comments?per_page=100'), repo, n,
                                              api=lambda path: queue.pages(path) if '/commits?' in path else queue.pr(repo, n))
    verdict = ''
    if last:
        body = last.get('body', '')
        decision = POLICY['REVIEW']['verdict'](body, DOT['review_config'](repo))
        header = POLICY['REVIEW']['reviewed_header_sha'](body if decision == 'approve' else body.replace('REVIEW: BLOCKED', 'APPROVE', 1))
        if header:
            current = header == sha
            if not current and decision == 'approve' and receipt is None:
                current = queue.covered(repo, header, sha)
            verdict = ('APPROVE' if decision == 'approve' else 'REVIEW: BLOCKED') + ('' if current else '-STALE')
    ready = queue.green(repo, n, sha) and pr.get('mergeable') is True and pr.get('mergeable_state') != 'dirty'
    return {'state': 'open', 'head': sha, 'verdict': verdict, 'ready': ready}


def factory_helpers():
    factory = Path(os.environ.get('CARR_FACTORY_ROOT', str(Path.home() / 'software-factory'))).resolve()
    revision = os.environ.get('CARR_FACTORY_REVISION', '')
    config = os.environ.get('FACTORY_PR_CONFIG', '')
    if not POLICY['SHA'].fullmatch(revision) or not config or not Path(config).is_file():
        raise ValueError('factory binding missing: set CARR_FACTORY_REVISION and FACTORY_PR_CONFIG')
    try:
        actual = subprocess.run(['git', '-C', str(factory), 'rev-parse', 'HEAD'], text=True,
                                capture_output=True, check=True, timeout=10).stdout.strip()
        if actual != revision:
            raise ValueError('factory revision differs from binding')
        dirty = subprocess.run(['git', '-C', str(factory), 'diff', revision, '--', 'deploy/orch', 'bin', 'src'],
                               text=True, capture_output=True, check=True, timeout=10).stdout
        if dirty:
            raise ValueError('factory helper source differs from bound revision')
        paths = {}
        for name in ('branch-wt', 'fix-pr', 'ci-fix'):
            relative = 'deploy/orch/' + name + '.sh'
            subprocess.run(['git', '-C', str(factory), 'cat-file', '-e', revision + ':' + relative],
                           check=True, capture_output=True, timeout=10)
            paths[name] = factory / relative
        return paths, {**os.environ, 'FACTORY_ROOT': str(factory), 'FACTORY_PR_CONFIG': config}
    except subprocess.SubprocessError as exc:
        raise ValueError('factory operational dependency unavailable') from exc


def main(argv=None):
    args = argv or sys.argv[1:]
    if len(args) not in (3, 4) or args[0] not in POLICY['REPOS']:
        raise ValueError('usage: pr-review-loop.py owner/repo pr worktree|- [rounds]')
    repo, n, tree = args[0], int(args[1]), args[2]
    rounds = int(args[3]) if len(args) == 4 else 3
    if n <= 0 or not 1 <= rounds <= 10:
        raise ValueError('invalid PR or round bound')
    orch = ROOT / 'out/orch'
    lock = orch / 'locks' / f'pr-loop-{repo.split("/")[1]}-{n}.lock'
    with loop_lock(lock):
        queue = POLICY['Queue'](orch / 'loop-state', ROOT)
        try:
            for _ in range(rounds):
                state = inspect(queue, repo, n)
                if state['state'] == 'closed' or state['verdict'] == 'APPROVE' and state['ready']:
                    print(json.dumps(state))
                    return 0
                if state['verdict'] in ('REVIEW: BLOCKED', 'APPROVE'):
                    helpers, env = factory_helpers()
                    if tree == '-':
                        home = Path.home() / repo.split('/')[1]
                        branch = queue.pr(repo, n)['head']['ref']
                        tree = subprocess.run(['sh', str(helpers['branch-wt']), repo, branch, str(home) + '-fix-' + str(n)],
                                              env=env, capture_output=True, text=True, check=True, timeout=120).stdout.strip()
                    if not Path(tree).is_dir():
                        raise ValueError('session-owned repair worktree unavailable')
                    helper = 'fix-pr' if state['verdict'] == 'REVIEW: BLOCKED' else 'ci-fix'
                    subprocess.run(['sh', str(helpers[helper]), repo, str(n), tree, '--no-loop'], env=env, check=True, timeout=4500)
                    current = queue.pr(repo, n)
                    if current['head']['sha'] == state['head']:
                        raise ValueError('repair made no head progress; stopped')
                # Routing is durable and fast. The feeder performs evidence execution separately.
                print(json.dumps(DOT['submit'](repo, n, orch)))
                return 20
            return 1
        finally:
            queue.db.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'PR loop recoverable failure: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
