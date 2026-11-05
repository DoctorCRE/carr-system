#!/usr/bin/env python3
"""Validate proof admission, without executing or attesting product behavior."""
import argparse
from datetime import datetime
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = 'contracts/production-proof-plan.v1.json'


def read(path):
    if path.stat().st_size > 1048576:
        raise ValueError('input exceeds 1 MiB')
    return path.read_text()


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=15).strip()


def validate(value, schema, label='requirement'):
    types = {'object': dict, 'array': list, 'string': str, 'integer': int}
    kind = schema.get('type')
    if kind and (not isinstance(value, types[kind]) or kind == 'integer' and isinstance(value, bool)):
        raise ValueError(f'{label}: expected {kind}')
    if 'enum' in schema and not any(type(value) is type(v) and value == v for v in schema['enum']):
        raise ValueError(f'{label}: unregistered value')
    if kind == 'object':
        if set(schema['required']) - value.keys() or value.keys() - schema['properties'].keys():
            raise ValueError(f'{label}: missing or unknown fields')
        for key, child in value.items():
            validate(child, schema['properties'][key], label + '.' + key)
    if kind == 'array':
        if not schema['minItems'] <= len(value) <= schema['maxItems']:
            raise ValueError(f'{label}: array bounds')
        for item in value:
            validate(item, schema['items'], label)
    if kind == 'string':
        if len(value.strip()) < schema.get('minLength', 0) or value.strip().lower() in {'todo', 'tbd', 'placeholder'}:
            raise ValueError(f'{label}: empty or placeholder')
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            raise ValueError(f'{label}: invalid format')
    if kind == 'integer' and not schema['minimum'] <= value <= schema['maximum']:
        raise ValueError(f'{label}: budget bounds')


def product_paths(paths, contract):
    if not isinstance(paths, list) or not paths or len(paths) > 10000:
        raise ValueError('changed paths missing or exceed 10000')
    product = []
    for name in paths:
        if not isinstance(name, str) or not name or name.startswith('/') or '\\' in name or any(
                p in {'.', '..', ''} for p in name.split('/')):
            raise ValueError('invalid changed path')
        if not any(fnmatch.fnmatchcase(name, pattern) for pattern in contract['x-non-product-paths']):
            product.append(name)
    return product


def admission(body, paths, root, contract):
    product = product_paths(paths, contract)
    clean = re.sub(r'<!--.*?-->', '', body, flags=re.S)
    headings = list(re.finditer(r'^##\s+(.+?)\s*$', clean, re.M))
    matches = [i for i, h in enumerate(headings) if h[1].lower() == 'production proof']
    if len(matches) != 1:
        raise ValueError('exactly one Production proof section required')
    i = matches[0]
    section = clean[headings[i].end():headings[i+1].start() if i+1 < len(headings) else len(clean)].strip()
    if section.lower().startswith('n/a:'):
        reason = section[4:].strip()
        if product or len(reason) < 3 or reason.lower() in {'todo', 'tbd', 'placeholder'}:
            raise ValueError('n/a requires non-product paths and a real reason')
        return 'non-product declaration accepted (admission only)'
    match = re.fullmatch(r'`?(proofs/requirements/[A-Za-z0-9][A-Za-z0-9._-]*\.json)`?', section)
    if not match:
        raise ValueError('Production proof must name one proofs/requirements/<change>.json')
    root = root.resolve()
    path = root / match[1]
    if any(p.is_symlink() for p in [path, path.parent, path.parent.parent]) or not path.resolve().is_relative_to(root):
        raise ValueError('requirement path escape or symlink')
    requirement = json.loads(read(path))
    validate(requirement, contract)
    ids = [a['id'] for a in requirement['assertions']]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate assertion IDs')
    covered = set()
    for assertion in requirement['assertions']:
        runner = contract['x-runners'].get(assertion['runner'])
        if runner is None:
            raise ValueError('unregistered runner')
        covered.update(runner['targets'])
    required_targets = {target for target, patterns in contract['x-required-targets'].items()
                        if any(fnmatch.fnmatchcase(path, pattern) for path in product for pattern in patterns)}
    if required_targets - set(requirement['targets']):
        raise ValueError('changed runtime target missing from declaration')
    if set(requirement['targets']) - covered:
        raise ValueError('target has no registered runner')
    return 'proof declaration accepted (admission only; execution unproven)'


def activated(created, edited, activation):
    parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parse(created) >= parse(activation) or bool(edited and parse(edited) >= parse(activation))


def github_metadata(event):
    owner, name = event['repository']['full_name'].split('/')
    query = ('query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name)'
             '{pullRequest(number:$number){createdAt lastEditedAt body headRefOid}}}')
    result = json.loads(command('gh', 'api', 'graphql', '-f', 'query=' + query,
                               '-f', 'owner=' + owner, '-f', 'name=' + name,
                               '-F', 'number=' + str(event['pull_request']['number'])))
    pr = result['data']['repository']['pullRequest']
    if pr['body'] != (event['pull_request']['body'] or '') or pr['headRefOid'] != event['pull_request']['head']['sha']:
        raise ValueError('stale PR event; current body/head differs')
    return pr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--body-file', type=Path)
    parser.add_argument('--files-file', type=Path)
    parser.add_argument('--base', default='origin/main')
    args = parser.parse_args()
    try:
        contract = json.loads(read(ROOT / CONTRACT_PATH))
        if args.body_file:
            body = read(args.body_file)
            base = args.base
            head = 'HEAD'
        elif os.environ.get('GITHUB_EVENT_NAME') == 'pull_request':
            event = json.loads(read(Path(os.environ['GITHUB_EVENT_PATH'])))
            base = event['pull_request']['base']['sha']
            head = event['pull_request']['head']['sha']
            history = command('git', 'log', '--first-parent', '--diff-filter=A', '--format=%cI', base, '--', CONTRACT_PATH)
            if not history:
                print('Production proof: rollout not merged into base; admission not required')
                return 0
            metadata = github_metadata(event)
            if not activated(metadata['createdAt'], metadata['lastEditedAt'], history.splitlines()[-1]):
                print('Production proof: unchanged pre-rollout PR is grandfathered')
                return 0
            body = event['pull_request']['body'] or ''
        else:
            print('Production proof: PR admission not evaluated; local use --body-file and --base')
            return 0
        paths = json.loads(read(args.files_file)) if args.files_file else command(
            'git', 'diff', '--name-only', '--no-renames', '-z', base + '...' + head).rstrip('\0').split('\0')
        print('Production proof: ' + admission(body, paths, args.root, contract))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Production proof REFUSED: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        return 1


if __name__ == '__main__':
    sys.exit(main())
