#!/usr/bin/env python3
"""Regenerate CARR operation contracts or the generated schema snapshot."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import scrubbed_env

REGISTRY = 'mcp-server/src/scac-mutation-registry.current.generated.js'
SCHEMA = 'db/schema.sql'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, env=scrubbed_env())


def regenerate():
    unresolved = git('diff', '--name-only', '--diff-filter=U', '-z').split(b'\0')
    unresolved = [path.decode() for path in unresolved if path]
    if unresolved:
        raise ValueError('resolve source conflicts before regenerating the registry; sealed metadata conflicts require explicit resolution')
    # Regeneration may change reviewed rows, never the sealed metadata facade.
    target = ROOT / REGISTRY
    committed = git('show', f'HEAD:{REGISTRY}').decode()
    prefix = 'export const SCAC_MUTATION_OPERATIONS'
    if committed.split(prefix)[0].split('export { SCAC_MUTATION_OPERATIONS')[0] != target.read_text().split('export { SCAC_MUTATION_OPERATIONS')[0]:
        raise ValueError('sealed metadata drift; resolve it explicitly')
    subprocess.run(['node', 'ops/scac-mutation-inventory.mjs', '--write-current'], cwd=ROOT, env=scrubbed_env(), check=True)
    subprocess.run(['node', 'ops/scac-mutation-inventory.mjs'], cwd=ROOT, env=scrubbed_env(), check=True)
    print('Registry contracts regenerated.')


def regenerate_schema():
    unresolved = git('diff', '--name-only', '--diff-filter=U', '-z').split(b'\0')
    unresolved = [path.decode() for path in unresolved if path]
    if any(path != SCHEMA for path in unresolved):
        raise ValueError('resolve source conflicts before regenerating the schema')
    target = ROOT / SCHEMA
    original = target.read_bytes()
    resolving = SCHEMA in unresolved
    if resolving and b'<<<<<<< ' not in original:
        raise ValueError('schema conflict markers are missing; preserve manual edits before regeneration')
    try:
        subprocess.run([sys.executable, ROOT / 'ops/migration-shadow.py', '--write'],
                       cwd=ROOT, env=scrubbed_env(), check=True)
        if resolving:
            git('add', '--', SCHEMA)
    except BaseException:
        target.write_bytes(original)
        raise
    print('Schema regenerated' + (' and its Git conflict resolved.' if resolving else '.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=['registry', 'schema'])
    target = parser.parse_args().target
    try:
        regenerate() if target == 'registry' else regenerate_schema()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'regen: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
