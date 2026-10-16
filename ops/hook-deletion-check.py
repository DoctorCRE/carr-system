#!/usr/bin/env python3
"""Refuse deletion of scripts still wired at the PR base. Unwire in a prior PR."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from git_env import scrubbed_env

WIRING = ('ops/config/hooks.json', 'ops/config/codex-hooks.json',
          'ops/config/claude-continuity-hooks.json', '.claude/settings.json')


def check(repo, base):
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args], check=True,
                              capture_output=True, text=True, env=scrubbed_env()).stdout

    if not base:
        event_path = os.environ.get('GITHUB_EVENT_PATH')
        if event_path:
            event = json.loads(Path(event_path).read_text())
            base = event.get('pull_request', {}).get('base', {}).get('sha') or event.get('before')
        base = base or git('merge-base', 'origin/main', 'HEAD').strip()
    wired = set()
    files = set(git('ls-tree', '-r', '--name-only', base).splitlines())
    for path in WIRING:
        if path not in files:
            continue
        document = json.loads(git('show', f'{base}:{path}'))
        for groups in document.get('hooks', document).values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                for hook in group.get('hooks', []):
                    wired.update(re.findall(r'/(hooks/[\w.-]+|ops/[\w.-]+)\.(py|sh|mjs|js)\b', hook.get('command', '')))
    deleted = set(git('diff', '--no-renames', '--name-only', '--diff-filter=D', base, 'HEAD').splitlines())
    unsafe = sorted(deleted & {f'{path}.{suffix}' for path, suffix in wired})
    for path in unsafe:
        print(f'REFUSED: {path} is wired at the base revision. Unwire first; delete in a later PR.')
    return 1 if unsafe else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--base')
    args = parser.parse_args()
    try:
        return check(args.repo, args.base)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'REFUSED: cannot verify two-phase hook deletion: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
