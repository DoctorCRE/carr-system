"""Compose pinned heads locally; publish only an atomic, bound receipt."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prepare_pr import git, regen_env
from regen import REGENERATED_OUTPUTS


def compose(request, regenerate):
    with tempfile.TemporaryDirectory(prefix='carr-merge-batch.') as temp:
        repo = Path(temp)
        git(repo, 'clone', '--quiet', '--no-checkout', '--', request['source'], str(repo))
        git(repo, 'checkout', '-q', '-b', 'batch', request['base'])
        git(repo, 'config', 'user.name', 'jbookout')
        git(repo, 'config', 'user.email', 'jbookout@users.noreply.github.com')
        for member in request['members']:
            previous = git(repo, 'rev-parse', 'HEAD')
            git(repo, 'merge', '--no-commit', '--no-ff', member['head'], check=False)
            conflicts = git(repo, 'diff', '--name-only', '--diff-filter=U').splitlines()
            for path in conflicts:
                if request['repo'] != 'jbookout/carr-system' or path not in REGENERATED_OUTPUTS:
                    return dict(request=request, conflict=member['pr'], detail=f'source conflict: {path}')
                git(repo, 'restore', '--source='+previous, '--staged', '--worktree', '--', path)
            if (repo / '.git/MERGE_HEAD').exists():
                git(repo, 'commit', '-q', '-m', f'Merge #{member["pr"]} at {member["head"]}')
            git(repo, 'merge-base', '--is-ancestor', member['head'], 'HEAD')
        if request['repo'] == 'jbookout/carr-system':
            regenerate(repo)
        changed = set(filter(None, git(repo, 'diff', '--name-only', '-z').split('\0')))
        changed.update(filter(None, git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
        if changed:
            git(repo, 'add', '--', *sorted(changed))
        tree = git(repo, 'write-tree')
        parents = [request['base'], *dict.fromkeys(m['head'] for m in request['members'])]
        args = [arg for parent in parents for arg in ('-p', parent)]
        message = 'Merge queue batch\n\n' + '\n'.join(f'Merges #{m["pr"]} at {m["head"]}' for m in request['members'])
        candidate = git(repo, 'commit-tree', tree, *args, '-m', message)
        git(repo, 'push', '--quiet', 'origin', f'{candidate}:refs/heads/{request["branch"]}')
        return dict(request=request, candidate=candidate, tree=tree)


def main():
    request_path, receipt_path = map(Path, sys.argv[1:])
    request = json.loads(request_path.read_text())
    def regenerate(repo):
        runtime = Path(request['runtime']) / 'mcp-server'
        lock = repo / 'mcp-server/package-lock.json'
        env = regen_env(os.environ)
        if lock.exists():
            if ((runtime / 'node_modules').is_dir()
                    and (runtime / 'package-lock.json').read_bytes() == lock.read_bytes()):
                (repo / 'mcp-server/node_modules').symlink_to(runtime / 'node_modules', target_is_directory=True)
            else:
                subprocess.run(['npm', '--prefix', 'mcp-server', 'ci', '--ignore-scripts'], cwd=repo,
                               env=env, check=True, capture_output=True, timeout=300)
        # Every member is already in the candidate, so its migrations are not peers.
        git(repo, 'update-ref', 'refs/remotes/origin/main', request['base'])
        exclusions = [arg for member in request['members'][1:] for arg in ('--exclude-pr', str(member['pr']))]
        subprocess.run([sys.executable, 'tools/regen.py', '--pr', str(request['members'][0]['pr']), *exclusions],
                       cwd=repo, env=env, check=True, timeout=1200)
    try:
        result = compose(request, regenerate)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result = dict(request=request, error=str(exc))
    temporary = receipt_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(result) + '\n')
    temporary.replace(receipt_path)


if __name__ == '__main__':
    main()
