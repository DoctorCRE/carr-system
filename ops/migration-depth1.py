#!/usr/bin/env python3
"""Require the eight affected suites and reconstruction in a fresh depth-1 clone."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from git_env import scrubbed_env

SUITES = tuple(f'mcp-server/test/{name}.test.mjs' for name in (
    'client-portal-postgres', 'client-portal-register-postgres',
    'client-portal-session-postgres', 'deal-extraction-postgres',
    'deal-schedules-postgres', 'fact-store-postgres', 'market-terms-postgres',
    'portal-migration-runtime'))


def assert_checkout(repo: Path, env: dict[str, str]) -> str:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], env=env, text=True).strip()
    if git('rev-parse', '--is-shallow-repository') != 'true' or git('rev-list', '--count', 'HEAD') != '1':
        raise ValueError('acceptance requires exactly one visible commit in a shallow checkout')
    if (repo / '.git/objects/info/alternates').exists():
        raise ValueError('acceptance refuses object alternates')
    baseline = json.loads((repo / 'db/migration-baseline.json').read_text())
    probe = subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', baseline['source_revision']],
                           env=env, capture_output=True)
    if probe.returncode == 0:
        raise ValueError('historical pin is readable; acceptance is invalid')
    return git('rev-parse', 'HEAD')


def run_acceptance(repo: Path, env: dict[str, str], *, reconstruction: bool = True) -> dict:
    sha = assert_checkout(repo, env)
    result = subprocess.run(['node', '--test', '--test-reporter=tap', '--test-concurrency=1', *SUITES],
                            cwd=repo, env=env, capture_output=True, text=True, timeout=1200)
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    counts = {key: int(value) for key, value in re.findall(
        r'^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$', result.stdout, re.M)}
    if result.returncode or not counts.get('tests') or counts.get('pass') != counts['tests'] or any(
            counts.get(key, 1) for key in ('fail', 'cancelled', 'skipped', 'todo')):
        raise ValueError(f'depth-1 suites failed or skipped: exit {result.returncode}, {counts}')
    if reconstruction:
        result = subprocess.run([env['CARR_CI_PYTHON'], 'ops/schema-reconstruction-selftest.py', '--postgres'],
                                cwd=repo, env=env, capture_output=True, text=True, timeout=1200)
        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)
        if result.returncode or 'skipped=' in result.stderr or not re.search(r'Ran [1-9]\d* test', result.stderr):
            raise ValueError('depth-1 reconstruction failed or skipped')
    return dict(sha=sha, shallow=True, visible_commits=1, suites=SUITES,
                counts=counts, reconstruction=reconstruction)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument('--source', default=str(root))
    parser.add_argument('--branch')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    env = scrubbed_env()
    env['GIT_NO_LAZY_FETCH'] = '1'
    (root / 'out').mkdir(exist_ok=True)
    source = args.source if args.source.startswith(('https://', 'file://')) else Path(args.source).resolve().as_uri()
    with tempfile.TemporaryDirectory(prefix='migration-depth1-', dir=root / 'out') as temp:
        repo = Path(temp) / 'repo'
        command = ['git', 'clone', '--depth', '1', '--no-hardlinks']
        if args.branch:
            command += ['--branch', args.branch]
        subprocess.run([*command, source, str(repo)], env=env, check=True, timeout=180)
        sha = assert_checkout(repo, env)
        subprocess.run([sys.executable, '-m', 'venv', str(Path(temp) / 'venv')], check=True, timeout=120)
        python = Path(temp) / 'venv/bin/python'
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', 'requirements.lock'],
                       cwd=repo, env=env, check=True, timeout=600)
        env['CARR_CI_PYTHON'] = str(python)
        env['PATH'] = str(python.parent) + os.pathsep + env['PATH']
        subprocess.run(['npm', '--prefix', 'mcp-server', 'ci', '--no-audit'], cwd=repo,
                       env=env, check=True, timeout=600)
        subprocess.run([str(python), 'tools/generate_migration_archive.py'], cwd=repo,
                       env=env, check=True, timeout=180)
        receipt = run_acceptance(repo, env)
        if receipt['sha'] != sha:
            raise ValueError('checkout changed during acceptance')
        if args.receipt:
            args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, subprocess.SubprocessError) as exc:
        print(f'migration depth-1 acceptance: {exc}', file=sys.stderr)
        raise SystemExit(1)
