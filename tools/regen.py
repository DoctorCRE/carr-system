#!/usr/bin/env python3
"""Regenerate CARR operation contracts or the generated schema snapshot."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import scrubbed_env

REGISTRY = 'mcp-server/src/scac-mutation-registry.current.generated.js'
SCHEMA = 'db/schema.sql'
OPERATIONS = re.compile(rb'export const SCAC_MUTATION_OPERATIONS = Object.freeze\(\{[\s\S]*?\n\}\);')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, env=scrubbed_env())


def grant_inputs(path):
    source = path.read_text()
    match = re.search(r'\$current_grants\$([\s\S]*?)\$current_grants\$', source)
    if match:
        contract = json.loads(match[1])
    else:
        function = re.search(
            r'CREATE (?:OR REPLACE )?FUNCTION ops\.scac_current_grant_contract\(\) RETURNS jsonb'
            r'[\s\S]*?\bAS (\$[A-Za-z_0-9]*\$)([\s\S]*?)\1;', source, re.I)
        body = re.fullmatch(r"\s*SELECT '((?:[^']|'')*)'::jsonb\s*;?\s*", function[2] if function else '', re.I)
        if not body:
            raise ValueError('expected a literal grant contract from a migration or rehearsal snapshot')
        contract = json.loads(body[1].replace("''", "'"))
    inputs = (ROOT / REGISTRY).read_text()
    version = re.search(r'SCAC_MUTATION_REGISTRY_VERSION = "([^"]+)"', inputs)[1]
    if (contract.get('registry_version') != version or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', contract.get('registry_digest', '')) or
            not isinstance(contract.get('secdef_execute'), list)):
        raise ValueError('malformed or incompatible current grant contract')
    return re.sub(r'\nexport const SCAC_MUTATION_CURRENT_DB_GRANTS = [\s\S]*$',
                  lambda _: '\nexport const SCAC_MUTATION_CURRENT_DB_GRANTS = Object.freeze(' +
                  json.dumps(contract, indent=2, ensure_ascii=False) + ');\n', inputs)


def regenerate(grant_contract=None):
    unresolved = git('diff', '--name-only', '--diff-filter=U', '-z').split(b'\0')
    unresolved = [path.decode() for path in unresolved if path]
    if any(path != REGISTRY for path in unresolved):
        raise ValueError('resolve source conflicts before regenerating the registry')
    target = ROOT / REGISTRY
    original = target.read_bytes()
    resolving = REGISTRY in unresolved
    if resolving and grant_contract:
        raise ValueError('resolve the registry conflict before refreshing grant expectations')
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
        if grant_contract:
            target.write_text(grant_inputs(grant_contract))
        subprocess.run(['node', 'ops/scac-mutation-inventory.mjs', '--write-current'], cwd=ROOT, env=scrubbed_env(), check=True)
        subprocess.run(['node', 'ops/scac-mutation-inventory.mjs'], cwd=ROOT, env=scrubbed_env(), check=True)
        if resolving:
            git('add', '--', REGISTRY)
    except BaseException:
        target.write_bytes(original)
        raise
    print('Registry regenerated' + (' and its Git conflict resolved.' if resolving else '.'))


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
    parser.add_argument('--grant-contract', type=Path, help='reviewed migration or disposable rehearsal schema; registry only')
    args = parser.parse_args()
    target = args.target
    if args.grant_contract and target != 'registry':
        parser.error('--grant-contract requires registry')
    try:
        regenerate(args.grant_contract) if target == 'registry' else regenerate_schema()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'regen: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
