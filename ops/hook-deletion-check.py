#!/usr/bin/env python3
"""Refuse deletion of scripts still wired at the PR base. Unwire in a prior PR."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from git_env import scrubbed_env

WIRING = ('ops/config/hooks.json', 'ops/config/codex-hooks.json',
          'ops/config/claude-continuity-hooks.json', '.claude/settings.json')


def command_target(command, repo):
    # Current templates end their missing-file guard before the invocation.
    prefix, separator, invocation = command.rpartition('; fi; ')
    if not separator:
        prefix = ''
        invocation = command
    directory_guard = '[ -d "${HOME}/carr-system/hooks" ] || exit 0; '
    if invocation.startswith(directory_guard):
        prefix += directory_guard
        invocation = invocation[len(directory_guard):]
    # Only shell builtins may precede the launch. Never discard another script
    # invocation while stripping the guard, or an earlier target could be lost.
    if '$(' in prefix or '`' in prefix or '\n' in prefix:
        raise ValueError(f'unsupported hook guard: {prefix}')
    lexer = shlex.shlex(prefix, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    statement_start = True
    for token in lexer:
        if token in {'|', '&'} or (re.fullmatch(r'[A-Za-z_]\w*=.*', token) and
                                   token not in {'r=${HOME}/carr-system', 'r=${CLAUDE_PROJECT_DIR:-}'}):
            raise ValueError(f'unsupported hook guard: {token}')
        if token in {';', '&&', '||'}:
            statement_start = True
        elif statement_start:
            if token in {'if', 'then', 'else'}:
                continue
            if token not in {'[', 'printf', 'exit', 'fi'} and not re.fullmatch(r'[A-Za-z_]\w*=.*', token):
                raise ValueError(f'unsupported hook guard command: {token}')
            statement_start = False
    tokens = shlex.split(invocation)
    if '$(' in invocation or any(re.search(r'[;|&<>`\n]', token) for token in tokens):
        raise ValueError(f'unsupported hook command: {invocation}')
    while tokens and (tokens[0] == 'exec' or re.fullmatch(r'[A-Za-z_]\w*=\S*', tokens[0])):
        tokens.pop(0)
    if tokens and tokens[0] == '/usr/bin/env':
        tokens.pop(0)
        while tokens and re.fullmatch(r'[A-Za-z_]\w*=\S*', tokens[0]):
            tokens.pop(0)
    if tokens and re.fullmatch(r'python(?:\d+(?:\.\d+)*)?|node|bash|sh', Path(tokens[0]).name):
        tokens.pop(0)
    if not tokens or Path(tokens[0]).suffix not in {'.py', '.sh', '.js', '.mjs'}:
        raise ValueError(f'unsupported hook target: {invocation}')
    target = tokens[0]
    for prefix in ('{{REPO}}', '${HOME}/carr-system', '$HOME/carr-system',
                   '${CLAUDE_PROJECT_DIR}', '$CLAUDE_PROJECT_DIR', '${r}', '$r'):
        if target.startswith(prefix + '/'):
            target = str(repo) + target[len(prefix):]
            break
    if re.search(r'[$`{}*?\[\]~]', target):
        raise ValueError(f'unsupported hook target: {target}')
    # Normalize lexically: resolving HEAD's symlinks could hide a base-wired path.
    path = Path(os.path.normpath(repo / target))
    if not path.is_relative_to(repo):
        raise ValueError(f'hook target is outside the repository: {target}')
    return path


def check(repo, base):
    repo = Path(os.path.abspath(repo))
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
                    wired.add(command_target(hook.get('command', ''), repo))
    deleted = set(git('diff', '--no-renames', '--name-only', '--diff-filter=D', base, 'HEAD').splitlines())
    unsafe = sorted(path for path in deleted if repo / path in wired)
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
