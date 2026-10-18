"""Prepare one exact PR head in a disposable checkout; never merge the PR."""
from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ops'))
from git_env import scrubbed_env
from regen import OUTPUTS


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(['git', *args], cwd=repo, env=scrubbed_env(),
                            capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise ValueError(f'git {args[0]} failed: {result.stderr.strip()}')
    return result.stdout.strip()


def prepare(remote: str, branch: str, head: str, main: str, receipt: Path,
            regenerate: Callable[[Path], None], before_push: Callable[[], None] = lambda: None) -> str:
    if branch == 'main' or not branch or branch.startswith('-'):
        raise ValueError('preparation requires an ordinary PR branch')
    with tempfile.TemporaryDirectory(prefix='carr-prepare-pr.') as temp:
        repo = Path(temp)
        git(repo, 'clone', '--quiet', '--no-checkout', '--', remote, str(repo))
        git(repo, 'check-ref-format', f'refs/heads/{branch}')
        git(repo, 'checkout', '-q', '-b', 'prepare', f'origin/{branch}')
        if git(repo, 'rev-parse', 'HEAD') != head or git(repo, 'rev-parse', 'origin/main') != main:
            raise ValueError('PR head or main moved before preparation')
        git(repo, 'config', 'user.name', 'jbookout')
        git(repo, 'config', 'user.email', 'jbookout@users.noreply.github.com')
        git(repo, 'merge', '--no-commit', '--no-ff', 'origin/main', check=False)
        conflicts = git(repo, 'diff', '--name-only', '--diff-filter=U').splitlines()
        for path in conflicts:
            if path not in OUTPUTS or path in ('requirements.lock', 'mcp-server/src/scac-mutation-registry.current.generated.js'):
                raise ValueError(f'source conflict requires review: {path}')
            git(repo, 'restore', '--source=origin/main', '--staged', '--worktree', '--', path)
        if git(repo, 'diff', '--name-only', '--diff-filter=U'):
            raise ValueError('unresolved merge conflicts')
        if (repo / '.git/MERGE_HEAD').exists():
            message = repo / '.git/merge-message'
            message.write_text('chore(ci): merge main before regeneration\n')
            git(repo, 'commit', '-q', '-F', str(message))
        regenerate(repo)
        changed = set(filter(None, git(repo, 'diff', '--name-only', '-z').split('\0')))
        changed.update(filter(None, git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
        if changed:
            git(repo, 'add', '--', *sorted(changed))
        staged = git(repo, 'diff', '--cached', '--name-only')
        merging = (repo / '.git/MERGE_HEAD').exists()
        if staged or merging:
            message = repo / '.git/regen-message'
            message.write_text('chore(ci): regenerate derived files against current main\n')
            git(repo, 'commit', '-q', '-F', str(message))
        prepared = git(repo, 'rev-parse', 'HEAD')
        receipt.parent.mkdir(parents=True, exist_ok=True)
        pending = receipt.with_suffix('.tmp')
        pending.write_text(json.dumps({'head': head, 'main': main, 'prepared': prepared, 'branch': branch}) + '\n')
        pending.replace(receipt)
        if prepared == head:
            return head
        before_push()
        observed = dict(line.split('\t')[::-1] for line in git(repo, 'ls-remote', 'origin', 'refs/heads/main', f'refs/heads/{branch}').splitlines())
        if observed.get('refs/heads/main') != main:
            raise ValueError('main moved during regeneration; no push')
        if observed.get(f'refs/heads/{branch}') != head:
            raise ValueError('PR head moved during regeneration; no push')
        git(repo, 'push', 'origin', f'HEAD:refs/heads/{branch}')
        return prepared


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('remote', 'branch', 'head', 'main', 'pr', 'receipt'):
        parser.add_argument(f'--{name}', required=True)
    args = parser.parse_args()
    try:
        def regenerate(repo):
            env = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'LANG', 'TMPDIR', 'GH_TOKEN', 'GITHUB_TOKEN')}
            runtime = Path(__file__).resolve().parents[1] / 'mcp-server'
            if ((runtime / 'node_modules').is_dir() and (runtime / 'package-lock.json').read_bytes() == (repo / 'mcp-server/package-lock.json').read_bytes()):
                (repo / 'mcp-server/node_modules').symlink_to(runtime / 'node_modules', target_is_directory=True)
            else:
                subprocess.run(['npm', '--prefix', 'mcp-server', 'ci', '--ignore-scripts'], cwd=repo,
                               env=env, check=True, capture_output=True, timeout=300)
            result = subprocess.run([sys.executable, 'tools/regen.py', '--pr', args.pr], cwd=repo,
                                    env=env, capture_output=True, text=True, timeout=1200)
            if result.returncode:
                raise ValueError(result.stderr[-8000:])
        def verify_pr():
            result = subprocess.run(['gh', 'api', f'repos/jbookout/carr-system/pulls/{int(args.pr)}'],
                                    capture_output=True, text=True, check=True, timeout=120)
            pr = json.loads(result.stdout)
            if (pr['head']['sha'] != args.head or pr['state'] != 'open' or pr['base']['ref'] != 'main'
                    or any(label['name'] == 'do_not_merge' for label in pr['labels'])):
                raise ValueError('PR changed or held during regeneration; no push')
        sha = prepare(args.remote, args.branch, args.head, args.main, Path(args.receipt), regenerate, verify_pr)
        print(json.dumps({'head': sha, 'changed': sha != args.head}))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'prepare-pr: REFUSED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
