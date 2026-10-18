#!/usr/bin/env python3
"""Generate baseline identities from an exact source commit, without database access.

Usage: .venv/bin/python ops/migration-baseline-manifest.py --source-revision SHA
The JSON is written to stdout. No checkout, database, or ledger is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import scrubbed_env
from migration_baseline import MigrationBaseline, SCHEMA, runner_sha256


def build_manifest(repository: Path, source_revision: str) -> dict:
    if not re.fullmatch(r'[0-9a-f]{40}', source_revision):
        raise ValueError('source revision must be an exact Git commit')

    def git(*args):
        return subprocess.run(['git', *args], cwd=repository, check=True,
                              env=scrubbed_env(), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout

    if git('cat-file', '-t', source_revision).strip() != b'commit':
        raise ValueError('source revision is not a Git commit')
    snapshot = git('show', f'{source_revision}:db/schema.sql')
    parse = runpy.run_path(str(ROOT / 'ops/migration-safety-gate.py'))['ledger_rows']
    ledger = parse(snapshot.decode())
    if not ledger:
        raise ValueError('source snapshot has no complete migration ledger')
    names = git('ls-tree', '-r', '--name-only', source_revision, '--', 'migrations').decode().splitlines()
    files = {Path(path).name: git('show', f'{source_revision}:{path}').decode()
             for path in names if path.endswith('.sql') and Path(path).parent == Path('migrations')}
    if {name: runner_sha256(sql) for name, sql in files.items()} != ledger:
        raise ValueError('source snapshot ledger differs from source migration files')
    document = dict(schema=SCHEMA, source_revision=source_revision,
                    snapshot=dict(path='db/schema.sql', sha256=hashlib.sha256(snapshot).hexdigest()),
                    cutoff=max(ledger),
                    migrations=[dict(filename=name, sha256=digest) for name, digest in sorted(ledger.items())])
    MigrationBaseline.from_document(document)
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    try:
        document = build_manifest(ROOT, args.source_revision)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f'migration baseline manifest refused: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(document, indent=2) + '\n', end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
