#!/usr/bin/env python3
"""Derive disposable strict-tool lists from independent conversion admissions."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path, PurePath

ROOT = Path(__file__).resolve().parents[1]


def generate(pkg: Path) -> None:
    groups: dict[str, set[str]] = {name: set() for name in ('node', 'worker', 'entry')}
    for path in sorted((pkg / 'typescript/projects').glob('*.json')):
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or record.keys() - groups.keys():
            raise ValueError(f'{path}: expected node, worker and entry path arrays')
        for key, values in record.items():
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError(f'{path}: {key} must be a path array')
            for value in values:
                matches = pkg.glob(value) if key == 'entry' and '*' in value else [pkg / value]
                groups[key].update(str(path.relative_to(pkg)) for path in matches if path.is_file())
    projects = groups['node'] | groups['worker']
    if groups['entry'] - projects:
        raise ValueError('Knip entries must belong to a strict project')
    for name in ('node', 'worker'):
        options = {'allowJs': True, 'checkJs': False} if name == 'node' else {
            'module': 'ESNext', 'moduleResolution': 'Bundler', 'types': [], 'lib': ['esnext', 'dom']}
        config = {'extends': './tsconfig.base.json', 'compilerOptions': options, 'files': sorted(groups[name])}
        (pkg / f'tsconfig.{name}.json').write_text(json.dumps(config, indent=2) + '\n')
    baseline = pkg / 'fallow-baselines/dead-code.legacy.json'
    if baseline.exists():
        text = baseline.read_text()
        def renamed(match: re.Match[str]) -> str:
            old = match.group()
            new = str(Path(old).with_suffix('.ts'))
            return new if not (pkg / old).exists() and (pkg / new).is_file() else old
        text = re.sub(r'[A-Za-z0-9_./-]+\.(?:mjs|js)(?=[:|"\s])', renamed, text)
        (pkg / 'fallow-baselines/dead-code.json').write_text(text)
    graph = {'entry': sorted(groups['entry']), 'project': sorted(projects)}
    (pkg / 'knip.json').write_text(json.dumps(graph, indent=2) + '\n')


def admit(pkg: Path, ref: str, name: str) -> None:
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
        raise ValueError('admission name must contain only letters, digits, underscores and hyphens')
    def read(path: str):
        return json.loads(subprocess.check_output(
            ['git', 'show', f'{ref}:mcp-server/{path}'], cwd=pkg, text=True, timeout=30,
            env={key: value for key, value in os.environ.items() if not key.startswith('GIT_')}))
    record = {group: read(f'tsconfig.{group}.json')['files'] for group in ('node', 'worker')}
    entries = read('knip.json')['entry']
    record['entry'] = sorted({file for files in record.values() for file in files
                              if any(PurePath(file).match(pattern) for pattern in entries)})
    destination = pkg / 'typescript/projects' / f'{name}.json'
    for existing in (pkg / 'typescript/projects').glob('*.json'):
        if existing == destination:
            continue
        previous = json.loads(existing.read_text())
        for group in record:
            record[group] = sorted(set(record[group]) - set(previous.get(group, [])))
    destination.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pkg', type=Path, default=ROOT / 'mcp-server')
    parser.add_argument('--admit-from', help='preserve strict admissions from a pre-merge conversion head')
    parser.add_argument('--name', help='unique conversion PR or card name')
    args = parser.parse_args()
    if args.admit_from:
        if not args.name:
            parser.error('--admit-from requires --name')
        admit(args.pkg.resolve(), args.admit_from, args.name)
    generate(args.pkg.resolve())
