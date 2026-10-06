"""Read the generated registry chain across Python and shell consumers."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'ops/config/scac-registry-chain.json'


def validate_chain(chain):
    if chain.get('schema') != 'scac-registry-chain.v1' or not chain.get('versions'):
        raise ValueError('unsupported or empty registry chain')
    predecessor = None
    for number, row in enumerate(chain['versions'], 1):
        if row['number'] != number or row['version'] != f'scac-mutation-registry.v{number}' or row['predecessor'] != predecessor:
            raise ValueError('registry chain continuity drifted')
        predecessor = row['version']
    return chain


def registry_chain():
    return validate_chain(json.loads(MANIFEST.read_text()))


def snapshot_selection(number, chain=None):
    chain = chain or registry_chain()
    row = next((r for r in chain['versions'] if r['number'] == int(number)), None)
    if row is None:
        raise ValueError('unknown snapshot registry version')
    spec = row.get('snapshot', {})
    versions = chain['versions'][:row['number']]
    historical = [r for r in versions[:-1] if r['number'] not in spec.get('history_excluded', [])]
    return {
        'SCAC_CURRENT_NUMBER': str(row['number']),
        'SCAC_EXPECTED_CURRENT_DIGEST': row['digest'].removeprefix('sha256:'),
        'SCAC_EXPECTED_CURRENT_SOURCE_SET': row['source_set_digest'].removeprefix('sha256:'),
        'SCAC_EXPECTED_CURRENT_CATALOG': row['catalog_digest'].removeprefix('sha256:'),
        'SCAC_VERSION_COUNT': str(len(versions)),
        'SCAC_TOTAL_ENTRY_COUNT': str(sum(r['entry_count'] for r in versions)),
        'SCAC_CURRENT_ENTRY_COUNT': str(row['entry_count']),
        'SCAC_CURRENT_SOURCE_COUNT': str(row['source_count']),
        'SCAC_CURRENT_RUNTIME': str(ROOT / 'mcp-server/src/scac-mutation-registry.current.generated.js'),
        'SCAC_VERSION_ARRAY': ','.join("'"+r['version']+"'" for r in versions),
        'SCAC_HISTORICAL_ARRAY': ','.join("'"+r['version']+"'" for r in historical),
        'SCAC_FULL_SET_SEAL_COUNT': str(row['number'] if spec.get('include_current_entry_set') else row['number']-1),
        'SCAC_CURRENT_CATALOG_FUNCTION': spec.get('catalog_function', f"ops.scac_mutation_catalog_v{row['number']}_current()"),
    }


def successor_snapshot_selection(applied, existing_number, chain=None):
    chain = chain or registry_chain()
    applied = set(applied)
    selected = None
    for row in chain['versions'][int(existing_number):]:
        if Path(row['migration']).name not in applied:
            continue
        missing = set(row['dependencies']) - applied
        if missing:
            raise ValueError('snapshot successor dependency absent: ' + ', '.join(sorted(missing)))
        selected = row
    return snapshot_selection(selected['number'], chain) if selected else {}


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('snapshot', 'successor-snapshot'):
        raise SystemExit('usage: registry_chain.py snapshot|successor-snapshot <number>')
    values = snapshot_selection(sys.argv[2]) if sys.argv[1] == 'snapshot' else successor_snapshot_selection(sys.stdin.read().splitlines(), sys.argv[2])
    for key, value in values.items():
        print(f'{key}={shlex.quote(value)}')
