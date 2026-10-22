#!/usr/bin/env python3
"""Retire finished job checkouts and prune stale worktree registrations."""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ops.git_env import scrubbed_env

DAY = 86400


def command(args, cwd):
    return subprocess.run(args, cwd=cwd, env=scrubbed_env(), check=True,
                          capture_output=True, text=True, timeout=60).stdout.strip()


def git(repo, *args):
    return command(['git', '-C', str(repo), *args], repo.parent)


def live_writer(lock):
    lock.seek(0)
    raw = lock.read()
    if not raw.strip():
        return False
    owner = json.loads(raw)
    if not isinstance(owner, dict) or owner.get('kind') != 'process_group':
        return True
    pgid = owner.get('pgid')
    if type(pgid) is not int or pgid <= 0:
        return True
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def pull_requests(repo, branch):
    origin = git(repo, 'remote', 'get-url', 'origin')
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?', origin)
    if not match:
        raise ValueError('unknown GitHub origin')
    time.sleep(2)
    return json.loads(command(['gh', 'pr', 'list', '--repo', match[1], '--head', branch,
                               '--state', 'all', '--json', 'state,headRefOid'], repo))


def retire(repo, staging, now):
    staging.mkdir(parents=True, exist_ok=True)
    with (staging / '.cleaner-started').open('a+') as started:
        fcntl.flock(started, fcntl.LOCK_EX)
        started.seek(0)
        stamp = started.read().strip()
        if not stamp:
            started.write(str(now))
            started.flush()
            os.fsync(started.fileno())
            stamp = str(now)
    worktree = (repo / '.git').is_file()
    if now - float(stamp) < 7 * DAY:
        destination = staging / datetime.fromtimestamp(now).astimezone().strftime('%Y-%m-%d') / (repo.parent.name + '-' + uuid.uuid4().hex[:12])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if worktree:
            git(repo, 'worktree', 'move', str(repo), str(destination))
        else:
            destination.mkdir()
            shutil.move(str(repo), str(destination / 'checkout'))
        return {'action': 'staged', 'destination': str(destination)}
    if worktree:
        common = Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
        command(['git', '--git-dir', str(common), 'worktree', 'remove', str(repo)], repo.parent)
    else:
        shutil.rmtree(repo)
        # Preserve the workspace and its transcript/evidence; only Git data is retired.
    return {'action': 'removed'}


def clean(repo: Path, *, staging: Path, now=None, immediate=False) -> dict:
    repo = Path(repo).absolute()
    now = time.time() if now is None else now
    row = {'path': str(repo), 'action': 'kept'}
    try:
        if not (repo / '.git').exists() or Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo.resolve():
            return {**row, 'reason': 'not a checkout root'}
        if repo.is_symlink() or repo.parent.is_symlink():
            return {**row, 'reason': 'symlink checkout'}
        gitdir = Path(git(repo, 'rev-parse', '--absolute-git-dir'))
        with (gitdir / 'room-writer.lock').open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**row, 'reason': 'live or uncertain writer'}
            if live_writer(lock):
                return {**row, 'reason': 'live or uncertain writer'}
            if git(repo, 'stash', 'list'):
                return {**row, 'reason': 'stashed work'}
            if git(repo, 'status', '--porcelain', '--untracked-files=all'):
                return {**row, 'reason': 'dirty tree'}
            branch = git(repo, 'symbolic-ref', '--short', 'HEAD')
            if branch in ('main', 'master', 'develop'):
                return {**row, 'reason': 'protected branch'}
            if not immediate and now - max(repo.stat().st_mtime, (gitdir / 'index').stat().st_mtime) <= 2 * DAY:
                return {**row, 'reason': 'younger than two days'}
            head = git(repo, 'rev-parse', 'HEAD')
            if not (repo / '.git').is_file() and git(repo, 'rev-list', '--branches', '--not', 'HEAD'):
                return {**row, 'reason': 'other local branch has unpushed commits'}
            tip = git(repo, 'ls-remote', 'origin', f'refs/heads/{branch}').split()
            pushed = bool(tip and tip[0] == head)
            if tip and not pushed:
                git(repo, 'fetch', '--no-tags', 'origin', f'refs/heads/{branch}')
                git(repo, 'merge-base', '--is-ancestor', head, 'FETCH_HEAD')
                pushed = True
            prs = [] if immediate and pushed else pull_requests(repo, branch)
            if not isinstance(prs, list) or not all(isinstance(pr, dict) for pr in prs):
                raise ValueError('invalid PR response')
            closed = any(pr.get('state') in ('MERGED', 'CLOSED') and pr.get('headRefOid') == head for pr in prs)
            no_commits = False
            if not immediate and not closed:
                git(repo, 'fetch', '--no-tags', 'origin', 'refs/heads/main')
                no_commits = not git(repo, 'rev-list', 'FETCH_HEAD..HEAD')
            if not pushed and not closed and not no_commits:
                return {**row, 'reason': 'unpushed or unavailable remote branch'}
            if not immediate and not closed and not no_commits:
                return {**row, 'reason': 'open or unknown PR with commits'}
            if git(repo, 'status', '--porcelain', '--untracked-files=all') or git(repo, 'rev-parse', 'HEAD') != head:
                return {**row, 'reason': 'checkout changed during inspection'}
            return {**row, **retire(repo, staging, now)}
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return {**row, 'reason': 'evidence unavailable'}


def sweep(repos, tmp_root, staging):
    rows = []
    for repo in repos:
        try:
            git(repo, 'worktree', 'prune')
            rows.append({'path': str(repo), 'action': 'pruned'})
        except (OSError, subprocess.SubprocessError):
            rows.append({'path': str(repo), 'action': 'kept', 'reason': 'prune failed'})
    for workspace in sorted(tmp_root.glob('room-codex-*')):
        repo = workspace / 'checkout'
        if repo.exists():
            rows.append(clean(repo, staging=staging))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', action='append', type=Path)
    parser.add_argument('--tmp-root', type=Path, default=Path('/private/tmp'))
    parser.add_argument('--staging', type=Path, default=Path.home() / '_to_delete')
    args = parser.parse_args()
    repos = args.repo or [Path.home() / name for name in ('carr-system', 'doctorcre-app', 'software-factory')]
    print(json.dumps(sweep(repos, args.tmp_root, args.staging), sort_keys=True))


if __name__ == '__main__':
    main()
