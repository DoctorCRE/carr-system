"""Validate synthetic portal migration snapshots and run the existing parity engine locally."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def safe_path(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_./-]+', value)
            and not value.startswith('/') and all(p not in ('', '.', '..') for p in value.split('/')), 'unsafe-path')


def selection(value):
    require(isinstance(value, dict) and set(value) == {'version', 'selections', 'notes_by_property', 'notes'}, 'selection-shape')
    require(type(value['version']) is int and value['version'] >= 1, 'selection-version')
    require(isinstance(value['selections'], list) and len(value['selections']) <= 100
            and all(isinstance(v, str) for v in value['selections'])
            and len(set(value['selections'])) == len(value['selections']), 'selection-ids')
    require(isinstance(value['notes_by_property'], dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in value['notes_by_property'].items())
            and isinstance(value['notes'], str), 'selection-notes')


def prepare(manifest):
    require(len(json.dumps(manifest).encode()) <= MAX_BYTES, 'manifest-size')
    require(isinstance(manifest, dict), 'manifest-shape')
    require(manifest.get('schema') == 'carr.portal-migration-rehearsal.v1' and manifest.get('synthetic') is True, 'synthetic-manifest')
    portals = manifest.get('portals')
    require(isinstance(portals, list) and len(portals) == 4, 'four-portals')
    slugs, deals, prepared = set(), set(), []
    for portal in portals:
        slug, deal = portal['slug'], portal['deal']
        require(re.fullmatch(r'synthetic-[a-z0-9-]+', slug), 'synthetic-slug')
        require(re.fullmatch(r'synthetic-deal-[a-z0-9-]+', deal), 'synthetic-deal')
        require(slug not in slugs, 'duplicate-slug')
        require(deal not in deals, 'duplicate-deal')
        require(portal['password'] == {'use_synthetic_slug': True}, 'synthetic-password')
        slugs.add(slug)
        deals.add(deal)
        require(portal['kind'] in ('package', 'legacy'), 'portal-kind')
        revision = portal['revision']
        require(type(revision) is int and revision > 0, 'publication-revision')
        files = portal['files']
        require(isinstance(files, list) and 0 < len(files) <= 32, 'file-limit')
        paths = set()
        for file in files:
            safe_path(file['path'])
            require(file['path'] not in paths and file['path'] != 'api/selection', 'duplicate-file')
            paths.add(file['path'])
            require(file['revision'] == revision, 'mixed-publication')
            require(isinstance(file['content'], str), 'file-content')
            require(hashlib.sha256(file['content'].encode()).hexdigest() == file['sha256'], 'file-hash')
        if portal['kind'] == 'legacy':
            require(digest(files) == portal['bundle_sha256'], 'bundle-hash')
        else:
            require(portal['package']['revision'] == revision, 'mixed-publication')
            require(portal['package']['fixture'] == 'example-health.market-study.json', 'synthetic-package')
            data = json.loads((ROOT / 'client-portal/fixtures' / portal['package']['fixture']).read_text())
            require(digest(data) == portal['package']['sha256'], 'package-hash')
        require(len(portal['aliases']) <= 32, 'alias-limit')
        aliases = set()
        for alias in portal['aliases']:
            safe_path(alias['path'])
            require(alias['portal'] == slug, 'cross-portal-alias')
            require(alias['revision'] == revision, 'mixed-publication')
            require(alias['target'] in paths, 'alias-target')
            require(alias['path'] not in paths | aliases and alias['path'] != 'api/selection', 'duplicate-alias')
            aliases.add(alias['path'])
        initial, frozen = portal['copy'], portal['freeze']
        selection(initial)
        selection(frozen)
        if initial != frozen:
            require(frozen['version'] > initial['version'], 'version-not-increased')
            require('delta' in portal, 'delta-required')
        if 'delta' in portal:
            delta = portal['delta']
            require(delta['from_version'] == initial['version'] and delta['selection'] == frozen, 'delta-mismatch')
        prepared.append(copy.deepcopy(portal))
        prepared[-1]['password'] = slug
        if portal['kind'] == 'package':
            prepared[-1]['package']['data'] = data
    require(sum(p['kind'] == 'package' for p in prepared) == 2, 'two-packages-two-bundles')
    return prepared


def rehearse(manifest):
    portals = prepare(manifest)
    receipt = {'schema': 'carr.portal-migration-receipt.v1', 'manifest_sha256': digest(manifest),
               'source_pin': '865fefee26fcbbe50a04c7515c5f3d3bc8913cd1',
               'status': 'pending-integration', 'cutover_ready': False,
               'integration': {'S9': 'pending', 'S10-S17': 'pending'}, 'production_proof': 'pending'}
    try:
        run = subprocess.run(['node', str(ROOT / 'mcp-server/portal-parity/migration-fixtures.mjs')],
                             input=json.dumps(portals), text=True, capture_output=True, timeout=180, check=False)
        require(run.returncode in (0, 3), run.stderr.strip() or 'candidate-process-failed')
        receipt['local'] = json.loads(run.stdout)
        require(run.returncode == 3 and receipt['local']['status'] == 'pending-integration', 'candidate-must-report-pending')
        require(receipt['local']['steps']['portal-registration']['status'] == 'PASS', 'portal-registration-failed')
        receipt['pending_steps'] = [name for name, result in receipt['local']['steps'].items()
                                    if result['status'] == 'pending']
    except (subprocess.SubprocessError, ValueError, OSError) as error:
        receipt['status'] = 'failed'
        receipt['local'] = {'status': 'FAIL', 'error': str(error) or type(error).__name__}
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    args = parser.parse_args()
    require(args.manifest.stat().st_size <= MAX_BYTES, 'manifest-size')
    receipt = rehearse(json.loads(args.manifest.read_text()))
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 1 if receipt['status'] == 'failed' else 3


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({'status': 'rejected', 'cutover_ready': False, 'error': str(error)}), file=sys.stderr)
        sys.exit(2)
