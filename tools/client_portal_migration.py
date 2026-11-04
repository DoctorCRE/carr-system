"""Offline, private portal migration packages. Never publishes or writes CARR."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 2000
MAX_BYTES = 256 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()


def source_files(source):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError('source must be a regular directory')
    files, total = {}, 0
    for folder, dirs, names in os.walk(source, followlinks=False):
        for name in dirs + names:
            if (Path(folder) / name).is_symlink():
                raise ValueError('source symlink refused')
        for name in sorted(names):
            path = Path(folder) / name
            if not path.is_file():
                raise ValueError('source must contain regular files only')
            total += path.stat().st_size
            if len(files) >= MAX_FILES or total > MAX_BYTES:
                raise ValueError('source resource limit exceeded')
            files[path.relative_to(source).as_posix()] = path.read_bytes()
    return dict(sorted(files.items()))


class Views(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pages = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'site-view' in attrs.get('class', '').split() and attrs.get('id'):
            self.pages.append(attrs['id'])


def assigned_properties(data):
    match = re.fullmatch(r'\s*window\.[A-Z_]+\s*=\s*(\{.*\})\s*;?\s*', data.decode(), re.S)
    if not match:
        raise ValueError('unsupported data assignment; refusing script execution')
    return json.loads(match[1]).get('properties', [])


def inventory(source):
    files = source_files(source)
    views = Views()
    views.feed(files.get('index.html', b'').decode('utf-8'))
    properties = []
    if 'data.js' in files:
        properties = [{'id': p['id'], 'address': p['address']}
                      for p in assigned_properties(files['data.js'])]
    return {'pages': views.pages, 'properties': properties,
            'selections': None, 'selection_status': 'export_required',
            'files': [{'path': path, 'sha256': digest(content), 'bytes': len(content),
                       'classification': 'review_required'} for path, content in files.items()]}


def validate_study(study):
    script = """import {validateMarketStudyPackage} from './src/market-study-package.js';
