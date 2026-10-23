#!/usr/bin/env python3
"""Compare changed tests on the PR base code and exact head code."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from git_env import fixture_env


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], env=fixture_env())


def test_file(name):
    path = Path(name)
    return (bool(re.search(r'(?:^test[-_].*|.*[-_]test|.*-selftest)\.(?:py|sh)$', path.name))
            or bool(re.search(r'\.test\.(?:js|mjs)$', path.name)))


def source_file(name, executable=False):
    return (not test_file(name) and not set(Path(name).parts) & {'test', 'tests', 'fixtures', 'node_modules'}
            and (executable or Path(name).suffix in {'.py', '.sh', '.js', '.mjs', '.ts', '.tsx', '.rs', '.sql', '.html', '.css'})
            and not name.startswith('ops/config/'))


def note(text):
    print(text, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(text + '\n')


def run_tests(tree, files):
    failed = False
    for name in files:
        path = tree / name
        cwd = tree
        if path.suffix in {'.js', '.mjs'}:
            cwd = next((parent for parent in path.parents if (parent / 'package.json').is_file()), tree)
            command = ['node', '--test', str(path)]
        elif path.suffix == '.sh':
            command = ['bash', str(path)]
        elif re.search(r'(?m)^(?:import pytest|from pytest\b)', path.read_text()):
            command = [sys.executable, '-m', 'pytest', '-q', str(path)]
        else:
            command = [sys.executable, str(path)]
        result = subprocess.run(command, cwd=cwd, env=fixture_env(), timeout=600)
        failed |= result.returncode != 0
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--head', required=True)
    args = parser.parse_args()
    if not all(re.fullmatch(r'[0-9a-f]{40}', sha) for sha in (args.base, args.head)):
        parser.error('base and head must be full commit SHAs')
    repo = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip())
    files = git(repo, 'diff', '--name-only', '-z', '--no-renames', '--diff-filter=AM',
                f'{args.base}...{args.head}').decode().split('\0')[:-1]
    tests = [name for name in files if test_file(name)]
    if not tests:
        note('test-first: not applicable; no added or modified tests')
        return 0
    if not any(source_file(name, git(repo, 'ls-tree', args.head, '--', name).startswith(b'100755 ')) for name in files):
        note('test-first: not applicable; no added or modified non-test source (deletion/docs/config only)')
        return 0
    note(f'test-first: {len(tests)} changed test files; base {args.base}; head {args.head}')
    results = {}
    with tempfile.TemporaryDirectory(prefix='carr-test-first-') as directory:
        for label, sha in [('base', args.base), ('head', args.head)]:
            tree = Path(directory) / label
            git(repo, 'worktree', 'add', '--detach', '--quiet', str(tree), sha)
            try:
                if label == 'base':
                    for name in tests:
                        target = tree / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(git(repo, 'show', f'{args.head}:{name}'))
                for package in ('mcp-server', 'practice-plugin', 'control-room', 'workspace'):
                    installed = repo / package / 'node_modules'
                    target = tree / package / 'node_modules'
                    if installed.is_dir() and target.parent.is_dir() and not target.exists():
                        target.symlink_to(installed, target_is_directory=True)
                results[label] = run_tests(tree, tests)
                note(f"{label}: {'FAIL' if results[label] else 'PASS'} ({len(tests)} selected files)")
            finally:
                git(repo, 'worktree', 'remove', '--force', str(tree))
    if not results['base']:
        note("test-first: these tests don't test the change; selected tests pass on base")
        return 1
    if results['head']:
        note('test-first: selected tests fail on head')
        return 1
    note('test-first: PASS; selected tests fail on base and pass on head')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
