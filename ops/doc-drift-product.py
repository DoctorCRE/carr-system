#!/usr/bin/env python3
"""CARR dependency projection and public instruction-report artifact."""
import argparse
import copy
import json
from pathlib import Path


def verify_dependencies(root):
    authority = {line.split('#', 1)[0].strip() for line in (root / 'requirements.txt').read_text().splitlines()}
    projection = [line.split('#', 1)[0].strip() for line in
                  (root / 'scripts/doc-drift/requirements.txt').read_text().splitlines()]
    if any(line and line not in authority for line in projection):
        raise ValueError('instruction checker dependency projection differs from requirements.txt')


def public_report(report):
    public = copy.deepcopy(report)
    for collection in ('claims', 'unchecked'):
        public[collection] = [
            {key: claim[key] for key in ('file', 'line', 'kind', 'unchecked')}
            if claim.get('unchecked') else claim
            for claim in public.get(collection, [])
        ]
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.report:
        if not args.output or args.report.resolve() == args.output.resolve():
            parser.error('artifact publication requires a distinct output path')
        report = public_report(json.loads(args.report.read_text()))
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    else:
        verify_dependencies(args.root)


if __name__ == '__main__':
    main()