let input=''; for await(const c of process.stdin) input+=c;
const r=validateMarketStudyPackage(JSON.parse(input));
process.exit(r.ok ? 0 : 1);"""
    result = subprocess.run(['node', '--input-type=module', '-e', script],
                            cwd=ROOT / 'mcp-server', input=encoded(study),
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError('market-study schema validation failed or validator unavailable')


def refuse_financial(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {'ownership', 'price_usd', 'asking_price', 'current_income_annual'}:
                raise ValueError('financial field refused')
            refuse_financial(value)
    elif isinstance(node, list):
        for value in node:
            refuse_financial(value)
    elif isinstance(node, str) and re.search(r'\$\s*\d|\bUSD\b', node):
        raise ValueError('financial figure refused')


def refuse_credentials(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if re.search(r'password|secret|token|cookie|authorization', key, re.I):
                raise ValueError('selection credentials refused')
            refuse_credentials(value)
    elif isinstance(node, list):
        for value in node:
            refuse_credentials(value)


def convert(source, mapping, output):
    if Path(source).is_symlink() or Path(output).is_symlink():
        raise ValueError('source and output symlinks refused')
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.resolve().is_relative_to(source) or source.is_relative_to(output.resolve()):
        raise ValueError('source and output must not overlap')
    files = source_files(source)
    if 'migration.json' not in files:
        raise ValueError('private migration descriptor required; raw bundle is inventory only')
    doc = json.loads(files.pop('migration.json'))
    if not isinstance(doc.get('selections'), list):
        raise ValueError('selection export required')
    refuse_credentials(doc['selections'])
    for row in doc['selections']:
        if (not isinstance(row, dict) or type(row.get('version')) is not int
                or row['version'] < 0 or not isinstance(row.get('selected_ids'), list)
                or not all(isinstance(x, str) for x in row['selected_ids'])
                or not isinstance(row.get('notes_by_property'), dict)):
            raise ValueError('invalid selection export')
    study = doc['study']
    if 'ownership' in study:
        raise ValueError('financial ownership section refused')
    refuse_financial(study['properties'])
    ids = [p['id'] for p in study['properties']]
    if 'data.js' in files and {p['id'] for p in assigned_properties(files['data.js'])} - set(ids):
        raise ValueError('source property omitted')
    unresolved = [slug for slug in ids if not mapping.get(slug)]
    if unresolved:
        raise ValueError('unresolved properties: ' + ', '.join(unresolved))
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate property slug')
    bindings = {slug: str(UUID(mapping[slug])) for slug in ids}
    if len(set(bindings.values())) != len(bindings):
        raise ValueError('duplicate canonical property binding')
    pages = doc['pages']
    if (not pages or len(set(pages)) != len(pages)
            or any(not isinstance(p, str) or not re.fullmatch(r'[a-z0-9-]+', p) for p in pages)):
        raise ValueError('invalid pages')
    views = Views()
    views.feed(files.get('index.html', b'').decode('utf-8'))
    if set(views.pages) - set(pages):
        raise ValueError('source page omitted')
    classifications = {}
    for row in doc['files']:
        path = row['path']
        if path in classifications or path not in files or row['classification'] not in {'content', 'financial'}:
            raise ValueError('invalid file classification')
        classifications[path] = row['classification']
    if set(classifications) != set(files):
        raise ValueError('every source file needs classification')
    validate_study(study)
    manifest, exclusions, assets = [], [], {}
    for path, data in files.items():
        row = {'path': path, 'sha256': digest(data)}
        if classifications[path] == 'financial':
            exclusions.append({**row, 'reason': 'financial'})
        else:
            assets['files/' + row['sha256']] = data
            manifest.append(row)
    if {row['sha256'] for row in exclusions} & {row['sha256'] for row in manifest}:
        raise ValueError('financial content also classified for inclusion')
    def check_assets(node):
        if isinstance(node, dict):
            if set(node) == {'sha256', 'name', 'type'} and 'files/' + node['sha256'] not in assets:
                raise ValueError('study asset missing or excluded')
            for value in node.values():
                check_assets(value)
        elif isinstance(node, list):
            for value in node:
                check_assets(value)
    check_assets(study)
    artifacts = {**assets, 'market-study.json': encoded(study),
                 'portal-parity-probes.json': (ROOT / 'client-portal/parity/probes.v1.json').read_bytes(),
                 'property-bindings.json': encoded(bindings),
                 'pages.json': encoded(pages), 'files.json': encoded(manifest),
                 'selections.json': encoded(doc['selections']),
                 'exclusions.json': encoded(exclusions)}
    probes = [{'path': path, 'sha256': digest(data)} for path, data in sorted(artifacts.items())]
    artifacts['parity-probes.json'] = encoded({'schema': 'carr.portal-migration-parity.v1', 'probes': probes})
    if output.exists():
        if source_files(output) != dict(sorted(artifacts.items())):
            raise ValueError('output drift; choose a new output directory')
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix='.portal-migration-', dir=output.parent))
    try:
        for path, data in artifacts.items():
            target = staged / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        os.rename(staged, output)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return output


def verify(package):
    files = source_files(package)
    doc = json.loads(files.pop('parity-probes.json'))
    if doc.get('schema') != 'carr.portal-migration-parity.v1':
        raise ValueError('parity schema mismatch')
    expected = {row['path']: row['sha256'] for row in doc['probes']}
    if expected != {path: digest(data) for path, data in files.items()}:
        raise ValueError('parity mismatch')
    validate_study(json.loads(files['market-study.json']))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inv = sub.add_parser('inventory')
    inv.add_argument('source', type=Path)
    inv.add_argument('output', type=Path)
    conv = sub.add_parser('convert')
    conv.add_argument('source', type=Path)
    conv.add_argument('mapping', type=Path)
    conv.add_argument('output', type=Path)
    check = sub.add_parser('verify')
    check.add_argument('package', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'inventory':
            result = inventory(args.source)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('xb') as stream:
                stream.write(encoded(result))
            print(json.dumps({key: len(result[key]) for key in ['pages', 'files', 'properties']}))
        elif args.command == 'convert':
            convert(args.source, json.loads(args.mapping.read_text()), args.output)
            print('package created; live proof remains pending')
        else:
            verify(args.package)
            print('local package parity passed')
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        parser.exit(2, 'migration refused; inspect private inputs and validation prerequisites\n')


if __name__ == '__main__':
    main()
