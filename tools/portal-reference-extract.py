#!/usr/bin/env python3
"""Extract a trusted reference folder into a private market-study package."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / 'client-portal/parity/probes.v1.json'
SCHEMA = json.loads((ROOT / 'client-portal/market-study.v1.schema.json').read_text())
NODE = r'''
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { validateMarketStudyPackage, RECORD_LAYER_GROUPS } from './mcp-server/src/market-study-package.js';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
if (input.action === 'validate') {
  console.log(JSON.stringify({...validateMarketStudyPackage(input.package, {mode: input.mode}), groups: RECORD_LAYER_GROUPS}));
} else {
  const require = createRequire(new URL('./mcp-server/package.json', import.meta.url));
  const {parse} = require('@babel/parser');
  console.log(JSON.stringify(parse(input.text, {sourceType: 'unambiguous'}).program));
}
'''


class ExtractError(Exception):
    def __init__(self, errors):
        self.errors = errors


def fail(path, message):
    raise ExtractError([{'path': path, 'message': message}])


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def node_call(value):
    result = subprocess.run(['node', '--input-type=module', '-e', NODE], cwd=ROOT,
                            input=json.dumps(value), text=True, capture_output=True, timeout=60)
    if result.returncode:
        fail('/validator' if value['action'] == 'validate' else '/source',
             'pinned Node validator or syntax parser failed; source values suppressed')
    return json.loads(result.stdout)


def load_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail('/input', 'ambiguous duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def source_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in ('.', '..') for p in path.parts) or '\\' in relative:
        fail(relative, 'unsafe source path')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            fail(relative, 'symlink source path refused')
    if not current.is_file():
        fail(relative, 'source path is not a regular file')
    return current


def literal(node, path):
    if not node:
        fail(path, 'nonliteral binding')
    kind = node['type']
    if kind in ('StringLiteral', 'NumericLiteral', 'BooleanLiteral'):
        value = node['value']
        if kind == 'NumericLiteral' and (value is None or not math.isfinite(value)):
            fail(path, 'nonliteral finite number required')
        return value
    if kind == 'NullLiteral':
        return None
    if kind == 'UnaryExpression' and node['operator'] == '-' and node['argument']['type'] == 'NumericLiteral':
        return -node['argument']['value']
    if kind == 'ArrayExpression':
        return [literal(x, path + '/' + str(i)) for i, x in enumerate(node['elements'])]
    if kind == 'ObjectExpression':
        result = {}
        for prop in node['properties']:
            if prop['type'] != 'ObjectProperty' or prop.get('computed') or prop.get('shorthand'):
                fail(path, 'nonliteral object property')
            key = prop['key'].get('name', prop['key'].get('value'))
            if not isinstance(key, str) or key in result or key in ('__proto__', 'constructor', 'prototype'):
                fail(path, 'ambiguous or unsafe object key')
            result[key] = literal(prop['value'], path + '/' + key)
        return result
    fail(path, 'nonliteral expression refused')


def collect_bindings(program):
    result = {}
    def name(node):
        if node['type'] == 'Identifier':
            return node['name']
        if node['type'] == 'MemberExpression' and not node['computed']:
            parent, key = name(node['object']), name(node['property'])
            return parent + '.' + key if parent and key else None
        return None
    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            kind = node.get('type')
            key, value = None, None
            if kind == 'VariableDeclarator':
                key = name(node['id']) or '<destructured@' + str(node['start']) + '>'
                value = node.get('init')
            elif kind == 'AssignmentExpression':
                key = name(node['left']) or '<assignment@' + str(node['start']) + '>'
                value = node['right'] if node['operator'] == '=' else node
            if key:
                result.setdefault(key, []).append(value)
            for child in node.values():
                walk(child)
    walk(program)
    return result


def parts(pointer):
    if pointer == '':
        return []
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        fail('/mapping', 'expected JSON pointer')
    return [p.replace('~1', '/').replace('~0', '~') for p in pointer[1:].split('/')]


def select(value, pointer):
    for part in parts(pointer):
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            fail(pointer, 'missing source or destination field')
    return value


def index_segment(key, pointer):
    if not key.isascii() or not key.isdigit() or (len(key) > 1 and key[0] == '0'):
        fail(pointer, 'ambiguous array index')
    return int(key)


def schema_at(keys):
    schema = SCHEMA
    for key in [*keys, None]:
        while '$ref' in schema:
            schema = select(SCHEMA, schema['$ref'][1:])
        if key is None:
            return schema
        if schema.get('type') == 'array':
            schema = schema.get('items', {})
        else:
            schema = schema.get('properties', {}).get(key, schema.get('additionalProperties', {}))
        if not isinstance(schema, dict):
            schema = {}


def container(keys, next_key, pointer):
    kind = schema_at(keys).get('type')
    if kind == 'array':
        return []
    if kind == 'object' or not next_key.isdigit():
        return {}
    fail(pointer, 'ambiguous container; map its parent object or array explicitly')


def assign(package, pointer, value):
    keys = parts(pointer)
    if not keys:
        fail(pointer, 'destination must name a field')
    parent = package
    for position, key in enumerate(keys):
        last = position == len(keys) - 1
        if isinstance(parent, list):
            index = index_segment(key, pointer)
            if index > len(parent):
                fail(pointer, 'sparse array index')
            if index == len(parent):
                parent.append(copy.deepcopy(value) if last else container(keys[:position + 1], keys[position + 1], pointer))
            elif last:
                parent[index] = copy.deepcopy(value)
            if not last:
                parent = parent[index]
        elif isinstance(parent, dict):
            if last:
                parent[key] = copy.deepcopy(value)
            else:
                if key not in parent:
                    parent[key] = container(keys[:position + 1], keys[position + 1], pointer)
                parent = parent[key]
        else:
            fail(pointer, 'destination conflicts with existing value')


def destination_order(item):
    return [(0, int(key)) if key.isascii() and key.isdigit() else (1, key)
            for key in parts(item[0])]


def matches(value, keys):
    if not keys:
        return [value]
    key, *rest = keys
    if key == '*':
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
        return [v for child in children for v in matches(child, rest)]
    try:
        child = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return []
    return matches(child, rest)


def children(value, pointer=''):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from children(child, pointer + '/' + key.replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from children(child, pointer + '/' + str(index))
    else:
        yield pointer


def record_provenance(provenance, target, value, source):
    for previous in list(provenance):
        if previous == target or previous.startswith(target + '/'):
            del provenance[previous]
    for suffix in children(value):
        provenance[target + suffix] = {**source, **(
            {'pointer': source['pointer'] + suffix} if 'pointer' in source else {})}


def inventory_sources(package, row, provenance):
    result = {}
    if row['status'] == 'excluded':
        return result
    leaves = list(children(package))
    for pointer in row['fields']:
        if pointer in row['waived']:
            continue
        pattern = parts(pointer)
        entries = []
        for leaf in leaves:
            keys = parts(leaf)
            if len(keys) < len(pattern) or any(a != '*' and a != b for a, b in zip(pattern, keys)):
                continue
            if leaf not in provenance:
                fail(pointer, 'inventory field has no effective source provenance')
            entries.append({'target': leaf, **provenance[leaf]})
        if not entries:
            fail(pointer, 'inventory field has no effective source provenance')
        result[pointer] = entries
    return result


def inventory(package, site, mode, groups):
    rows, errors = [], []
    for probe in json.loads(PROBES.read_text())['probes']:
        if not probe['id'].startswith(site[-1] + '-') or probe['td'] == 'T':
            continue
        row = {'id': probe['id'], 'fields': probe.get('fields', [])}
        if probe.get('retired') or 'AD4' in probe.get('allowed', []):
            row.update(status='excluded', reason=probe.get('retired', 'AD4'))
        else:
            waived = []
            for pointer in row['fields']:
                group = parts(pointer)[0]
                if mode == 'partial' and group in groups and group not in package:
                    waived.append(pointer)
                elif not matches(package, parts(pointer)):
                    errors.append({'path': pointer, 'message': 'unmapped inventory field', 'row': probe['id']})
            row.update(status='waived' if len(waived) == len(row['fields']) else 'mapped', waived=waived)
            if not row['fields']:
                errors.append({'path': probe['id'], 'message': 'inventory row has no mapping'})
        rows.append(row)
    if errors:
        raise ExtractError(errors)
    return rows


def extract(source, output, mapping_name, mode):
    if not source.is_absolute() or not output.is_absolute():
        fail('/paths', 'source and output must be absolute paths')
    source = source.resolve(strict=True)
    output = output.absolute()
    if output.is_symlink():
        fail('/output', 'symlink output path refused')
    output = output.resolve()
    if output == ROOT or ROOT in output.parents or output == source or source in output.parents or output in source.parents:
        fail('/output', 'output must be outside repository and separate from source')
    if output.exists() or not output.parent.is_dir():
        fail('/output', 'output must be new with an existing parent directory')
    mapping = load_json(source_path(source, mapping_name).read_text())
    if mapping['site'] not in ('REF-A', 'REF-V'):
        fail('/site', 'expected REF-A or REF-V')
    sources, parsed, inputs, used = {}, {}, {}, set()
    for key, spec in mapping['sources'].items():
        file = spec['file']
        if file not in parsed:
            data = source_path(source, file).read_bytes()
            inputs[file] = digest(data)
            if file.endswith('.json'):
                parsed[file] = load_json(data)
            else:
                program = node_call({'action': 'parse', 'text': data.decode()})
                parsed[file] = {'program': program, 'bindings': collect_bindings(program)}
        if file.endswith('.json'):
            sources[key] = parsed[file]
        else:
            if 'ast_pointer' in spec:
                sources[key] = literal(select(parsed[file]['program'], spec['ast_pointer']), file + spec['ast_pointer'])
                continue
            binding = spec['binding']
            nodes = parsed[file]['bindings'].get(binding, [])
            if len(nodes) != 1:
                fail(file + ':' + binding, 'ambiguous or missing binding; count=' + str(len(nodes)))
            sources[key] = literal(nodes[0], file + ':' + binding)
            used.add((file, binding))
    errors = []
    referenced = {spec['source'] for spec in mapping['fields'].values() if spec is not None}
    for key, spec in mapping['sources'].items():
        if key not in referenced:
            errors.append({'path': spec['file'] + ':' + key, 'message': 'source has no destination mapping'})
    ignored = mapping.get('ignore', {})
    for file, bindings in parsed.items():
        if file.endswith('.json'):
            continue
        for binding in bindings['bindings']:
            if (file, binding) not in used and not ignored.get(file + ':' + binding, '').strip():
                errors.append({'path': file + ':' + binding, 'message': 'unmapped constant'})
    package, provenance = {}, {}
    for target, spec in sorted(mapping['fields'].items(), key=destination_order):
        if spec is None:
            continue
        try:
            value = select(sources[spec['source']], spec['pointer'])
            assign(package, target, value)
            record_provenance(provenance, target, value,
                              {**mapping['sources'][spec['source']], 'pointer': spec['pointer']})
        except ExtractError as exc:
            errors.extend({**error, 'source': mapping['sources'][spec['source']]['file'],
                           'target': target} for error in exc.errors)
    if errors:
        raise ExtractError(errors)
    assets, contents = [], {}
    for spec in mapping['assets']:
        target = spec['target']
        if mode == 'partial' and parts(target)[0] not in package:
            continue
        select(package, target)
        path = source_path(source, spec['file'])
        data = path.read_bytes()
        sha = digest(data)
        asset = {'sha256': sha, 'name': path.name, 'type': spec['type']}
        assign(package, target, asset)
        record_provenance(provenance, target, asset, {'file': spec['file'], 'asset_field': target})
        assets.append({'field': target, 'source': spec['file'], 'output': 'files/' + sha, **asset})
        contents[sha] = data
        inputs[spec['file']] = sha
    declared = {a['field'] for a in assets}
    def check_assets(value, pointer=''):
        if isinstance(value, dict):
            if {'sha256', 'name', 'type'} <= value.keys() and pointer not in declared:
                fail(pointer, 'asset has no source mapping')
            for key, child in value.items():
                check_assets(child, pointer + '/' + key)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                check_assets(child, pointer + '/' + str(i))
    check_assets(package)
    validation = node_call({'action': 'validate', 'package': package, 'mode': mode})
    if not validation['ok']:
        raise ExtractError(validation['errors'])
    rows = inventory(package, mapping['site'], mode, validation['groups'])
    for row in rows:
        row['sources'] = inventory_sources(package, row, provenance)

    counts = {'mapped_rows': sum(r['status'] == 'mapped' for r in rows),
              'waived_rows': sum(r['status'] == 'waived' for r in rows),
              'excluded_rows': sum(r['status'] == 'excluded' for r in rows),
              'assets': len(assets), 'files': len(contents), 'source_files': len(inputs)}
    manifest = {'schema': 'carr.reference-extraction.v1', 'site': mapping['site'], 'mode': mode,
                'counts': counts, 'assets': assets, 'rows': rows, 'inputs': inputs,
                'mapping_sha256': digest(source_path(source, mapping_name).read_bytes()),
                'validator': {p: digest((ROOT / p).read_bytes()) for p in (
                    'mcp-server/src/market-study-package.js', 'client-portal/market-study.v1.schema.json',
                    'mcp-server/package-lock.json', 'client-portal/parity/probes.v1.json')}}
    package_bytes, manifest_bytes = encode(package), encode(manifest)
    staging = Path(tempfile.mkdtemp(prefix='.reference-extract-', dir=output.parent))
    try:
        (staging / 'files').mkdir(mode=0o700)
        for name, data in [('market-study.json', package_bytes), ('manifest.json', manifest_bytes),
                           *[('files/' + sha, data) for sha, data in sorted(contents.items())]]:
            path = staging / name
            path.write_bytes(data)
            path.chmod(0o600)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'ok': True, 'counts': counts, 'package_sha256': digest(package_bytes), 'manifest_sha256': digest(manifest_bytes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mapping', default='extract.json', help='mapping path relative to trusted source')
    parser.add_argument('--mode', choices=('full', 'partial'), default='full')
    args = parser.parse_args()
    try:
        result = extract(args.source, args.output, args.mapping, args.mode)
    except ExtractError as exc:
        result = {'ok': False, 'error_count': len(exc.errors), 'errors': exc.errors}
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired, RecursionError, AttributeError, IndexError):
        result = {'ok': False, 'error_count': 1, 'errors': [{'path': '/input', 'message': 'invalid input or unavailable local dependency; values suppressed'}]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
