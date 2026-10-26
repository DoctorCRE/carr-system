#!/usr/bin/env python3
"""Regenerate CARR operation contracts, including an operations-only Git conflict."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import scrubbed_env

REGISTRY = 'mcp-server/src/scac-mutation-registry.current.generated.js'
OPERATIONS = re.compile(rb'export const SCAC_MUTATION_OPERATIONS = Object.freeze\(\{[\s\S]*?\n\}\);')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, env=scrubbed_env())


def regenerate():
    unresolved = git('diff', '--name-only', '--diff-filter=U', '-z').split(b'\0')
    unresolved = [path.decode() for path in unresolved if path]
    if any(path != REGISTRY for path in unresolved):
        raise ValueError('resolve source conflicts before regenerating the registry')
    target = ROOT / REGISTRY
    original = target.read_bytes()
    resolving = REGISTRY in unresolved
    if resolving:
        ours = git('show', f':2:{REGISTRY}')
        theirs = git('show', f':3:{REGISTRY}')
        parts = [OPERATIONS.split(source) for source in (ours, theirs)]
        if any(len(part) != 2 for part in parts) or parts[0] != parts[1]:
            raise ValueError('registry conflict includes sealed metadata or grants; resolve those explicitly')
        if OPERATIONS.split(original) != parts[0]:
            raise ValueError('registry has edits outside operations; preserve those before regeneration')
        target.write_bytes(ours)
    try:
        subprocess.run(['node', 'ops/scac-mutation-inventory.mjs', '--write-current'], cwd=ROOT, env=scrubbed_env(), check=True)
        subprocess.run(['node', 'ops/scac-mutation-inventory.mjs'], cwd=ROOT, env=scrubbed_env(), check=True)
        if resolving:
            git('add', '--', REGISTRY)
    except BaseException:
        target.write_bytes(original)
        raise
    print('Registry regenerated' + (' and its Git conflict resolved.' if resolving else '.'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=['registry'])
    parser.parse_args()
    try:
        regenerate()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'regen: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
